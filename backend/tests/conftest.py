"""Shared test fixtures for node unit tests.

Auto-discovered by pytest for all test files in this directory.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from moira.config import MoiraConfig
from moira.inference.client import ChatResponse, InferenceClient
from moira.inference.registry import ModelRegistry, ResolvedModel


@pytest.fixture
def config():
    return MoiraConfig()


@pytest.fixture
def mock_writer():
    events = []

    def write(event):
        events.append(event)

    with (
        patch("moira.workflow.nodes.decomposition.get_stream_writer", return_value=write),
        patch("moira.workflow.nodes.synthesis.get_stream_writer", return_value=write),
        patch("moira.workflow.nodes.research_review.get_stream_writer", return_value=write),
        patch("moira.workflow.nodes.evaluation.get_stream_writer", return_value=write),
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
