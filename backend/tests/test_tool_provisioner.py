"""Tests for the tool_provisioner module."""

from unittest.mock import AsyncMock, patch

import pytest

from moira.persistence.interfaces import ApiSource
from moira.services.tool_ingestion.tool_provisioner import (
    REST_TOOL_IMPL,
    ToolProvisioner,
    build_argument_schema,
)
from moira.tools.base import ToolDefinition


class TestBuildArgumentSchema:
    """Tests for the build_argument_schema helper."""

    def test_empty_params(self):
        schema = build_argument_schema([], None)
        assert schema == {"type": "object", "properties": {}, "required": []}

    def test_params_with_locations(self):
        params = [
            {
                "name": "city",
                "location": "query",
                "required": True,
                "schema_def": {"type": "string"},
                "description": "City name",
            },
            {
                "name": "X-Api-Key",
                "location": "header",
                "required": True,
                "schema_def": {"type": "string"},
                "description": "API key",
            },
        ]
        schema = build_argument_schema(params, None)
        assert schema["properties"]["city"]["x-parameter-location"] == "query"
        assert schema["properties"]["X-Api-Key"]["x-parameter-location"] == "header"
        assert "city" in schema["required"]
        assert "X-Api-Key" in schema["required"]

    def test_body_schema_merged(self):
        body_schema = {
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
            "required": ["name"],
        }
        schema = build_argument_schema([], body_schema)
        assert "name" in schema["properties"]
        assert "age" in schema["properties"]
        assert "name" in schema["required"]

    def test_param_takes_precedence_over_body(self):
        """If a param and body property share a name, param wins."""
        params = [
            {
                "name": "name",
                "location": "query",
                "required": False,
                "schema_def": {"type": "string"},
                "description": "Name",
            },
        ]
        body_schema = {
            "properties": {"name": {"type": "integer"}},
            "required": [],
        }
        schema = build_argument_schema(params, body_schema)
        # Param's definition wins (it has x-parameter-location)
        assert schema["properties"]["name"]["x-parameter-location"] == "query"


def _mock_source_repo():
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=None)
    repo.get_all = AsyncMock(return_value=[])
    repo.save = AsyncMock()
    repo.delete = AsyncMock(return_value=True)
    repo.update_tool_count = AsyncMock()
    return repo


def _mock_tool_repo():
    repo = AsyncMock()
    repo.get_all_tools = AsyncMock(return_value=[])
    repo.get_tool = AsyncMock(return_value=None)
    repo.save_tool = AsyncMock()
    repo.delete_tool = AsyncMock(return_value=True)
    repo.save_group = AsyncMock()
    return repo


def _sample_operations():
    return [
        {
            "name": "test_api__get_items",
            "description": "Get all items",
            "method": "GET",
            "path": "/items",
            "parameters": [
                {
                    "name": "limit",
                    "location": "query",
                    "required": False,
                    "schema_def": {"type": "integer"},
                    "description": "Max items",
                },
            ],
            "request_body": None,
            "tags": ["items"],
            "deprecated": False,
            "security_requirements": [],
            "operation_id": "getItems",
        },
        {
            "name": "test_api__get_item",
            "description": "Get a single item",
            "method": "GET",
            "path": "/items/{id}",
            "parameters": [
                {
                    "name": "id",
                    "location": "path",
                    "required": True,
                    "schema_def": {"type": "string"},
                    "description": "Item ID",
                },
            ],
            "request_body": None,
            "tags": ["items"],
            "deprecated": False,
            "security_requirements": [],
            "operation_id": "getItem",
        },
    ]


class TestToolProvisionerCommit:
    """Tests for the provisioner's commit phase."""

    @pytest.mark.asyncio
    async def test_commit_creates_tools(self):
        source_repo = _mock_source_repo()
        tool_repo = _mock_tool_repo()

        with patch("moira.services.tool_ingestion.tool_provisioner.service_provider") as mock_sp:
            mock_sp.return_value = None
            provisioner = ToolProvisioner(source_repo=source_repo, tool_repo=tool_repo)

            result = await provisioner.commit(
                source_id="src-123",
                base_url="https://api.example.com",
                spec_url="https://api.example.com/openapi.json",
                spec_format="openapi_3_0",
                group_name="Test API",
                auth_type=None,
                selected_operations=["test_api__get_items"],
                operations_data=_sample_operations(),
                server_url="https://api.example.com/v1",
            )

        assert result.succeeded == ["test_api__get_items"]
        assert result.failed == []
        assert result.disabled == []
        assert tool_repo.save_tool.call_count == 1
        tool_repo.save_group.assert_called_once_with(
            {
                "name": "test_api",
                "display_name": "Test API",
            }
        )
        source_repo.save.assert_called_once()

    @pytest.mark.asyncio
    async def test_commit_auth_required_disables_tools(self):
        source_repo = _mock_source_repo()
        tool_repo = _mock_tool_repo()

        with patch("moira.services.tool_ingestion.tool_provisioner.service_provider") as mock_sp:
            mock_sp.return_value = None
            provisioner = ToolProvisioner(source_repo=source_repo, tool_repo=tool_repo)

            result = await provisioner.commit(
                source_id="src-456",
                base_url="https://api.example.com",
                spec_url=None,
                spec_format="openapi_3_0",
                group_name="Auth API",
                auth_type="bearer",
                selected_operations=["test_api__get_items"],
                operations_data=_sample_operations(),
                server_url="https://api.example.com",
            )

        assert result.succeeded == ["test_api__get_items"]
        assert result.disabled == ["test_api__get_items"]
        saved_tool = tool_repo.save_tool.call_args[0][0]
        assert saved_tool.enabled is False
        assert saved_tool.config["auth_type"] == "bearer"
        assert "credential_ref" in saved_tool.config

    @pytest.mark.asyncio
    async def test_commit_unknown_operation_fails(self):
        source_repo = _mock_source_repo()
        tool_repo = _mock_tool_repo()

        with patch("moira.services.tool_ingestion.tool_provisioner.service_provider") as mock_sp:
            mock_sp.return_value = None
            provisioner = ToolProvisioner(source_repo=source_repo, tool_repo=tool_repo)

            result = await provisioner.commit(
                source_id="src-789",
                base_url="https://api.example.com",
                spec_url=None,
                spec_format="openapi_3_0",
                group_name="Test API",
                auth_type=None,
                selected_operations=["nonexistent_tool"],
                operations_data=_sample_operations(),
                server_url="https://api.example.com",
            )

        assert result.succeeded == []
        assert len(result.failed) == 1
        assert result.failed[0]["name"] == "nonexistent_tool"

    @pytest.mark.asyncio
    async def test_commit_exceeds_max_operations(self):
        source_repo = _mock_source_repo()
        tool_repo = _mock_tool_repo()
        provisioner = ToolProvisioner(source_repo=source_repo, tool_repo=tool_repo)

        with pytest.raises(ValueError, match="Too many operations"):
            await provisioner.commit(
                source_id="src-max",
                base_url="https://api.example.com",
                spec_url=None,
                spec_format="openapi_3_0",
                group_name="Big API",
                auth_type=None,
                selected_operations=["op"] * 201,
                operations_data=[],
                server_url="https://api.example.com",
            )

    @pytest.mark.asyncio
    async def test_commit_tool_definition_fields(self):
        """Verify the generated ToolDefinition has correct fields."""
        source_repo = _mock_source_repo()
        tool_repo = _mock_tool_repo()

        with patch("moira.services.tool_ingestion.tool_provisioner.service_provider") as mock_sp:
            mock_sp.return_value = None
            provisioner = ToolProvisioner(source_repo=source_repo, tool_repo=tool_repo)

            await provisioner.commit(
                source_id="src-fields",
                base_url="https://api.example.com",
                spec_url=None,
                spec_format="openapi_3_0",
                group_name="Fields API",
                auth_type=None,
                selected_operations=["test_api__get_item"],
                operations_data=_sample_operations(),
                server_url="https://api.example.com/v2",
            )

        saved_tool: ToolDefinition = tool_repo.save_tool.call_args[0][0]
        assert saved_tool.name == "test_api__get_item"
        assert saved_tool.implementation == REST_TOOL_IMPL
        assert saved_tool.config["base_url"] == "https://api.example.com/v2"
        assert saved_tool.config["endpoint"] == "/items/{id}"
        assert saved_tool.config["method"] == "GET"
        assert saved_tool.config["source_id"] == "src-fields"
        assert saved_tool.config["operation_fingerprint"] == "GET:/items/{id}"
        assert saved_tool.built_in is False
        assert "id" in saved_tool.argument_schema["properties"]
        assert saved_tool.argument_schema["properties"]["id"]["x-parameter-location"] == "path"


class TestToolProvisionerDelete:
    """Tests for deleting an API source and its tools."""

    @pytest.mark.asyncio
    async def test_delete_source_removes_tools(self):
        source_repo = _mock_source_repo()
        source_repo.get = AsyncMock(
            return_value=ApiSource(
                id="src-del",
                name="Delete API",
                base_url="https://api.example.com",
                spec_url=None,
                spec_format="openapi_3_0",
                auth_type=None,
                group_name="Delete API",
                tool_count=1,
                enabled=True,
                created_at="2025-01-01T00:00:00",
                updated_at="2025-01-01T00:00:00",
            )
        )
        tool_repo = _mock_tool_repo()
        tool_repo.get_all_tools = AsyncMock(
            return_value=[
                ToolDefinition(
                    name="delete_api__get_items",
                    description="Get items",
                    config={"source_id": "src-del"},
                ),
                ToolDefinition(
                    name="other_api__get_items",
                    description="Other items",
                    config={"source_id": "src-other"},
                ),
            ]
        )

        with patch("moira.services.tool_ingestion.tool_provisioner.service_provider") as mock_sp:
            mock_sp.return_value = None
            provisioner = ToolProvisioner(source_repo=source_repo, tool_repo=tool_repo)
            deleted = await provisioner.delete_source("src-del")

        assert deleted == ["delete_api__get_items"]
        tool_repo.delete_tool.assert_called_once_with("delete_api__get_items")
        source_repo.delete.assert_called_once_with("src-del")

    @pytest.mark.asyncio
    async def test_delete_nonexistent_source_raises(self):
        source_repo = _mock_source_repo()
        source_repo.get = AsyncMock(return_value=None)
        tool_repo = _mock_tool_repo()

        provisioner = ToolProvisioner(source_repo=source_repo, tool_repo=tool_repo)
        with pytest.raises(ValueError, match="not found"):
            await provisioner.delete_source("nonexistent")
