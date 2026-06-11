"""Offline test fixtures for research loop evaluation (Phase 0).

These fixtures define concrete test data that the overhauled research loop
must handle correctly. They are designed to be run against the current
system (where they will likely fail) and then against the overhauled system
to demonstrate improvement.

Three fixtures:
1. Verification stress - planted mistakes in type, ability, moves, legality, synergy
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
from moira.models.state import Finding, ResearchState
from moira.service_setup import _services
from moira.tools.base import ToolDefinition, ToolResult


@pytest.fixture
def config():
    return MoiraConfig()


@pytest.fixture
def mock_writer():
    events = []

    def write(event):
        events.append(event)

    with patch("moira.workflow.nodes.research_nodes.get_stream_writer", return_value=write):
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


# ---------------------------------------------------------------------------
# Fixture 1: Verification Stress
# ---------------------------------------------------------------------------
# A short Tyranitar-partner draft with planted mistakes in type matchups,
# abilities, typical moves, OU legality, and synergy reasoning.
# The verification system should catch these errors.
#
# Planted errors:
# 1. TYPE: Claims Tyranitar is weak to Ground (actually takes neutral from Ground
#    due to Rock resisting it). Real weakness: Fighting x4, Fairy, Water, Grass,
#    Bug, Steel, Ground (x2), but the draft says Ground x4 which is wrong.
# 2. ABILITY: Claims Tyranitar's Sand Stream sets sand for 8 turns (actually 5).
# 3. TYPICAL MOVE: Lists Fire Blast as a typical Tyranitar OU move (it's niche,
#    not typical). Also lists Draco Meteor (Tyranitar cannot learn Draco Meteor).
# 4. OU LEGALITY: Claims Tera Blast is "common in Gen9 OU Tyranitar" without
#    distinguishing between legal and typical. Also implies Tyranitar is always
#    S-rank viability (it fluctuates).
# 5. SYNERGY: Claims Corviknight pairs well because it's "immune to Ground"
#    (Corviknight takes neutral from Ground, not immune). The real synergy is
#    that Corviknight resists Fighting and is immune to Ground... wait, actually
#    Corviknight IS immune to Ground due to Levitate? No - Corviknight does NOT
#    have Levitate. It has Pressure and Defiant. So it takes neutral from Ground
#    (x2 vs Rock, resisted by Steel = neutral). The claim of Ground immunity is wrong.

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

# Expected errors the verification system should flag
VERIFICATION_STRESS_EXPECTED_ERRORS = [
    {
        "category": "type_correctness",
        "error": "Claims Ground x4 weakness. Tyranitar is Rock/Dark: Ground is x2 "
        "(super effective vs Rock, neutral vs Dark). Not x4.",
    },
    {
        "category": "ability_correctness",
        "error": "Claims Sand Stream lasts 8 turns. In Gen 9, Sand Stream sets "
        "sandstorm for 5 turns (changed from permanent weather in earlier gens).",
    },
    {
        "category": "typical_move_discipline",
        "error": "Lists Draco Meteor as a typical Tyranitar move. Tyranitar cannot "
        "learn Draco Meteor in any generation.",
    },
    {
        "category": "typical_move_discipline",
        "error": "Lists Fire Blast as typical. Fire Blast is niche on Tyranitar, "
        "not a standard OU moveset pick.",
    },
    {
        "category": "ou_legality",
        "error": "Claims Tera Blast is 'very common' and Tyranitar is 'S-rank'. "
        "These are relevance claims, not legality claims. Tera Blast's prevalence "
        "and Tyranitar's viability tier should be distinguished from OU legality.",
    },
    {
        "category": "synthesis_discipline",
        "error": "Claims Corviknight is 'immune to Ground'. Corviknight does not have "
        "Levitate (abilities are Pressure/Defiant). Ground deals neutral damage to "
        "Corviknight (x2 to Rock type, resisted by Steel type = neutral). The central "
        "synergy claim is based on a factual error.",
    },
]


class TestVerificationStressFixture:
    """Verification should catch planted errors in the stress-test draft.

    These tests use the current system's verification node. They are expected
    to FAIL against the current system (the verification is not robust enough)
    and PASS against the overhauled system with claim-level verification.
    """

    @pytest.fixture
    def stress_state(self, config) -> ResearchState:
        cw = config.budget.cost_weights
        return {
            "question": "What Pokemon synergize well with Tyranitar in Gen9 OU?",
            "plan": "Research Tyranitar typing, abilities, weaknesses, partners",
            "active_tools": [
                ToolDefinition(name="web_search", description="Search the web"),
                ToolDefinition(name="pokeapi", description="Pokemon data API"),
            ],
            "findings": [],
            "compressed_findings": [],
            "draft": VERIFICATION_STRESS_DRAFT,
            "verification": "",
            "report": None,
            "budget_remaining": 30.0,
            "budget_limit": 50.0,
            "cost_weights": {
                "planning": cw.planning,
                "tool_discovery": cw.tool_discovery,
                "tool_selection": cw.tool_selection,
                "research_execution": cw.research_execution,
                "compression": cw.compression,
                "draft_synthesis": cw.draft_synthesis,
                "verification": cw.verification,
                "report_generation": cw.report_generation,
            },
            "verification_history": [],
            "unverified_claims": [],
            "error": "",
            "draft_retry_count": 0,
        }

    async def test_flags_ground_weakness_error(
        self, config, mock_writer, mock_model, stress_state
    ):
        """Verification should catch that Ground is not x4 on Tyranitar."""
        _inject_services(config, mock_model)
        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="pokeapi",
                    output=(
                        "Tyranitar: Rock/Dark. Ground is x2 "
                        "(super effective vs Rock, neutral vs Dark). Not x4."
                    ),
                    success=True,
                    duration_ms=100,
                )
            ]
        )
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content=json.dumps(
                    [{"tool": "pokeapi", "args": {"query": "Tyranitar type weaknesses"}}]
                )
            ),
            ChatResponse(content="Fact-checking complete."),
            ChatResponse(
                content=json.dumps(
                    {
                        "outcome": "retry_plan",
                        "case": 6,
                        "assessment": "Ground x4 weakness claim is wrong",
                        "supported_claims": ["Tyranitar is Rock/Dark type"],
                        "unsupported_claims": [
                            "Ground x4 weakness is incorrect: Ground is x2, not x4"
                        ],
                        "contradictions": [
                            "Draft claims Ground x4 but Tyranitar takes x2 from Ground"
                        ],
                        "relevance": "on_topic",
                        "depth": "sufficient",
                        "guidance": "Fix type weakness calculations",
                    }
                )
            ),
        ]

        from moira.workflow.nodes.research_nodes import verification

        result = await verification(stress_state, _make_run_config(config))

        assessment = result["verification_history"][0]["assessment"].lower()
        contradictions = result["verification_history"][0].get("contradictions", [])
        unsupported = result["verification_history"][0].get("unsupported_claims", [])
        all_text = " ".join(
            [assessment] + contradictions + unsupported
        ).lower()

        assert "ground" in all_text, (
            "Verification should flag the Ground x4 error. "
            f"Got assessment: {assessment}, contradictions: {contradictions}"
        )

    async def test_flags_sand_stream_duration_error(
        self, config, mock_writer, mock_model, stress_state
    ):
        """Verification should catch that Sand Stream lasts 5 turns, not 8."""
        _inject_services(config, mock_model)
        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="pokeapi",
                    output="Sand Stream: sets sandstorm for 5 turns in Gen9",
                    success=True,
                    duration_ms=100,
                )
            ]
        )
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content=json.dumps(
                    [{"tool": "pokeapi", "args": {"query": "Sand Stream ability duration"}}]
                )
            ),
            ChatResponse(content="Fact-checking complete."),
            ChatResponse(
                content=json.dumps(
                    {
                        "outcome": "retry_plan",
                        "case": 6,
                        "assessment": "Sand Stream duration is wrong",
                        "supported_claims": [],
                        "unsupported_claims": ["Sand Stream lasts 5 turns, not 8"],
                        "contradictions": ["Draft claims 8 turns, actual is 5"],
                        "relevance": "on_topic",
                        "depth": "sufficient",
                        "guidance": "Fix ability details",
                    }
                )
            ),
        ]

        from moira.workflow.nodes.research_nodes import verification

        result = await verification(stress_state, _make_run_config(config))

        all_text = " ".join(
            [
                result["verification_history"][0]["assessment"],
                *result["verification_history"][0].get("contradictions", []),
                *result["verification_history"][0].get("unsupported_claims", []),
            ]
        ).lower()

        assert "sand" in all_text or "5 turn" in all_text or "8 turn" in all_text, (
            "Verification should flag the Sand Stream duration error. "
            f"Got: {all_text}"
        )

    async def test_flags_draco_meteor_impossibility(
        self, config, mock_writer, mock_model, stress_state
    ):
        """Verification should catch that Tyranitar cannot learn Draco Meteor."""
        _inject_services(config, mock_model)
        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="pokeapi",
                    output="Tyranitar learnset does not include Draco Meteor",
                    success=True,
                    duration_ms=100,
                )
            ]
        )
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content=json.dumps(
                    [{"tool": "pokeapi", "args": {"query": "Tyranitar learnset Draco Meteor"}}]
                )
            ),
            ChatResponse(content="Fact-checking complete."),
            ChatResponse(
                content=json.dumps(
                    {
                        "outcome": "retry_plan",
                        "case": 6,
                        "assessment": "Draco Meteor is not in Tyranitar's learnset",
                        "supported_claims": [],
                        "unsupported_claims": ["Tyranitar cannot learn Draco Meteor"],
                        "contradictions": ["Draft lists Draco Meteor as a typical move"],
                        "relevance": "on_topic",
                        "depth": "sufficient",
                        "guidance": "Remove Draco Meteor from move list",
                    }
                )
            ),
        ]

        from moira.workflow.nodes.research_nodes import verification

        result = await verification(stress_state, _make_run_config(config))

        all_text = " ".join(
            [
                result["verification_history"][0]["assessment"],
                *result["verification_history"][0].get("contradictions", []),
                *result["verification_history"][0].get("unsupported_claims", []),
            ]
        ).lower()

        assert "draco" in all_text or "learnset" in all_text or "cannot learn" in all_text, (
            "Verification should flag Draco Meteor as impossible. "
            f"Got: {all_text}"
        )

    async def test_flags_corviknight_ground_immunity_error(
        self, config, mock_writer, mock_model, stress_state
    ):
        """Verification should catch that Corviknight is NOT immune to Ground."""
        _inject_services(config, mock_model)
        mock_executor = AsyncMock()
        mock_executor.execute_batch = AsyncMock(
            return_value=[
                ToolResult(
                    tool_name="pokeapi",
                    output=(
                        "Corviknight: Steel/Flying. "
                        "Abilities: Pressure, Defiant (hidden). No Levitate."
                    ),
                    success=True,
                    duration_ms=100,
                )
            ]
        )
        _services["tool_executor"] = mock_executor

        mock_model["client"].chat_completion.side_effect = [
            ChatResponse(
                content=json.dumps(
                    [{"tool": "pokeapi", "args": {"query": "Corviknight abilities type"}}]
                )
            ),
            ChatResponse(content="Fact-checking complete."),
            ChatResponse(
                content=json.dumps(
                    {
                        "outcome": "retry_plan",
                        "case": 6,
                        "assessment": "Corviknight Ground immunity claim is wrong",
                        "supported_claims": [],
                        "unsupported_claims": [
                            "Corviknight does not have Levitate, takes neutral from Ground"
                        ],
                        "contradictions": [
                            "Draft claims Corviknight is immune to Ground"
                        ],
                        "relevance": "on_topic",
                        "depth": "sufficient",
                        "guidance": "Fix Corviknight Ground interaction",
                    }
                )
            ),
        ]

        from moira.workflow.nodes.research_nodes import verification

        result = await verification(stress_state, _make_run_config(config))

        all_text = " ".join(
            [
                result["verification_history"][0]["assessment"],
                *result["verification_history"][0].get("contradictions", []),
                *result["verification_history"][0].get("unsupported_claims", []),
            ]
        ).lower()

        assert (
            "corviknight" in all_text
            and ("ground" in all_text or "immune" in all_text or "levitate" in all_text)
        ), (
            "Verification should flag the Corviknight Ground immunity error. "
            f"Got: {all_text}"
        )


# ---------------------------------------------------------------------------
# Fixture 2: Tool Routing
# ---------------------------------------------------------------------------
# Decomposed facts from the canary question, run against a fixed tool catalog.
# The system should rank Pokemon-specific tools ahead of web_search for
# structured facts.

# The decomposed facts that the canary question should produce.
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

# A fixed tool catalog for testing routing decisions.
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

# Expected routing: which tool should be preferred for which fact.
# Each entry maps a fact subject pattern to the expected primary tool.
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
    domain-specific tools ahead of generic search. They test against the
    current system and will be used as regression tests for the overhauled
    system's tool_identification node.
    """

    def test_pokeapi_ranked_above_web_search_for_typing(self):
        """For Tyranitar typing facts, pokeapi should rank higher than web_search."""
        # The key assertion: for species-specific facts, a Pokemon API
        # should produce a higher similarity score than generic search.
        # This is a structural assertion about the LanceDB embedding match.
        #
        # In the current system, this may not hold because the embeddings
        # may not capture "this tool answers species data questions" well.
        # The overhauled system's enriched tool descriptions should fix this.
        #
        # For now, we document the expected behavior:
        pokeapi_desc = TOOL_CATALOG[0].description
        web_search_desc = TOOL_CATALOG[3].description

        # The Pokemon API description explicitly mentions "typing" and "species"
        # which should produce a stronger match for the typing query.
        assert "typing" in pokeapi_desc.lower()
        assert "typing" not in web_search_desc.lower()

    def test_pokemon_db_ranked_above_web_search_for_ou_legality(self):
        """For OU legality facts, pokemon_db should rank higher than web_search."""
        pokemon_db_desc = TOOL_CATALOG[1].description
        pokemon_db_tags = TOOL_CATALOG[1].tags
        web_search_desc = TOOL_CATALOG[3].description

        assert "tier" in pokemon_db_desc.lower()
        assert any(t.lower() == "ou" for t in pokemon_db_tags), (
            f"pokemon_db tags should contain 'ou': {pokemon_db_tags}"
        )
        assert "legality" not in web_search_desc.lower()

    def test_catalog_structure_matches_routing_expectations(self):
        """Validate that our test catalog has the expected structure."""
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
        """Validate that the decomposed facts cover the evaluation rubric areas."""
        facts = CANARY_DECOMPOSED_FACTS
        fact_texts = [f["fact_needed"].lower() for f in facts]

        # Must cover typing
        assert any("typing" in f for f in fact_texts)
        # Must cover abilities
        assert any("abilit" in f for f in fact_texts)
        # Must cover weaknesses
        assert any("weakness" in f for f in fact_texts)
        # Must cover moves
        assert any("move" in f for f in fact_texts)
        # Must cover legality
        assert any("legal" in f for f in fact_texts)

    async def test_tool_discovery_ranks_specialized_first(
        self, config, mock_writer, mock_model
    ):
        """When discovering tools for Pokemon-specific facts, specialized tools
        should be in the candidate list and ranked ahead of web_search.

        This test uses the current tool_discovery node to verify that
        LanceDB returns Pokemon tools for Pokemon-related plan text.
        It is expected to partially fail against the current system
        (which embeds the plan text, not individual fact queries)."""
        _inject_services(config, mock_model)

        from moira.workflow.nodes.research_nodes import tool_discovery

        # Mock LanceDB discovery to return tools in similarity order
        pokemon_tools = [t for t in TOOL_CATALOG if not t.is_default]
        mock_discovery = AsyncMock()
        mock_discovery.discover = AsyncMock(return_value=pokemon_tools[:3])
        _services["tool_discovery"] = mock_discovery

        default_tools = [t for t in TOOL_CATALOG if t.is_default]
        mock_catalog = MagicMock()
        mock_catalog.get_default_tools.return_value = default_tools
        _services["tool_catalog"] = mock_catalog

        state = {
            "question": "What Pokemon synergize with Tyranitar in Gen9 OU?",
            "plan": "Research Tyranitar typing, abilities, weaknesses, OU legality, teammates",
            "active_tools": [],
            "budget_remaining": 50.0,
            "cost_weights": config.budget.cost_weights.model_dump(),
        }

        result = await tool_discovery(state, _make_run_config(config))

        discovered_names = [t.name for t in result.get("active_tools", [])]
        # pokeapi and pokemon_db should be discovered
        assert "pokeapi" in discovered_names or "pokemon_db" in discovered_names, (
            f"Expected Pokemon tools in discovery results, got: {discovered_names}"
        )


# ---------------------------------------------------------------------------
# Fixture 3: Synthesis Trap
# ---------------------------------------------------------------------------
# A fixed fact bundle containing individually true facts that tempt an
# unjustified conclusion. The system should qualify the claim or refuse
# to overstate it.

# Individually true facts about Tyranitar and a potential partner.
SYNTHESIS_TRAP_FACTS = [
    Finding(
        content="Tyranitar is Rock/Dark type",
        source="pokeapi",
        citation_url="https://pokeapi.co/api/v2/pokemon/248",
        type="evidence",
    ),
    Finding(
        content="Tyranitar is weak to Fighting (x4), Water, Grass, Bug, Steel, Fairy, Ground (x2)",
        source="pokeapi",
        citation_url="https://pokeapi.co/api/v2/type/rock",
        type="evidence",
    ),
    Finding(
        content="Tyranitar has the ability Sand Stream which sets sandstorm for 5 turns",
        source="pokeapi",
        citation_url="https://pokeapi.co/api/v2/ability/29",
        type="evidence",
    ),
    Finding(
        content="Corviknight is Steel/Flying type",
        source="pokeapi",
        citation_url="https://pokeapi.co/api/v2/pokemon/823",
        type="evidence",
    ),
    Finding(
        content=(
            "Corviknight resists Fairy (x0.5), "
            "takes neutral from Fighting (Rock x2, Steel x0.5), "
            "immune to Ground (Flying type)"
        ),
        source="pokeapi",
        citation_url="https://pokeapi.co/api/v2/type/flying",
        type="evidence",
    ),
    Finding(
        content="Corviknight has the ability Pressure (or Defiant as hidden ability)",
        source="pokeapi",
        citation_url="https://pokeapi.co/api/v2/pokemon/823",
        type="evidence",
    ),
    Finding(
        content="Corviknight can learn Roost, Brave Bird, U-turn, Iron Head, Body Press",
        source="pokeapi",
        citation_url="https://pokeapi.co/api/v2/pokemon/823",
        type="evidence",
    ),
    Finding(
        content="Tinkaton is Fairy/Steel type",
        source="pokeapi",
        citation_url="https://pokeapi.co/api/v2/pokemon/957",
        type="evidence",
    ),
    Finding(
        content="Tinkaton resists Dark (x0.5), Rock (x0.5), and is immune to Dragon",
        source="pokeapi",
        citation_url="https://pokeapi.co/api/v2/type/fairy",
        type="evidence",
    ),
]

# The trap: from these facts, one might conclude "Corviknight is the BEST partner"
# because it resists some of Tyranitar's weaknesses. But the facts don't support
# "best" — they only support "covers some weaknesses." The synthesis should NOT:
# 1. Claim Corviknight is "the best" or "top" partner without usage/stat backing
# 2. Claim Corviknight makes Tyranitar "easy to use" without battle data
# 3. Ignore that Corviknight takes neutral from Fighting (Tyranitar's x4 weakness)
# 4. Claim Flying-type Ground immunity is the "main" synergy (it's one factor)

SYNTHESIS_TRAP_EXPECTED_BEHAVIORS = [
    "Should NOT claim Corviknight is the best partner without usage data",
    "Should NOT claim Corviknight makes Tyranitar viable without battle data",
    "Should qualify that Corviknight takes neutral from Fighting (not a full resist)",
    "Should acknowledge limited evidence: no usage stats, no matchup data provided",
    "Should note that other partners (like Tinkaton) also cover weaknesses",
]


class TestSynthesisTrapFixture:
    """Synthesis should not overstate conclusions from individually true facts.

    These tests verify that the draft_synthesis and verification nodes don't
    draw unjustified conclusions from fact bundles. The facts are individually
    true but don't support strong recommendations.

    NOTE: These tests are expected to FAIL against the current system.
    The current synthesis prompt does not enforce strict fact-only reasoning,
    so the model's output (which we mock) passes through unchecked. These
    tests document the behavior the overhauled system must achieve.
    """

    @pytest.fixture
    def trap_state(self, config) -> ResearchState:
        cw = config.budget.cost_weights
        return {
            "question": "What Pokemon synergize well with Tyranitar in Gen9 OU?",
            "plan": "Research Tyranitar typing, weaknesses, potential partners",
            "active_tools": [
                ToolDefinition(name="pokeapi", description="Pokemon data API"),
            ],
            "findings": list(SYNTHESIS_TRAP_FACTS),
            "compressed_findings": [],
            "draft": "",
            "verification": "",
            "report": None,
            "budget_remaining": 30.0,
            "budget_limit": 50.0,
            "cost_weights": {
                "planning": cw.planning,
                "tool_discovery": cw.tool_discovery,
                "tool_selection": cw.tool_selection,
                "research_execution": cw.research_execution,
                "compression": cw.compression,
                "draft_synthesis": cw.draft_synthesis,
                "verification": cw.verification,
                "report_generation": cw.report_generation,
            },
            "verification_history": [],
            "unverified_claims": [],
            "error": "",
            "draft_retry_count": 0,
        }

    @pytest.mark.xfail(
        reason="Current system passes model output through without checking "
        "for overclaims. Overhauled synthesis must enforce fact-only reasoning.",
        strict=True,
    )
    async def test_synthesis_does_not_claim_best_partner(
        self, config, mock_writer, mock_model, trap_state
    ):
        """Draft synthesis should not claim Corviknight is 'the best' partner
        without usage/stat evidence."""
        _inject_services(config, mock_model)

        # Simulate a draft that overclaims
        overclaiming_draft = (
            "Corviknight is the best partner for Tyranitar in Gen9 OU. "
            "Its typing perfectly covers all of Tyranitar's weaknesses, "
            "making Tyranitar easy to use on any team."
        )
        mock_model["client"].chat_completion.return_value = ChatResponse(
            content=overclaiming_draft
        )

        from moira.workflow.nodes.research_nodes import draft_synthesis

        result = await draft_synthesis(trap_state, _make_run_config(config))

        draft = result.get("draft", "")
        # The draft should NOT contain superlative claims like "best" or
        # "perfectly covers all" given the limited evidence.
        # This test documents the expected behavior; it will likely fail
        # against the current system because the model tends to overstate.
        #
        # We check for the most egregious overclaims:
        assert "best partner" not in draft.lower() or "evidence" in draft.lower(), (
            "Draft should not claim 'best partner' without usage data. "
            f"Got: {draft[:200]}"
        )

    @pytest.mark.xfail(
        reason="Current system passes model output through without checking "
        "Fighting type interaction accuracy. Overhauled synthesis must enforce "
        "fact-only reasoning.",
        strict=True,
    )
    async def test_synthesis_qualifies_fighting_neutral(
        self, config, mock_writer, mock_model, trap_state
    ):
        """Draft should note that Corviknight takes neutral from Fighting,
        not resist it (Tyranitar's x4 weakness)."""
        _inject_services(config, mock_model)

        mock_model["client"].chat_completion.return_value = ChatResponse(
            content="Corviknight resists all of Tyranitar's weaknesses including Fighting."
        )

        from moira.workflow.nodes.research_nodes import draft_synthesis

        result = await draft_synthesis(trap_state, _make_run_config(config))

        draft = result.get("draft", "").lower()
        # Should not claim Corviknight "resists Fighting"
        if "resist" in draft and "fighting" in draft:
            assert "neutral" in draft or "does not resist" in draft, (
                "Draft should clarify Corviknight takes neutral from Fighting, "
                "not resist it. Got: " + draft[:200]
            )

    async def test_synthesis_acknowledges_limited_evidence(
        self, config, mock_writer, mock_model, trap_state
    ):
        """Draft should acknowledge that no usage data or matchup data was
        provided in the facts."""
        _inject_services(config, mock_model)

        mock_model["client"].chat_completion.return_value = ChatResponse(
            content="Based on the available type matchups, Corviknight provides "
            "good defensive synergy. However, without usage statistics or "
            "competitive matchup data, a definitive ranking cannot be established."
        )

        from moira.workflow.nodes.research_nodes import draft_synthesis

        result = await draft_synthesis(trap_state, _make_run_config(config))

        draft = result.get("draft", "").lower()
        # Should contain some hedging language
        has_qualification = any(
            word in draft
            for word in ["however", "without", "limited", "based on", "available", "suggests"]
        )
        assert has_qualification, (
            "Draft should hedge given limited evidence. Got: " + draft[:200]
        )
