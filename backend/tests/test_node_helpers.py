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


class TestParseJsonObject:
    """Unit tests for the multi-strategy JSON extraction pipeline."""

    def test_direct_parse_clean_json(self):
        from moira.workflow.nodes._helpers import _parse_json_object

        result = _parse_json_object('{"route": "accept", "goal_met": true}')
        assert result == {"route": "accept", "goal_met": True}

    def test_strips_think_blocks(self):
        from moira.workflow.nodes._helpers import _parse_json_object

        text = '<think>Let me reason about this...</think>\n{"route": "retry"}'
        result = _parse_json_object(text)
        assert result == {"route": "retry"}

    def test_strips_standalone_close_think(self):
        from moira.workflow.nodes._helpers import _parse_json_object

        text = '</think>\n{"goal_met": false}'
        result = _parse_json_object(text)
        assert result == {"goal_met": False}

    def test_markdown_fence_extraction(self):
        from moira.workflow.nodes._helpers import _parse_json_object

        text = 'Here is my evaluation:\n```json\n{"route": "accept"}\n```\nDone.'
        result = _parse_json_object(text)
        assert result == {"route": "accept"}

    def test_prose_wrapped_json(self):
        from moira.workflow.nodes._helpers import _parse_json_object

        text = (
            "I evaluated the conclusions. "
            '{"route": "accept", "goal_met": true} That is my assessment.'
        )
        result = _parse_json_object(text)
        assert result["route"] == "accept"

    def test_deeply_nested_json(self):
        """Balanced-brace extraction must handle 2+ levels of nesting —
        the old regex could only handle one level."""
        from moira.workflow.nodes._helpers import _parse_json_object

        text = (
            "Result:\n"
            '{"conclusion_results": ['
            '  {"conclusion_id": "c001", "meta": {"confidence": 0.9, "sources": ["a", "b"]}}'
            '], "route": "accept"}'
        )
        result = _parse_json_object(text)
        assert result["route"] == "accept"
        assert result["conclusion_results"][0]["meta"]["confidence"] == 0.9

    def test_braces_inside_string_values(self):
        """Braces appearing inside JSON string values must not confuse
        the depth-counting extractor."""
        from moira.workflow.nodes._helpers import _parse_json_object

        text = '{"goal_assessment": "The set {c001, c002} is verified", "route": "accept"}'
        result = _parse_json_object(text)
        assert result["route"] == "accept"
        assert "{c001, c002}" in result["goal_assessment"]

    def test_trailing_comma_repair(self):
        """Trailing commas before } or ] should be stripped."""
        from moira.workflow.nodes._helpers import _parse_json_object

        text = '{"route": "accept", "goal_met": true,}'
        result = _parse_json_object(text)
        assert result["route"] == "accept"
        assert result["goal_met"] is True

    def test_trailing_comma_in_nested_array(self):
        from moira.workflow.nodes._helpers import _parse_json_object

        text = '{"results": [{"id": "c001",}, {"id": "c002",}], "route": "accept"}'
        result = _parse_json_object(text)
        assert len(result["results"]) == 2
        assert result["route"] == "accept"

    def test_multiple_top_level_objects_picks_longest(self):
        """When multiple {} blocks exist, the longest is tried first."""
        from moira.workflow.nodes._helpers import _parse_json_object

        text = (
            '{"query": "small"} then '
            '{"route": "accept", "goal_met": true, "goal_assessment": "all good"}'
        )
        result = _parse_json_object(text)
        assert result.get("route") == "accept"
        assert "query" not in result

    def test_returns_empty_dict_on_total_failure(self):
        from moira.workflow.nodes._helpers import _parse_json_object

        assert _parse_json_object("no json here at all") == {}
        assert _parse_json_object("") == {}

    def test_control_char_repair_then_parse(self):
        """Literal control chars inside strings are repaired before parsing."""
        from moira.workflow.nodes._helpers import _parse_json_object

        text = '{"goal_assessment": "line1\nline2", "route": "accept"}'
        result = _parse_json_object(text)
        assert result["route"] == "accept"
        assert result["goal_assessment"] == "line1\nline2"

    def test_double_brace_repair(self):
        """Models that mimic str.format() {{}} escaping should be repaired.

        Regression test for a real crash: the evaluation system prompt
        showed JSON examples with {{ }} (intended for .format() but used
        without formatting), and the model echoed the double braces.
        """
        from moira.workflow.nodes._helpers import _parse_json_object

        text = (
            '{{"conclusion_results": [{{"conclusion_id": "c001", '
            '"result": "verified", "reason": "ok"}}, '
            '{{"conclusion_id": "c002", "result": "unsupported", '
            '"reason": "overclaim"}}], "goal_met": false, '
            '"goal_assessment": "not enough", "route": "retry"}}'
        )
        result = _parse_json_object(text)
        assert len(result["conclusion_results"]) == 2
        assert result["conclusion_results"][0]["conclusion_id"] == "c001"
        assert result["goal_met"] is False
        assert result["route"] == "retry"

    def test_double_brace_preserved_inside_string_values(self):
        """{{ }} inside JSON string values must NOT be collapsed."""
        from moira.workflow.nodes._helpers import _fix_double_braces

        text = '{"template": "Hello {{name}}!", "value": 1}'
        # Already valid JSON — _fix_double_braces should leave it unchanged
        # because the {{ }} is inside a string value.
        result = _fix_double_braces(text)
        assert result == text


class TestFixInvalidEscapes:
    """Unit tests for the invalid-escape-sequence repair utility."""

    def test_noop_on_valid_json(self):
        """Already-valid JSON should pass through unchanged."""
        from moira.workflow.nodes._helpers import _fix_invalid_escapes

        valid = '{"answer": "hello\\nworld", "x": 1}'
        assert _fix_invalid_escapes(valid) == valid

    def test_preserves_valid_escape_sequences(self):
        """All RFC 8259 escape sequences must be preserved."""
        from moira.workflow.nodes._helpers import _fix_invalid_escapes

        valid = '{"a": "\\t\\n\\r\\b\\f\\\\\\"\\/", "b": "\\u0041"}'
        assert _fix_invalid_escapes(valid) == valid

    def test_preserves_escaped_backslash(self):
        r"""An escaped backslash (\\) must not be double-treated."""
        from moira.workflow.nodes._helpers import _fix_invalid_escapes

        # \\s in JSON means a literal backslash followed by s.
        # The function must leave it as-is.
        valid = '{"a": "\\\\sim"}'
        assert _fix_invalid_escapes(valid) == valid

    def test_repairs_latex_sim(self):
        r"""\sim (invalid escape \s) becomes \\sim."""
        from moira.workflow.nodes._helpers import _fix_invalid_escapes

        broken = '{"a": "\\sim"}'
        fixed = _fix_invalid_escapes(broken)
        assert fixed == '{"a": "\\\\sim"}'

    def test_repairs_latex_psi(self):
        r"""\psi (invalid escape \p) becomes \\psi."""
        from moira.workflow.nodes._helpers import _fix_invalid_escapes

        broken = '{"a": "\\psi_0(x)"}'
        fixed = _fix_invalid_escapes(broken)
        assert fixed == '{"a": "\\\\psi_0(x)"}'

    def test_repairs_latex_infty(self):
        r"""\infty (invalid escape \i) becomes \\infty."""
        from moira.workflow.nodes._helpers import _fix_invalid_escapes

        broken = '{"a": "x \\to \\infty"}'
        fixed = _fix_invalid_escapes(broken)
        # \t is valid (tab), so \to passes through; \i is invalid, becomes \\i
        assert fixed == '{"a": "x \\to \\\\infty"}'

    def test_does_not_touch_outside_strings(self):
        """Backslashes outside JSON strings should not be modified."""
        from moira.workflow.nodes._helpers import _fix_invalid_escapes

        # No string context here — just structural text
        text = '{"regex": "pattern"}'
        assert _fix_invalid_escapes(text) == text

    def test_parse_succeeds_after_repair(self):
        """End-to-end: json.loads should succeed after repair."""

        from moira.workflow.nodes._helpers import _fix_invalid_escapes

        broken = '{"answer": "$x \\sim y$", "n": 1}'
        with pytest.raises(json.JSONDecodeError):
            json.loads(broken)
        fixed = _fix_invalid_escapes(broken)
        result = json.loads(fixed)
        assert result["answer"] == "$x \\sim y$"
        assert result["n"] == 1

    def test_parse_json_object_with_latex(self):
        """Full pipeline: _parse_json_object must handle LaTeX escapes."""
        from moira.workflow.nodes._helpers import _parse_json_object

        text = '{"answer": "The function $\\psi_0(x) \\sim x$ decays as $x \\to \\infty$", "n": 1}'
        result = _parse_json_object(text)
        assert result["n"] == 1
        assert "\\sim" in result["answer"]
        assert "\\infty" in result["answer"]

    def test_multiple_invalid_escapes(self):
        """Multiple invalid escapes in the same string are all fixed."""
        from moira.workflow.nodes._helpers import _fix_invalid_escapes

        broken = '{"a": "\\sim \\psi \\infty \\zeta"}'
        fixed = _fix_invalid_escapes(broken)
        # Re-parse to verify validity
        result = json.loads(fixed)
        assert result["a"] == "\\sim \\psi \\infty \\zeta"

    def test_backslash_at_end_of_string_value(self):
        r"""A backslash just before the closing quote (\") is a valid escape
        and must not be double-escaped."""
        from moira.workflow.nodes._helpers import _fix_invalid_escapes

        valid = '{"a": "say \\""}'
        assert _fix_invalid_escapes(valid) == valid
