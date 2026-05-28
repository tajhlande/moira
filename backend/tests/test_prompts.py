import pytest

from moira.prompts import _parse, get_prompt, load_prompts


class TestPromptParsing:
    def test_parse_single_section(self):
        content = "## greeting.system\n\nHello, you are a helpful assistant.\n"
        result = _parse(content)
        assert result == {"greeting.system": "Hello, you are a helpful assistant."}

    def test_parse_multiple_sections(self):
        content = "## a.system\n\nSystem prompt A.\n\n## a.user\n\nUser prompt A.\n"
        result = _parse(content)
        assert "a.system" in result
        assert "a.user" in result
        assert result["a.system"] == "System prompt A."
        assert result["a.user"] == "User prompt A."

    def test_parse_strips_whitespace(self):
        content = "## test.key\n\n  padded content  \n\n"
        result = _parse(content)
        assert result["test.key"] == "padded content"

    def test_parse_ignores_preamble(self):
        content = "# Title\nSome intro text\n\n## first.section\n\nFirst body.\n"
        result = _parse(content)
        assert "first.section" in result
        assert len(result) == 1


class TestLoadPrompts:
    def test_load_prompts_from_file(self):
        prompts = load_prompts()
        assert "planning.system" in prompts
        assert "verification.system" in prompts
        assert "report_generation.system" in prompts

    def test_get_prompt_returns_template(self):
        template = get_prompt("planning.system")
        assert "research" in template.lower()

    def test_get_prompt_missing_key_raises(self):
        with pytest.raises(KeyError, match="nonexistent"):
            get_prompt("nonexistent")

    def test_env_var_override(self, tmp_path, monkeypatch):
        from moira.prompts import REQUIRED_SECTIONS

        # Build a custom file with all required sections so validation passes
        sections = ["## custom.key\n\nCustom content.\n"]
        for key in REQUIRED_SECTIONS:
            sections.append(f"## {key}\n\nPlaceholder for {key}.\n")
        prompt_file = tmp_path / "custom.md"
        prompt_file.write_text("\n".join(sections))
        monkeypatch.setenv("MOIRA_PROMPT_FILE", str(prompt_file))

        import moira.prompts

        moira.prompts._PROMPTS = None

        result = get_prompt("custom.key")
        assert result == "Custom content."

        moira.prompts._PROMPTS = None
        monkeypatch.delenv("MOIRA_PROMPT_FILE")

    def test_format_variables(self):
        template = get_prompt("report_generation.system")
        # Should not raise — {path_instruction} is the only variable
        formatted = template.format(path_instruction="Test instruction")
        assert "Test instruction" in formatted
