"""Tests for workflow node shared helpers."""

import json

import pytest


class TestFixJsonControlChars:
    """Unit tests for the literal-control-character repair utility."""

    def test_noop_on_valid_json(self):
        """Already-valid JSON should pass through unchanged."""
        from moira.workflow.nodes._helpers import _fix_json_control_chars

        valid = '{"answer": "hello\\nworld", "x": 1}'
        assert _fix_json_control_chars(valid) == valid

    def test_noop_on_json_without_strings(self):
        """JSON with no string values should pass through unchanged."""
        from moira.workflow.nodes._helpers import _fix_json_control_chars

        valid = '{"x": 1, "y": [1, 2, 3]}'
        assert _fix_json_control_chars(valid) == valid

    def test_repairs_literal_newline_in_string(self):
        """Literal newline inside a JSON string value should be escaped."""
        from moira.workflow.nodes._helpers import _fix_json_control_chars

        broken = '{"answer": "line1\nline2"}'
        fixed = _fix_json_control_chars(broken)
        assert fixed == '{"answer": "line1\\nline2"}'

    def test_preserves_structural_newlines(self):
        """Newlines between JSON tokens (outside strings) should be
        preserved as-is — they are valid JSON whitespace."""
        from moira.workflow.nodes._helpers import _fix_json_control_chars

        text = '{\n  "answer": "ok"\n}'
        assert _fix_json_control_chars(text) == text

    def test_repairs_literal_tab_in_string(self):
        """Literal tab inside a JSON string value should be escaped."""
        from moira.workflow.nodes._helpers import _fix_json_control_chars

        broken = '{"a": "col1\tcol2"}'
        fixed = _fix_json_control_chars(broken)
        assert fixed == '{"a": "col1\\tcol2"}'

    def test_handles_escaped_quotes(self):
        """Escaped quotes (\\") should not toggle the in-string state."""
        from moira.workflow.nodes._helpers import _fix_json_control_chars

        # The \" inside the string should not end the string, so the
        # literal newline after it should be escaped.
        broken = '{"a": "say \\"hi\\" then\nmore"}'
        fixed = _fix_json_control_chars(broken)
        assert "\\n" in fixed
        assert "\n" not in fixed.split('"')[3]  # inside the string value

    def test_parse_succeeds_after_repair(self):
        """End-to-end: json.loads should succeed after repair."""

        from moira.workflow.nodes._helpers import _fix_json_control_chars

        broken = '{"answer": "line1\nline2", "x": 1}'
        # Direct json.loads should fail
        with pytest.raises(json.JSONDecodeError):
            json.loads(broken)
        # After repair, it should succeed
        fixed = _fix_json_control_chars(broken)
        result = json.loads(fixed)
        assert result["answer"] == "line1\nline2"
        assert result["x"] == 1
