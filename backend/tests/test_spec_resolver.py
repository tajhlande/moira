"""Tests for the spec_resolver module."""

import json

import pytest

from moira.services.tool_ingestion.spec_resolver import _validate_spec_text


class TestValidateSpecText:
    def test_valid_json_openapi(self):
        text = json.dumps({"openapi": "3.0.3", "info": {"title": "T", "version": "1"}})
        _validate_spec_text(text)  # should not raise

    def test_valid_json_swagger(self):
        text = json.dumps({"swagger": "2.0", "info": {"title": "T", "version": "1"}})
        _validate_spec_text(text)  # should not raise

    def test_invalid_json(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            _validate_spec_text("{not valid json")

    def test_valid_yaml_openapi(self):
        _validate_spec_text("openapi: '3.0.3'\ninfo:\n  title: Test\n  version: '1'")

    def test_valid_yaml_swagger(self):
        _validate_spec_text("swagger: '2.0'\ninfo:\n  title: Test\n  version: '1'")

    def test_neither_openapi_nor_swagger(self):
        with pytest.raises(ValueError, match="openapi.*swagger"):
            _validate_spec_text('{"title": "not a spec"}')

    def test_plain_text_rejected(self):
        with pytest.raises(ValueError, match="does not appear"):
            _validate_spec_text("this is just text")
