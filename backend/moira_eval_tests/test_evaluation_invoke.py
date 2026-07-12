"""Tests for moira_eval.invoke — triggering workflow runs via API.

Tests:
- create_conversation sends POST and extracts ID
- send_message sends POST with content and extracts run_id
- poll_until_done returns terminal status
- poll_until_done returns "timeout" on deadline
- invoke_question orchestrates the full flow (always polls)
- invoke_question with budget passes settings
- invoke_question predefined sets title
- invoke_question adhoc generates title with Eval: prefix
- invoke_question adhoc title failure fallback
- CLI --question resolves and invokes
- CLI --text invokes custom text
- CLI --all invokes all predefined questions
- CLI timeout aborts immediately (no subsequent questions)
- CLI run error aborts immediately
- CLI with no args errors
- HTTP errors are caught and reported
"""

import sys
from unittest.mock import MagicMock, patch

import httpx
import pytest

from moira_eval.invoke import (
    _generate_title,
    create_conversation,
    invoke_question,
    poll_until_done,
    send_message,
    set_title,
)
from moira_eval.questions import QUESTIONS

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _mock_response(*, status_code=200, json_data=None):
    """Build a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    resp.text = str(json_data or {})
    return resp


def _completed_poll(run_id: str = "run-1"):
    """A GET response showing the run as completed."""
    return _mock_response(json_data={"runs": [{"id": run_id, "status": "completed"}]})


@pytest.fixture
def mock_client():
    """A mock httpx.Client."""
    return MagicMock(spec=httpx.Client)


# ---------------------------------------------------------------------------
# create_conversation
# ---------------------------------------------------------------------------


def test_create_conversation_returns_id(mock_client):
    mock_client.post.return_value = _mock_response(json_data={"id": "conv-123", "title": "Test"})
    result = create_conversation("http://localhost:8000", mock_client)
    assert result == "conv-123"
    mock_client.post.assert_called_once_with("http://localhost:8000/api/conversations")


def test_create_conversation_raises_on_error(mock_client):
    mock_client.post.return_value = _mock_response(status_code=500)
    with pytest.raises(httpx.HTTPStatusError):
        create_conversation("http://localhost:8000", mock_client)


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


def test_send_message_returns_run_id(mock_client):
    mock_client.post.return_value = _mock_response(
        json_data={"run_id": "run-456", "user_message_id": 1}
    )
    result = send_message("http://localhost:8000", "conv-123", "Hello?", mock_client)
    assert result == "run-456"

    call_args = mock_client.post.call_args
    assert call_args[0][0] == "http://localhost:8000/api/conversations/conv-123/messages"
    assert call_args[1]["json"]["content"] == "Hello?"
    assert "settings" not in call_args[1]["json"]


def test_send_message_with_budget(mock_client):
    mock_client.post.return_value = _mock_response(json_data={"run_id": "run-789"})
    send_message("http://localhost:8000", "conv-123", "Hello?", mock_client, budget=80)
    call_args = mock_client.post.call_args
    assert call_args[1]["json"]["settings"] == {"budget": 80}


# ---------------------------------------------------------------------------
# poll_until_done
# ---------------------------------------------------------------------------


def test_poll_returns_completed(mock_client):
    """Poll finds the run already completed on first check."""
    mock_client.get.return_value = _completed_poll()
    status = poll_until_done("http://localhost:8000", "conv-1", "run-1", mock_client, timeout=5)
    assert status == "completed"


def test_poll_returns_error(mock_client):
    mock_client.get.return_value = _mock_response(
        json_data={"runs": [{"id": "run-1", "status": "error"}]}
    )
    status = poll_until_done("http://localhost:8000", "conv-1", "run-1", mock_client, timeout=5)
    assert status == "error"


def test_poll_returns_timeout(mock_client):
    """Run never reaches terminal status within deadline."""
    mock_client.get.return_value = _mock_response(
        json_data={"runs": [{"id": "run-1", "status": "running"}]}
    )
    status = poll_until_done("http://localhost:8000", "conv-1", "run-1", mock_client, timeout=1)
    assert status == "timeout"


def test_poll_finds_correct_run_among_many(mock_client):
    """Poll finds the right run when multiple runs exist."""
    mock_client.get.return_value = _mock_response(
        json_data={
            "runs": [
                {"id": "run-old", "status": "completed"},
                {"id": "run-target", "status": "completed"},
            ]
        }
    )
    status = poll_until_done(
        "http://localhost:8000", "conv-1", "run-target", mock_client, timeout=5
    )
    assert status == "completed"


# ---------------------------------------------------------------------------
# set_title
# ---------------------------------------------------------------------------


def test_set_title_calls_patch(mock_client):
    """set_title sends a PATCH with the title body."""
    mock_client.patch.return_value = _mock_response(
        json_data={"id": "conv-1", "title": "Eval: test-q"}
    )
    set_title("http://localhost:8000", "conv-1", "Eval: test-q", mock_client)
    call_args = mock_client.patch.call_args
    assert call_args[0][0] == "http://localhost:8000/api/conversations/conv-1"
    assert call_args[1]["json"]["title"] == "Eval: test-q"


def test_set_title_non_fatal_on_error(mock_client):
    """set_title does not raise on HTTP error."""
    mock_client.patch.return_value = _mock_response(status_code=500)
    # Should not raise
    set_title("http://localhost:8000", "conv-1", "Eval: test-q", mock_client)


# ---------------------------------------------------------------------------
# _generate_title
# ---------------------------------------------------------------------------


def test_generate_title_returns_title(mock_client):
    mock_client.post.return_value = _mock_response(
        json_data={"id": "conv-1", "title": "Capital of France"}
    )
    result = _generate_title("http://localhost:8000", "conv-1", mock_client)
    assert result == "Capital of France"


def test_generate_title_returns_none_on_error(mock_client):
    mock_client.post.return_value = _mock_response(status_code=502)
    result = _generate_title("http://localhost:8000", "conv-1", mock_client)
    assert result is None


# ---------------------------------------------------------------------------
# invoke_question (orchestration — always polls)
# ---------------------------------------------------------------------------


def test_invoke_question_completed(mock_client):
    """invoke_question polls and returns 'completed'."""
    mock_client.post.side_effect = [
        _mock_response(json_data={"id": "conv-1"}),
        _mock_response(json_data={"run_id": "run-1"}),
    ]
    mock_client.patch.return_value = _mock_response(
        json_data={"id": "conv-1", "title": "Eval: test-q"}
    )
    mock_client.get.return_value = _completed_poll()
    result = invoke_question("http://localhost:8000", "test-q", "What is?", mock_client, timeout=5)
    assert result["question_id"] == "test-q"
    assert result["run_id"] == "run-1"
    assert result["conversation_id"] == "conv-1"
    assert result["status"] == "completed"


def test_invoke_question_with_budget(mock_client):
    mock_client.post.side_effect = [
        _mock_response(json_data={"id": "conv-1"}),
        _mock_response(json_data={"run_id": "run-1"}),
    ]
    mock_client.patch.return_value = _mock_response(
        json_data={"id": "conv-1", "title": "Eval: test-q"}
    )
    mock_client.get.return_value = _completed_poll()
    invoke_question(
        "http://localhost:8000",
        "test-q",
        "What is?",
        mock_client,
        budget=80,
        timeout=5,
    )
    # Find the send_message POST call (has "content" in json body)
    post_calls = [c for c in mock_client.post.call_args_list]
    msg_call = next(
        c for c in post_calls if "json" in (c.kwargs or {}) and "content" in c.kwargs["json"]
    )
    assert msg_call.kwargs["json"]["settings"] == {"budget": 80}


def test_invoke_question_predefined_sets_title(mock_client):
    """Predefined question gets 'Eval: <question_id>' title after creation."""
    mock_client.post.side_effect = [
        _mock_response(json_data={"id": "conv-1"}),
        _mock_response(json_data={"run_id": "run-1"}),
    ]
    mock_client.patch.return_value = _mock_response(
        json_data={"id": "conv-1", "title": "Eval: test-q"}
    )
    mock_client.get.return_value = _completed_poll()
    invoke_question("http://localhost:8000", "test-q", "What is?", mock_client, timeout=5)

    patch_call = mock_client.patch.call_args
    assert patch_call[1]["json"]["title"] == "Eval: test-q"


def test_invoke_question_adhoc_generates_title(mock_client):
    """Adhoc text gets generated title prefixed with 'Eval: '."""
    # Adhoc flow: POST create → POST send_message → POST generate-title →
    # PATCH set_title → GET poll
    mock_client.post.side_effect = [
        _mock_response(json_data={"id": "conv-1"}),
        _mock_response(json_data={"run_id": "run-1"}),
        _mock_response(json_data={"title": "Airspeed of Swallow"}),
    ]
    mock_client.patch.return_value = _mock_response(
        json_data={"id": "conv-1", "title": "Eval: Airspeed of Swallow"}
    )
    mock_client.get.return_value = _completed_poll()

    result = invoke_question(
        "http://localhost:8000",
        "adhoc",
        "What is the airspeed?",
        mock_client,
        timeout=5,
    )

    assert result["run_id"] == "run-1"
    assert result["status"] == "completed"
    # Verify generate-title was called
    gen_title_call = mock_client.post.call_args_list[2]
    assert "/generate-title" in gen_title_call[0][0]
    # Verify PATCH was called with "Eval: " prefix
    patch_call = mock_client.patch.call_args
    assert patch_call[1]["json"]["title"] == "Eval: Airspeed of Swallow"


def test_invoke_question_adhoc_title_failure_fallback(mock_client):
    """Adhoc falls back to 'Eval: (adhoc)' when title generation fails."""
    mock_client.post.side_effect = [
        _mock_response(json_data={"id": "conv-1"}),
        _mock_response(json_data={"run_id": "run-1"}),
        _mock_response(status_code=502),  # generate-title fails
    ]
    mock_client.patch.return_value = _mock_response(
        json_data={"id": "conv-1", "title": "Eval: (adhoc)"}
    )
    mock_client.get.return_value = _completed_poll()

    result = invoke_question("http://localhost:8000", "adhoc", "What is?", mock_client, timeout=5)

    assert result["run_id"] == "run-1"
    patch_call = mock_client.patch.call_args
    assert patch_call[1]["json"]["title"] == "Eval: (adhoc)"


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


def test_cli_no_args_errors():
    """Running with no --question, --text, or --all exits with error."""
    with patch.object(sys, "argv", ["invoke"]):
        with pytest.raises(SystemExit):
            from moira_eval.invoke import main

            main()


def test_cli_unknown_question_errors():
    with patch.object(sys, "argv", ["invoke", "--question", "nonexistent"]):
        with pytest.raises(SystemExit):
            from moira_eval.invoke import main

            main()


def test_cli_question_invokes(mock_client):
    """CLI --question resolves the question and invokes it."""
    with (
        patch.object(
            sys,
            "argv",
            ["invoke", "--question", "tyranitar-ou", "--timeout", "5"],
        ),
        patch("moira_eval.invoke.httpx.Client") as mock_client_cls,
    ):
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_client.post.side_effect = [
            _mock_response(json_data={"id": "conv-1"}),
            _mock_response(json_data={"run_id": "run-1"}),
        ]
        mock_client.patch.return_value = _mock_response(
            json_data={"id": "conv-1", "title": "Eval: tyranitar-ou"}
        )
        mock_client.get.return_value = _completed_poll()

        from moira_eval.invoke import main

        main()  # should not raise

        # Verify the question text was used in send_message
        post_calls = mock_client.post.call_args_list
        msg_call = next(
            c for c in post_calls if "json" in (c.kwargs or {}) and "content" in c.kwargs["json"]
        )
        assert msg_call.kwargs["json"]["content"] == QUESTIONS["tyranitar-ou"].text


def test_cli_text_invokes(mock_client):
    """CLI --text invokes with custom text."""
    with (
        patch.object(
            sys,
            "argv",
            ["invoke", "--text", "What is 2+2?", "--timeout", "5"],
        ),
        patch("moira_eval.invoke.httpx.Client") as mock_client_cls,
    ):
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        # Adhoc flow: create → send_message → generate-title
        mock_client.post.side_effect = [
            _mock_response(json_data={"id": "conv-1"}),
            _mock_response(json_data={"run_id": "run-1"}),
            _mock_response(json_data={"title": "Math Question"}),
        ]
        mock_client.patch.return_value = _mock_response(
            json_data={"id": "conv-1", "title": "Eval: Math Question"}
        )
        mock_client.get.return_value = _completed_poll()

        from moira_eval.invoke import main

        main()

        # Verify the question text was used in send_message
        post_calls = mock_client.post.call_args_list
        msg_call = next(
            c for c in post_calls if "json" in (c.kwargs or {}) and "content" in c.kwargs["json"]
        )
        assert msg_call.kwargs["json"]["content"] == "What is 2+2?"


def test_cli_all_invokes_all_questions(mock_client):
    """CLI --all invokes all predefined questions serially."""
    with (
        patch.object(sys, "argv", ["invoke", "--all", "--timeout", "5"]),
        patch("moira_eval.invoke.httpx.Client") as mock_client_cls,
    ):
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        # Each predefined question needs: POST create + POST send
        num_questions = len(QUESTIONS)
        mock_client.post.side_effect = [
            resp
            for i in range(num_questions)
            for resp in [
                _mock_response(json_data={"id": f"conv-{i}"}),
                _mock_response(json_data={"run_id": f"run-{i}"}),
            ]
        ]
        mock_client.patch.return_value = _mock_response(json_data={"id": "x", "title": "x"})
        # Include all run IDs so poll_until_done finds whichever it seeks
        all_runs = [{"id": f"run-{i}", "status": "completed"} for i in range(num_questions)]
        mock_client.get.return_value = _mock_response(json_data={"runs": all_runs})

        from moira_eval.invoke import main

        main()

        assert mock_client.post.call_count == num_questions * 2
        assert mock_client.patch.call_count == num_questions


def test_cli_timeout_aborts(mock_client):
    """On timeout, CLI aborts immediately — subsequent questions are NOT invoked."""
    with (
        patch.object(
            sys,
            "argv",
            ["invoke", "--all", "--timeout", "1"],
        ),
        patch("moira_eval.invoke.httpx.Client") as mock_client_cls,
    ):
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        # Only set up POST for the first question — second should never be reached
        mock_client.post.side_effect = [
            _mock_response(json_data={"id": "conv-0"}),
            _mock_response(json_data={"run_id": "run-0"}),
        ]
        mock_client.patch.return_value = _mock_response(json_data={"id": "x"})
        # Poll never sees completed status → timeout
        mock_client.get.return_value = _mock_response(
            json_data={"runs": [{"id": "run-0", "status": "running"}]}
        )

        from moira_eval.invoke import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

        # Only 2 POSTs (create + send for first question), not more
        assert mock_client.post.call_count == 2


def test_cli_run_error_aborts(mock_client):
    """When a run completes with 'error' status, CLI aborts immediately."""
    with (
        patch.object(
            sys,
            "argv",
            ["invoke", "--all", "--timeout", "5"],
        ),
        patch("moira_eval.invoke.httpx.Client") as mock_client_cls,
    ):
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        # Only first question's POSTs
        mock_client.post.side_effect = [
            _mock_response(json_data={"id": "conv-0"}),
            _mock_response(json_data={"run_id": "run-0"}),
        ]
        mock_client.patch.return_value = _mock_response(json_data={"id": "x"})
        # Poll sees error status
        mock_client.get.return_value = _mock_response(
            json_data={"runs": [{"id": "run-0", "status": "error"}]}
        )

        from moira_eval.invoke import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

        # Only 2 POSTs — second question never started
        assert mock_client.post.call_count == 2


def test_cli_http_error_aborts(mock_client):
    """HTTP errors are caught and abort the command."""
    with (
        patch.object(
            sys,
            "argv",
            ["invoke", "--text", "What is?"],
        ),
        patch("moira_eval.invoke.httpx.Client") as mock_client_cls,
    ):
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        error_resp = MagicMock(spec=httpx.Response)
        error_resp.status_code = 503
        error_resp.text = "Service Unavailable"
        error_resp.json.return_value = {}
        error_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=error_resp
        )
        mock_client.post.return_value = error_resp

        from moira_eval.invoke import main

        # Should exit 1, not raise
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
