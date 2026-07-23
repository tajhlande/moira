"""Offline test fixtures for research loop evaluation (Phase 0).

These fixtures define concrete test data that the research loop must handle
correctly. They are designed to validate fact-level review (research_review)
and conclusion-level evaluation (evaluation) behavior.

Three fixtures:
1. Review stress - planted mistakes in type, ability, moves, legality, synergy
2. Tool routing - decomposed facts matched against a fixed tool catalog
3. Synthesis trap - individually true facts that tempt an unjustified conclusion

See agent-docs/research-loop-overhaul-plan.md Phase 0 for context.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from moira.config import MoiraConfig
from moira.inference.client import ChatResponse, InferenceClient
from moira.inference.registry import ModelRegistry, ResolvedModel
from moira.models.knowledge import Conclusion, Fact, ResearchState
from moira.service_setup import _services
from moira.tools.base import ToolDefinition


@pytest.fixture
def config():
    return MoiraConfig()


@pytest.fixture
def mock_writer():
    events = []

    def write(event):
        events.append(event)

    with (
        patch("moira.workflow.nodes.research_review.get_stream_writer", return_value=write),
        patch("moira.workflow.nodes.evaluation.get_stream_writer", return_value=write),
        patch("moira.workflow.nodes.synthesis.get_stream_writer", return_value=write),
        patch("moira.workflow.nodes.decomposition.get_stream_writer", return_value=write),
        patch("moira.workflow.nodes.planning.get_stream_writer", return_value=write),
        patch("moira.workflow.nodes.research.get_stream_writer", return_value=write),
        patch("moira.workflow.nodes.report_generation.get_stream_writer", return_value=write),
        patch("moira.workflow.nodes.tool_identification.get_stream_writer", return_value=write),
        patch("moira.workflow.nodes._helpers_deps.get_stream_writer", return_value=write),
    ):
        yield events


@pytest.fixture
def mock_model():
    client = AsyncMock(spec=InferenceClient)
    client.chat_completion = AsyncMock(return_value=ChatResponse(content="test response"))
    resolved = ResolvedModel(model_id="test-model", client=client)
    registry = MagicMock(spec=ModelRegistry)
    registry.resolve = AsyncMock(return_value=resolved)
    return {"client": client, "resolved": resolved, "registry": registry}


def _inject_services(config, mock_model):
    _services.clear()
    _services["config"] = config
    _services["model_registry"] = mock_model["registry"]


def _make_run_config(config):
    return {"configurable": {"moira_config": config}}


def _build_state(config, question: str, facts: list[Fact] | None = None) -> ResearchState:
    cw = config.budget.cost_weights
    step_costs = {
        "decomposition": cw.decomposition,
        "tool_identification": cw.tool_identification,
        "planning": cw.planning,
        "research": cw.research,
        "synthesis": cw.synthesis,
        "research_review": cw.research_review,
        "evaluation": cw.evaluation,
        "report_generation": cw.report_generation,
    }
    return {
        "knowledge": {
            "question": question,
            "user_goal": "",
            "topic": "",
            "entities": [],
            "concepts": [],
            "facts": facts or [],
            "conclusions": [],
            "citations": [],
            "review_history": [],
            "evaluation_history": [],
        },
        "execution_state": {
            "candidate_tools": [],
            "evidence_requests": [],
            "budget_remaining": float(config.budget.default_limit),
            "budget_limit": float(config.budget.default_limit),
            "step_costs": step_costs,
            "tool_costs": {},
            "tool_call_counts": {},
            "total_tool_cost_consumed": 0.0,
            "error": "",
            "research_retry_count": 0,
            "research_count": 0,
            "review_count": 0,
            "evaluation_count": 0,
            "retry_limits": {"max_review": 3, "max_evaluation": 2},
        },
    }


# ---------------------------------------------------------------------------
# Fixture 1: Review Stress
# ---------------------------------------------------------------------------
# A short Tyranitar-partner draft with planted mistakes in type matchups,
# abilities, typical moves, OU legality, and synergy reasoning.
# The research_review node should catch fact-level errors, and the
# evaluation node should catch conclusion-level errors.

VERIFICATION_STRESS_DRAFT = """\
Tyranitar Partners in Gen9 OU

Tyranitar is a Rock/Dark-type Pokemon in Gen9 OU. Its Sand Stream ability sets a
sandstorm for 8 turns, boosting its Special Defense by 1.5x.

Key weaknesses: Fighting (x4), Ground (x4), Water, Grass, Bug, Steel, Fairy.

Recommended Partners:

1. Corviknight - Excellent partner. Corviknight is immune to Ground-type attacks,
   which covers Tyranitar's x4 Ground weakness. It also resists Fairy and Fighting.
   Typical Tyranitar moves include Fire Blast, Draco Meteor, Stone Edge, and
   Tera Blast (very common in Gen9 OU Tyranitar). Corviknight can defog away
   hazards and provide slow U-turn pivoting.

2. Amoonguss - Regenerator pivot that resists Water and Grass. Sleep Powder
   provides utility. Tyranitar is generally considered S-rank in Gen9 OU viability.

Synergy Summary: Corviknight's Ground immunity perfectly covers Tyranitar's
biggest weakness, making it the top partner choice.
"""


class TestReviewStressFixture:
    """Research review and evaluation should catch planted errors.

    These tests verify that research_review catches fact-level errors
    and evaluation catches conclusion-level errors in the stress-test draft.
    """

    @pytest.fixture
    def stress_state(self, config) -> ResearchState:
        facts = [
            Fact(
                id="f001",
                subject="Tyranitar",
                fact_needed="Tyranitar typing",
                claim="Rock/Dark type",
                status="unverified",
            ),
            Fact(
                id="f002",
                subject="Tyranitar",
                fact_needed="Tyranitar abilities",
                claim="Sand Stream, sets sandstorm for 8 turns",
                status="unverified",
            ),
            Fact(
                id="f003",
                subject="Tyranitar",
                fact_needed="Tyranitar weaknesses",
                claim="Fighting x4, Ground x4, Water, Grass, Bug, Steel, Fairy",
                status="unverified",
            ),
            Fact(
                id="f004",
                subject="Tyranitar",
                fact_needed="Tyranitar typical moves",
                claim="Fire Blast, Draco Meteor, Stone Edge, Tera Blast",
                status="unverified",
            ),
            Fact(
                id="f005",
                subject="Corviknight",
                fact_needed="Corviknight abilities",
                claim="Immune to Ground",
                status="unverified",
            ),
        ]
        question = "What Pokemon synergize well with Tyranitar in Gen9 OU?"
        state = _build_state(config, question, facts)
        state["knowledge"]["user_goal"] = "Find synergistic partners for Tyranitar in Gen9 OU"
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="web_search", description="Search the web"),
            ToolDefinition(name="pokeapi", description="Pokemon data API"),
        ]
        return state

    async def test_review_flags_ground_weakness_error(
        self, config, mock_writer, mock_model, stress_state
    ):
        """Research review should catch that Ground is not x4 on Tyranitar."""
        _inject_services(config, mock_model)

        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps(
                {
                    "fact_results": [
                        {
                            "fact_id": "f003",
                            "result": "contradicted",
                            "evidence": "Ground is x2, not x4",
                        },
                    ],
                    "coverage_assessment": "Ground weakness claim is wrong",
                    "missing_areas": [],
                    "route": "retry",
                }
            )
        )

        from moira.workflow.nodes.research_review import research_review

        result = await research_review(stress_state, _make_run_config(config))

        all_text = json.dumps(result["knowledge"]["review_history"]).lower()
        assert "ground" in all_text, (
            f"Research review should flag the Ground x4 error. Got: {all_text[:300]}"
        )

    async def test_review_flags_sand_stream_duration_error(
        self, config, mock_writer, mock_model, stress_state
    ):
        """Research review should catch that Sand Stream lasts 5 turns, not 8."""
        _inject_services(config, mock_model)

        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content=json.dumps(
                    {
                        "fact_results": [
                            {
                                "fact_id": "f002",
                                "result": "contradicted",
                                "evidence": "Sand Stream lasts 5 turns, not 8",
                            },
                        ],
                        "coverage_assessment": "Sand Stream duration wrong",
                        "missing_areas": [],
                        "route": "retry",
                    }
                )
            ),
        ]

        from moira.workflow.nodes.research_review import research_review

        result = await research_review(stress_state, _make_run_config(config))

        all_text = json.dumps(result["knowledge"]["review_history"]).lower()
        assert "sand" in all_text or "5 turn" in all_text, (
            f"Research review should flag the Sand Stream duration error. Got: {all_text[:300]}"
        )

    async def test_review_flags_draco_meteor_impossibility(
        self, config, mock_writer, mock_model, stress_state
    ):
        """Research review should catch that Tyranitar cannot learn Draco Meteor."""
        _inject_services(config, mock_model)

        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content=json.dumps(
                    {
                        "fact_results": [
                            {
                                "fact_id": "f004",
                                "result": "contradicted",
                                "evidence": "Draco Meteor not in learnset",
                            },
                        ],
                        "coverage_assessment": "Draco Meteor is impossible",
                        "missing_areas": [],
                        "route": "retry",
                    }
                )
            ),
        ]

        from moira.workflow.nodes.research_review import research_review

        result = await research_review(stress_state, _make_run_config(config))

        all_text = json.dumps(result["knowledge"]["review_history"]).lower()
        assert "draco" in all_text or "learnset" in all_text, (
            f"Research review should flag Draco Meteor as impossible. Got: {all_text[:300]}"
        )

    async def test_review_flags_corviknight_ground_immunity_error(
        self, config, mock_writer, mock_model, stress_state
    ):
        """Research review should catch that Corviknight is NOT immune to Ground."""
        _inject_services(config, mock_model)

        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content=json.dumps(
                    {
                        "fact_results": [
                            {
                                "fact_id": "f005",
                                "result": "contradicted",
                                "evidence": "Corviknight has no Levitate",
                            },
                        ],
                        "coverage_assessment": "Corviknight Ground immunity wrong",
                        "missing_areas": [],
                        "route": "retry",
                    }
                )
            ),
        ]

        from moira.workflow.nodes.research_review import research_review

        result = await research_review(stress_state, _make_run_config(config))

        all_text = json.dumps(result["knowledge"]["review_history"]).lower()
        assert "corviknight" in all_text and ("ground" in all_text or "immune" in all_text), (
            "Research review should flag the Corviknight Ground immunity error. "
            f"Got: {all_text[:300]}"
        )

    async def test_evaluation_flags_goal_not_met(
        self, config, mock_writer, mock_model, stress_state
    ):
        """Evaluation should route retry when conclusions are unsupported."""
        _inject_services(config, mock_model)

        stress_state["knowledge"]["conclusions"] = [
            {
                "id": "c001",
                "conclusion": "Corviknight is the best partner",
                "supporting_fact_ids": ["f005"],
                "reasoning": "Ground immunity covers Tyranitar",
                "status": "unverified",
            },
        ]

        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content=json.dumps(
                    {
                        "conclusion_results": [
                            {
                                "conclusion_id": "c001",
                                "result": "contradicted",
                                "reason": "Corviknight is not immune to Ground",
                            },
                        ],
                        "goal_met": False,
                        "goal_assessment": "Central synergy claim is based on error",
                        "route": "retry",
                    }
                )
            ),
        ]

        from moira.workflow.nodes.evaluation import evaluation

        result = await evaluation(stress_state, _make_run_config(config))

        all_text = json.dumps(result["knowledge"]["evaluation_history"]).lower()
        assert "retry" in all_text, (
            "Evaluation should route retry when conclusions are contradicted. "
            f"Got: {all_text[:300]}"
        )


# ---------------------------------------------------------------------------
# Fixture 2: Tool Routing
# ---------------------------------------------------------------------------
# Decomposed facts from the canary question, run against a fixed tool catalog.
# The system should rank Pokemon-specific tools ahead of web_search for
# structured facts.

CANARY_DECOMPOSED_FACTS = [
    {"fact_needed": "Tyranitar typing", "subject": "Tyranitar"},
    {"fact_needed": "Tyranitar abilities including hidden ability", "subject": "Tyranitar"},
    {"fact_needed": "Tyranitar weaknesses and resistances", "subject": "Tyranitar"},
    {"fact_needed": "Tyranitar typical moves in competitive play", "subject": "Tyranitar"},
    {"fact_needed": "Gen9 OU legality rules", "subject": "Gen9 OU"},
    {"fact_needed": "OU-legal Pokemon that resist Fighting type", "subject": "Gen9 OU"},
    {"fact_needed": "OU-legal Pokemon that can set up entry hazards", "subject": "Gen9 OU"},
    {"fact_needed": "Tyranitar common teammate statistics", "subject": "Tyranitar"},
]

TOOL_CATALOG = [
    ToolDefinition(
        name="pokeapi",
        description="Pokemon species data API. Returns typing, abilities, base stats, "
        "learnsets, and evolution chains for individual Pokemon species.",
        tags=["pokemon", "species", "type", "ability", "stats", "moves"],
    ),
    ToolDefinition(
        name="pokemon_db",
        description="Competitive Pokemon database. Provides tier listings, usage "
        "statistics, sample movesets, and viability rankings.",
        tags=["pokemon", "competitive", "tier", "usage", "moveset", "OU"],
    ),
    ToolDefinition(
        name="pokemon_showdown",
        description="Battle simulator and team data. Provides damage calculations, "
        "type matchup tables, and usage stats from competitive ladders.",
        tags=["pokemon", "battle", "damage-calc", "type-chart", "usage"],
    ),
    ToolDefinition(
        name="web_search",
        description="Generic web search via SearXNG. Searches the open web for "
        "any topic. Use when specialized tools don't cover the needed information.",
        tags=["search", "web", "general"],
    ),
    ToolDefinition(
        name="url_content",
        description="Fetches and extracts text content from a URL. Useful for "
        "reading specific web pages found via search.",
        tags=["url", "content", "fetch"],
    ),
    ToolDefinition(
        name="calculator",
        description="Evaluates mathematical expressions. Useful for damage "
        "calculations and stat computations.",
        tags=["math", "calculator"],
        is_default=True,
    ),
    ToolDefinition(
        name="datetime",
        description="Returns current date and time.",
        tags=["time", "date"],
        is_default=True,
    ),
]

EXPECTED_TOOL_ROUTING = {
    "Tyranitar typing": "pokeapi",
    "Tyranitar abilities": "pokeapi",
    "Tyranitar weaknesses": "pokeapi",
    "Tyranitar typical moves": "pokemon_db",
    "Gen9 OU legality": "pokemon_db",
    "OU-legal Pokemon that resist Fighting": "pokemon_showdown",
    "OU-legal Pokemon entry hazards": "pokemon_db",
    "Tyranitar common teammate": "pokemon_db",
}


class TestToolRoutingFixture:
    """Tool routing should prefer specialized tools over web_search for
    structured facts.

    These tests verify that the tool discovery/selection process ranks
    domain-specific tools ahead of generic search.
    """

    def test_pokeapi_ranked_above_web_search_for_typing(self):
        pokeapi_desc = TOOL_CATALOG[0].description
        web_search_desc = TOOL_CATALOG[3].description
        assert "typing" in pokeapi_desc.lower()
        assert "typing" not in web_search_desc.lower()

    def test_pokemon_db_ranked_above_web_search_for_ou_legality(self):
        pokemon_db_desc = TOOL_CATALOG[1].description
        pokemon_db_tags = TOOL_CATALOG[1].tags
        web_search_desc = TOOL_CATALOG[3].description
        assert "tier" in pokemon_db_desc.lower()
        assert any(t.lower() == "ou" for t in pokemon_db_tags), (
            f"pokemon_db tags should contain 'ou': {pokemon_db_tags}"
        )
        assert "legality" not in web_search_desc.lower()

    def test_catalog_structure_matches_routing_expectations(self):
        tool_names = [t.name for t in TOOL_CATALOG]
        assert "pokeapi" in tool_names
        assert "pokemon_db" in tool_names
        assert "pokemon_showdown" in tool_names
        assert "web_search" in tool_names

        default_tools = [t for t in TOOL_CATALOG if t.is_default]
        assert len(default_tools) == 2
        default_names = {t.name for t in default_tools}
        assert "calculator" in default_names
        assert "datetime" in default_names

    def test_decomposed_facts_cover_key_areas(self):
        facts = CANARY_DECOMPOSED_FACTS
        fact_texts = [f["fact_needed"].lower() for f in facts]
        assert any("typing" in f for f in fact_texts)
        assert any("abilit" in f for f in fact_texts)
        assert any("weakness" in f for f in fact_texts)
        assert any("move" in f for f in fact_texts)
        assert any("legal" in f for f in fact_texts)

    async def test_tool_discovery_ranks_specialized_first(self, config, mock_writer, mock_model):
        _inject_services(config, mock_model)

        from moira.workflow.nodes.tool_identification import tool_identification

        pokemon_tools = [t for t in TOOL_CATALOG if not t.is_default]
        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(return_value=pokemon_tools[:3])
        _services["tool_discovery"] = mock_discovery

        default_tools = [t for t in TOOL_CATALOG if t.is_default]
        mock_catalog = MagicMock()
        mock_catalog.get_default_tools.return_value = default_tools
        _services["tool_catalog"] = mock_catalog

        state = _build_state(config, "What Pokemon synergize with Tyranitar in Gen9 OU?")
        facts = [
            Fact(id="f001", subject="Tyranitar", fact_needed="Tyranitar typing", status="unknown"),
        ]
        state["knowledge"]["facts"] = facts

        result = await tool_identification(state, _make_run_config(config))

        discovered_names = [t.name for t in result["execution_state"]["candidate_tools"]]
        assert "pokeapi" in discovered_names or "pokemon_db" in discovered_names, (
            f"Expected Pokemon tools in discovery results, got: {discovered_names}"
        )


# ---------------------------------------------------------------------------
# Fixture 3: Synthesis Trap
# ---------------------------------------------------------------------------
# A fixed fact bundle containing individually true facts that tempt an
# unjustified conclusion. The system should qualify the claim or refuse
# to overstate it.

SYNTHESIS_TRAP_EXPECTED_BEHAVIORS = [
    "Should NOT claim Corviknight is the best partner without usage data",
    "Should NOT claim Corviknight makes Tyranitar viable without battle data",
    "Should qualify that Corviknight takes neutral from Fighting (not a full resist)",
    "Should acknowledge limited evidence: no usage stats, no matchup data provided",
    "Should note that other partners (like Tinkaton) also cover weaknesses",
]


class TestSynthesisTrapFixture:
    """Synthesis passes through model output without filtering overclaims.

    Overclaim detection is the evaluator's job, not synthesis's.  The test
    below confirms that a well-hedged conclusion passes through unchanged.
    Overclaim detection is tested in ``TestEvaluationOverclaimFixture``.
    """

    @pytest.fixture
    def trap_state(self, config) -> ResearchState:
        facts = [
            Fact(
                id="f001",
                subject="Tyranitar",
                fact_needed="Tyranitar typing",
                claim="Tyranitar is Rock/Dark type",
                status="unverified",
            ),
            Fact(
                id="f002",
                subject="Tyranitar",
                fact_needed="Tyranitar weaknesses",
                claim="Weak to Fighting (x4), Water, Grass, Bug, Steel, Fairy, Ground (x2)",
                status="unverified",
            ),
            Fact(
                id="f003",
                subject="Tyranitar",
                fact_needed="Tyranitar ability",
                claim="Sand Stream sets sandstorm for 5 turns",
                status="unverified",
            ),
            Fact(
                id="f004",
                subject="Corviknight",
                fact_needed="Corviknight typing",
                claim="Corviknight is Steel/Flying type",
                status="unverified",
            ),
            Fact(
                id="f005",
                subject="Corviknight",
                fact_needed="Corviknight matchups",
                claim="Resists Fairy (x0.5), takes neutral from Fighting, "
                "immune to Ground (Flying type)",
                status="unverified",
            ),
            Fact(
                id="f006",
                subject="Corviknight",
                fact_needed="Corviknight abilities",
                claim="Pressure or Defiant (hidden)",
                status="unverified",
            ),
            Fact(
                id="f007",
                subject="Corviknight",
                fact_needed="Corviknight moves",
                claim="Roost, Brave Bird, U-turn, Iron Head, Body Press",
                status="unverified",
            ),
            Fact(
                id="f008",
                subject="Tinkaton",
                fact_needed="Tinkaton typing",
                claim="Tinkaton is Fairy/Steel type",
                status="unverified",
            ),
            Fact(
                id="f009",
                subject="Tinkaton",
                fact_needed="Tinkaton resistances",
                claim="Resists Dark (x0.5), Rock (x0.5), immune to Dragon",
                status="unverified",
            ),
        ]
        question = "What Pokemon synergize well with Tyranitar in Gen9 OU?"
        state = _build_state(config, question, facts)
        state["knowledge"]["user_goal"] = "Find synergistic partners for Tyranitar in Gen9 OU"
        state["execution_state"]["candidate_tools"] = [
            ToolDefinition(name="pokeapi", description="Pokemon data API"),
        ]
        return state

    async def test_synthesis_acknowledges_limited_evidence(
        self, config, mock_writer, mock_model, trap_state
    ):
        """Synthesis should acknowledge that no usage data or matchup data was
        provided in the facts."""
        _inject_services(config, mock_model)

        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps(
                {
                    "conclusions": [
                        {
                            "conclusion": (
                                "Corviknight provides good defensive synergy "
                                "based on type matchups. However, without usage "
                                "statistics, a definitive ranking cannot be "
                                "established."
                            ),
                            "supporting_fact_ids": ["f004", "f005"],
                            "reasoning": "Type coverage analysis with hedging for missing data",
                        }
                    ]
                }
            )
        )

        from moira.workflow.nodes.synthesis import synthesis

        result = await synthesis(trap_state, _make_run_config(config))

        conclusions_text = json.dumps(result["knowledge"]["conclusions"]).lower()
        has_qualification = any(
            word in conclusions_text
            for word in ["however", "without", "limited", "based on", "available", "suggests"]
        )
        assert has_qualification, (
            "Synthesis should hedge given limited evidence. Got: " + conclusions_text[:200]
        )


# ---------------------------------------------------------------------------
# Fixture 3b: Evaluation Overclaim Detection
# ---------------------------------------------------------------------------
# The synthesis trap fixture above provides individually true facts that tempt
# unjustified conclusions.  The tests below verify that the evaluation node
# catches overclaims when the evaluator model flags them — replacing the old
# xfail tests that incorrectly targeted synthesis as the validator.


class TestEvaluationOverclaimFixture:
    """Evaluation should detect overclaims and mark them unsupported.

    These tests replace the former xfail synthesis tests.  Synthesis is the
    generator — it passes through whatever the model emits.  Evaluation is the
    validator.  Each test mocks synthesis to emit an overclaim, then runs
    evaluation with a mock verdict of ``unsupported`` and asserts the node
    correctly applies that status.
    """

    @pytest.fixture
    def overclaim_state(self, config) -> ResearchState:
        """State with individually true facts and an overclaim conclusion,
        ready to be passed to the evaluation node."""
        facts = [
            Fact(
                id="f001",
                subject="Tyranitar",
                fact_needed="Tyranitar typing",
                claim="Tyranitar is Rock/Dark type",
                status="verified",
            ),
            Fact(
                id="f002",
                subject="Tyranitar",
                fact_needed="Tyranitar weaknesses",
                claim="Weak to Fighting (x4), Water, Grass, Bug, Steel, Fairy, Ground (x2)",
                status="verified",
            ),
            Fact(
                id="f004",
                subject="Corviknight",
                fact_needed="Corviknight typing",
                claim="Corviknight is Steel/Flying type",
                status="verified",
            ),
            Fact(
                id="f005",
                subject="Corviknight",
                fact_needed="Corviknight matchups",
                claim="Resists Fairy (x0.5), takes neutral from Fighting, "
                "immune to Ground (Flying type)",
                status="verified",
            ),
        ]
        question = "What Pokemon synergize well with Tyranitar in Gen9 OU?"
        state = _build_state(config, question, facts)
        state["knowledge"]["user_goal"] = "Find synergistic partners for Tyranitar in Gen9 OU"
        state["knowledge"]["conclusions"] = [
            Conclusion(
                id="c001",
                conclusion="Corviknight is the best partner for Tyranitar in Gen9 OU",
                supporting_fact_ids=["f004", "f005"],
                reasoning="Covers all weaknesses",
                status="unverified",
            ),
        ]
        state["knowledge"]["citations"] = [
            {
                "id": "cit001",
                "source": "web_search",
                "url": "https://example.com/corviknight",
                "title": "Corviknight Analysis",
                "excerpt": "Corviknight is Steel/Flying type.",
                "content": "Corviknight is a Steel/Flying type Pokemon. "
                "It resists Fairy and is immune to Ground. "
                "It takes neutral damage from Fighting.",
            },
        ]
        return state

    async def test_evaluation_marks_best_partner_claim_unsupported(
        self, config, mock_writer, mock_model, overclaim_state
    ):
        """When evaluation detects an overclaim ('best partner' with no usage
        data), it should mark the conclusion ``unsupported``."""
        _inject_services(config, mock_model)

        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps(
                {
                    "conclusion_results": [
                        {
                            "conclusion_id": "c001",
                            "result": "unsupported",
                            "reason": (
                                "The facts establish type matchups but contain "
                                "no usage statistics, viability ranking, or "
                                "comparative data to support 'best partner'."
                            ),
                        }
                    ],
                    "goal_met": False,
                    "goal_assessment": (
                        "The sole conclusion is an overclaim — no verified "
                        "conclusions remain to answer the question."
                    ),
                    "route": "retry",
                }
            )
        )

        from moira.workflow.nodes.evaluation import evaluation

        result = await evaluation(overclaim_state, _make_run_config(config))

        conclusions = result["knowledge"]["conclusions"]
        assert conclusions[0]["status"] == "unsupported"
        assert "best partner" not in conclusions[0]["status"]

    async def test_evaluation_marks_fighting_resist_claim_unsupported(
        self, config, mock_writer, mock_model, overclaim_state
    ):
        """When evaluation detects a factual error (claiming Fighting resist
        when the source says neutral), it should mark the conclusion
        ``unsupported`` rather than ``contradicted`` (no active refutation,
        just a grounding failure)."""
        _inject_services(config, mock_model)

        # Override the conclusion with the fighting-resist overclaim
        overclaim_state["knowledge"]["conclusions"] = [
            Conclusion(
                id="c001",
                conclusion="Corviknight resists all of Tyranitar's weaknesses including Fighting",
                supporting_fact_ids=["f004", "f005"],
                reasoning="Steel resists Fairy and Fighting",
                status="unverified",
            ),
        ]

        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps(
                {
                    "conclusion_results": [
                        {
                            "conclusion_id": "c001",
                            "result": "unsupported",
                            "reason": (
                                "The source states Corviknight takes neutral from "
                                "Fighting, not resisted. The conclusion overstates "
                                "the type interaction."
                            ),
                        }
                    ],
                    "goal_met": False,
                    "goal_assessment": "Conclusion misrepresents the Fighting interaction.",
                    "route": "retry",
                }
            )
        )

        from moira.workflow.nodes.evaluation import evaluation

        result = await evaluation(overclaim_state, _make_run_config(config))

        conclusions = result["knowledge"]["conclusions"]
        assert conclusions[0]["status"] == "unsupported"


# ---------------------------------------------------------------------------
# Fixture 3c: Structural Sanity Check
# ---------------------------------------------------------------------------
# Verifies that research_review catches hallucinated fact references before
# evaluation runs, and that evaluation preserves the terminal "unsupported"
# verdict instead of overwriting it.


class TestStructuralSanityCheck:
    """Research review should structurally flag conclusions that reference
    non-existent fact IDs, and evaluation should not overwrite that verdict."""

    @pytest.fixture
    def hallucination_state(self, config) -> ResearchState:
        facts = [
            Fact(
                id="f001",
                subject="Tyranitar",
                fact_needed="Tyranitar typing",
                claim="Rock/Dark type",
                status="verified",
            ),
        ]
        state = _build_state(config, "Test question", facts)
        state["knowledge"]["user_goal"] = "Test goal"
        state["knowledge"]["conclusions"] = [
            Conclusion(
                id="c001",
                conclusion="Tyranitar is a strong pick",
                supporting_fact_ids=["f001", "f999"],
                reasoning="Based on typing",
                status="unverified",
            ),
        ]
        return state

    async def test_review_marks_hallucinated_fact_id_unsupported(
        self, config, mock_writer, mock_model, hallucination_state
    ):
        """research_review should mark a conclusion ``unsupported`` when its
        supporting_fact_ids reference a fact that does not exist."""
        _inject_services(config, mock_model)

        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps(
                {
                    "fact_results": [
                        {
                            "fact_id": "f001",
                            "result": "verified",
                            "evidence": "Tyranitar is confirmed Rock/Dark",
                        }
                    ],
                    "coverage_assessment": "OK",
                    "missing_areas": [],
                    "route": "continue",
                }
            )
        )

        from moira.workflow.nodes.research_review import research_review

        result = await research_review(hallucination_state, _make_run_config(config))

        conclusions = result["knowledge"]["conclusions"]
        assert conclusions[0]["status"] == "unsupported"

    async def test_evaluation_preserves_structural_unsupported(
        self, config, mock_writer, mock_model, hallucination_state
    ):
        """Evaluation should NOT overwrite a conclusion already marked
        ``unsupported`` by the structural check in research_review."""
        _inject_services(config, mock_model)

        # Pre-mark the conclusion as unsupported (as research_review would)
        hallucination_state["knowledge"]["conclusions"][0]["status"] = "unsupported"

        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=json.dumps(
                {
                    "conclusion_results": [
                        {
                            "conclusion_id": "c001",
                            "result": "verified",
                            "reason": "Looks fine to me",
                        }
                    ],
                    "goal_met": True,
                    "goal_assessment": "Goal met",
                    "route": "accept",
                }
            )
        )

        from moira.workflow.nodes.evaluation import evaluation

        result = await evaluation(hallucination_state, _make_run_config(config))

        # The structural "unsupported" verdict must be preserved — evaluation
        # should skip conclusions already marked unsupported.
        conclusions = result["knowledge"]["conclusions"]
        assert conclusions[0]["status"] == "unsupported"
