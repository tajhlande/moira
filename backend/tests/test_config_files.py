"""Tests for config loading and prompt file integrity.

These are CI-gate tests: they verify that the config template and prompts file
are well-formed and contain all required sections so deployment errors are
caught early.
"""

import os

import yaml

from moira.config import CostWeights, MoiraConfig
from moira.prompts import REQUIRED_SECTIONS, get_prompt, load_prompts

PROJECT_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CONFIG_TEMPLATE = os.path.join(PROJECT_ROOT, "config", "moira-config-template.yaml")
PROMPTS_FILE = os.path.join(PROJECT_ROOT, "backend", "moira", "resources", "prompts.md")


class TestConfigTemplate:
    def test_template_file_exists(self):
        assert os.path.exists(CONFIG_TEMPLATE), f"Config template not found at {CONFIG_TEMPLATE}"

    def test_template_parses_as_valid_yaml(self):
        with open(CONFIG_TEMPLATE) as f:
            raw = yaml.safe_load(f)
        assert isinstance(raw, dict)

    def test_template_validates_as_moir_config(self):
        with open(CONFIG_TEMPLATE) as f:
            raw = yaml.safe_load(f)
        config = MoiraConfig.model_validate(raw)
        assert config.budget.default_limit > 0
        assert config.database.sqlite_path
        assert len(CostWeights.model_fields) == 8

    def test_template_has_all_cost_weight_keys(self):
        with open(CONFIG_TEMPLATE) as f:
            raw = yaml.safe_load(f)
        config = MoiraConfig.model_validate(raw)
        cw = config.budget.cost_weights
        expected = [
            "decomposition",
            "tool_identification",
            "planning",
            "research",
            "synthesis",
            "research_review",
            "evaluation",
            "report_generation",
        ]
        for key in expected:
            assert getattr(cw, key) > 0, f"Cost weight '{key}' must be > 0"

    def test_template_has_retry_limits(self):
        with open(CONFIG_TEMPLATE) as f:
            raw = yaml.safe_load(f)
        config = MoiraConfig.model_validate(raw)
        rl = config.budget.retry_limits
        assert rl.max_review >= 1
        assert rl.max_evaluation >= 1


class TestPromptsFile:
    def test_prompts_file_exists(self):
        assert os.path.exists(PROMPTS_FILE), f"Prompts file not found at {PROMPTS_FILE}"

    def test_load_validates_required_sections(self):
        """load_prompts() raises SystemExit if any REQUIRED_SECTIONS are
        missing or empty. If this passes, the prompts file is complete."""
        prompts = load_prompts()
        assert all(k in prompts for k in REQUIRED_SECTIONS)

    def test_report_generation_system_has_path_instruction_placeholder(self):
        template = get_prompt("report_generation.system")
        assert "{path_instruction}" in template, (
            "report_generation.system must contain {path_instruction} placeholder"
        )

    def test_all_format_placeholders_are_known(self):
        """Verify that every {variable} in templates that take args has the
        expected placeholders. Prevents typos in template variable names."""
        template_vars = {
            "decomposition.user": {"question"},
            "planning.user": {
                "user_goal",
                "topic",
                "entities",
                "concepts",
                "unknown_facts",
                "tool_descriptions_with_costs_and_limits",
                "budget_remaining",
                "reserved_budget",
                "available_for_tools",
            },
            "planning.system_retry_evaluation": {
                "evaluation_feedback",
                "failed_conclusions",
            },
            "planning.system_retry_review": {
                "coverage_assessment",
                "missing_areas",
            },
            "planning.system_retry_context": {
                "established_facts",
                "prior_conclusions",
                "prior_citations",
            },
            "planning.system_earlier_turns": {"earlier_turns"},
            "planning.system_prior_report": {"prior_question", "prior_report_answer"},
            "research.system": {"max_extra_rounds"},
            "research.user": {
                "user_goal",
                "unknown_facts",
                "tool_call_plan",
                "tool_descriptions",
            },
            "research.system_retry_review": {"coverage_assessment", "missing_areas"},
            "research.system_retry_context": {
                "established_facts",
                "prior_conclusions",
                "prior_citations",
            },
            "synthesis.user": {
                "user_goal",
                "topic",
                "entities",
                "concepts",
                "facts_with_claims",
                "prior_conclusions_section",
            },
            "synthesis.system_retry": {"evaluation_feedback"},
            "research_review.user": {
                "user_goal",
                "question",
                "facts_with_claims_and_sources",
                "conclusions_context",
                "source_content",
            },
            "evaluation.user": {
                "user_goal",
                "question",
                "facts_with_statuses",
                "conclusions_with_supporting_facts",
                "source_content",
            },
            "report_generation.system": {"path_instruction"},
            "report_generation.reason_error": {"error"},
            "report_generation.user": {
                "question",
                "user_goal",
                "verified_facts",
                "verified_conclusions",
                "contradicted_items",
                "unknown_facts",
                "citations",
            },
            "tool_enrichment.user": {"tool_name", "tool_description", "tool_parameters"},
        }
        import re

        prompts = load_prompts()
        for key, expected_vars in template_vars.items():
            template = prompts[key]
            found = set(re.findall(r"\{(\w+)\}", template))
            assert found == expected_vars, (
                f"Template '{key}' has placeholders {found}, expected {expected_vars}"
            )
