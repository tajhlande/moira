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
        assert len(CostWeights.model_fields) == 7

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
            "verification",
            "report_generation",
        ]
        for key in expected:
            assert getattr(cw, key) > 0, f"Cost weight '{key}' must be > 0"

    def test_template_has_inference_structure(self):
        with open(CONFIG_TEMPLATE) as f:
            raw = yaml.safe_load(f)
        config = MoiraConfig.model_validate(raw)
        assert hasattr(config.inference, "providers")
        assert hasattr(config.inference, "models")
        assert hasattr(config.inference.models, "intelligence_endpoint")
        assert hasattr(config.inference.models, "task_endpoint")


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
            "planning.system_retry": {"case", "assessment", "guidance"},
            "planning.system_earlier_turns": {"earlier_turns"},
            "planning.system_prior_report": {"prior_question", "prior_report_answer"},
            "tool_selection.user": {"question", "plan", "tool_descriptions"},
            "research_execution.user": {"question", "plan", "tool_descriptions"},
            "draft_synthesis.user": {"question", "plan", "evidence"},
            "draft_synthesis.system_retry": {"case", "assessment", "guidance"},
            "verification.user": {"question", "draft"},
            "verification.fact_check.user": {"question", "draft", "tool_descriptions"},
            "verification.evidence": {"evidence"},
            "report_generation.system": {"path_instruction"},
            "report_generation.path_error": {"error"},
            "report_generation.path_budget_exhausted": {"unverified_claims"},
            "report_generation.user": {"question", "draft", "evidence"},
        }
        import re

        prompts = load_prompts()
        for key, expected_vars in template_vars.items():
            template = prompts[key]
            found = set(re.findall(r"\{(\w+)\}", template))
            assert found == expected_vars, (
                f"Template '{key}' has placeholders {found}, expected {expected_vars}"
            )
