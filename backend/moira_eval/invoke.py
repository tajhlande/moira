"""Trigger workflow runs programmatically without the UI.

Creates a conversation, sends the question as a message, and polls
until the run completes (or the per-question timeout expires).  Each
question runs to completion before the next one starts — invoke is
strictly serial to avoid overloading the model server with concurrent
inference requests.

Does NOT score — that's ``run.py``'s job.

Usage::

    # single predefined question
    uv run python -m moira_eval.invoke \\
        --question tyranitar-ou

    # all predefined questions sequentially
    uv run python -m moira_eval.invoke --all

    # custom text
    uv run python -m moira_eval.invoke \\
        --text "What is the capital of France?"
"""

import argparse
import sys
import time

import httpx

from moira_eval.questions import QUESTIONS, get_question

_DEFAULT_BASE_URL = "http://localhost:8000"
_DEFAULT_TIMEOUT = 900  # 12 minutes per question
_POLL_INTERVAL = 3  # seconds between status polls


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def create_conversation(base_url: str, client: httpx.Client) -> str:
    """Create a new conversation and return its ID."""
    resp = client.post(f"{base_url}/api/conversations")
    resp.raise_for_status()
    return resp.json()["id"]


def send_message(
    base_url: str,
    conversation_id: str,
    content: str,
    client: httpx.Client,
    budget: int | None = None,
) -> str:
    """Send a user message that starts a workflow run.

    Returns the ``run_id``.
    """
    body: dict = {"content": content}
    if budget is not None:
        body["settings"] = {"budget": budget}

    resp = client.post(
        f"{base_url}/api/conversations/{conversation_id}/messages",
        json=body,
    )
    resp.raise_for_status()
    return resp.json()["run_id"]


def set_title(
    base_url: str,
    conversation_id: str,
    title: str,
    client: httpx.Client,
) -> None:
    """Set a conversation's title via PATCH.

    Non-fatal: logs a warning on error but does not raise, since the
    title is cosmetic and should not block the run.
    """
    try:
        resp = client.patch(
            f"{base_url}/api/conversations/{conversation_id}",
            json={"title": title},
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        print(
            f"  WARNING: failed to set title '{title}': {exc}",
            file=sys.stderr,
        )


def _generate_title(
    base_url: str,
    conversation_id: str,
    client: httpx.Client,
) -> str | None:
    """Call the task-model title generation endpoint.

    Returns the generated title string, or ``None`` if generation fails.
    The caller is responsible for prefixing with "Eval: ".
    """
    try:
        resp = client.post(
            f"{base_url}/api/conversations/{conversation_id}/generate-title",
        )
        resp.raise_for_status()
        return resp.json().get("title")
    except httpx.HTTPError as exc:
        print(f"  WARNING: title generation failed: {exc}", file=sys.stderr)
        return None


def poll_until_done(
    base_url: str,
    conversation_id: str,
    run_id: str,
    client: httpx.Client,
    timeout: int = _DEFAULT_TIMEOUT,
) -> str:
    """Poll the conversation until the run reaches a terminal status.

    Returns one of ``"completed"``, ``"error"``, ``"stopped"``, or
    ``"timeout"``.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"{base_url}/api/conversations/{conversation_id}")
        resp.raise_for_status()
        data = resp.json()
        for run in data.get("runs", []):
            if run.get("id") == run_id:
                status = run.get("status", "")
                if status in ("completed", "error", "stopped"):
                    return status
        time.sleep(_POLL_INTERVAL)
    return "timeout"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def invoke_question(
    base_url: str,
    question_id: str,
    question_text: str,
    client: httpx.Client,
    timeout: int = _DEFAULT_TIMEOUT,
    budget: int | None = None,
) -> dict:
    """Invoke a single question and poll until it completes.

    Returns a dict with ``question_id``, ``run_id``, ``conversation_id``,
    and ``status`` (one of ``"completed"``, ``"error"``, ``"stopped"``,
    or ``"timeout"``).
    """
    conv_id = create_conversation(base_url, client)

    # Set the conversation title.
    if question_id == "adhoc":
        # For adhoc text, generate a title from the message content after
        # sending it (the generate-title endpoint needs a user message).
        run_id = send_message(base_url, conv_id, question_text, client, budget)
        generated = _generate_title(base_url, conv_id, client)
        title = f"Eval: {generated}" if generated else "Eval: (adhoc)"
        set_title(base_url, conv_id, title, client)
    else:
        # For predefined questions, the title is deterministic.
        set_title(base_url, conv_id, f"Eval: {question_id}", client)
        run_id = send_message(base_url, conv_id, question_text, client, budget)

    status = poll_until_done(base_url, conv_id, run_id, client, timeout)

    return {
        "question_id": question_id,
        "run_id": run_id,
        "conversation_id": conv_id,
        "status": status,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trigger MOiRA workflow runs without the UI.",
    )
    parser.add_argument(
        "--base-url",
        default=_DEFAULT_BASE_URL,
        help=f"MOiRA API base URL (default: {_DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--question",
        default=None,
        help="Predefined question ID (e.g., tyranitar-ou).",
    )
    parser.add_argument(
        "--text",
        default=None,
        help="Custom question text (mutually exclusive with --question).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Invoke all predefined questions sequentially.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=_DEFAULT_TIMEOUT,
        help=f"Per-question timeout in seconds (default: {_DEFAULT_TIMEOUT}).",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=None,
        help="Budget override for the run (clamped to 35-300 by the API).",
    )
    args = parser.parse_args()

    # Determine which questions to invoke.
    if args.all:
        questions = [(qid, q.text) for qid, q in QUESTIONS.items()]
    elif args.question:
        q = get_question(args.question)
        if q is None:
            available = ", ".join(sorted(QUESTIONS.keys()))
            print(
                f"Unknown question '{args.question}'. Available: {available}",
                file=sys.stderr,
            )
            sys.exit(1)
        questions = [(q.id, q.text)]
    elif args.text:
        questions = [("adhoc", args.text)]
    else:
        parser.error("Provide --question, --text, or --all.")

    # Invoke each question serially. Continue past failures so a single
    # transient error (bad JSON, model timeout) doesn't waste the entire
    # batch. A summary is printed at the end; the process exits non-zero
    # if any questions failed.
    succeeded = []
    failed = []
    with httpx.Client(timeout=httpx.Timeout(60.0)) as client:
        for qid, qtext in questions:
            print(f"Invoking {qid}...", file=sys.stderr)
            try:
                result = invoke_question(
                    args.base_url,
                    qid,
                    qtext,
                    client,
                    timeout=args.timeout,
                    budget=args.budget,
                )
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                body = exc.response.text[:200]
                print(f"  ERROR ({status_code}): {body}", file=sys.stderr)
                failed.append((qid, f"HTTP {status_code}: {body}"))
                continue
            except httpx.HTTPError as exc:
                print(f"  ERROR: {exc}", file=sys.stderr)
                failed.append((qid, str(exc)))
                continue
            except Exception as exc:
                print(f"  ERROR: {exc}", file=sys.stderr)
                failed.append((qid, str(exc)))
                continue

            status_str = result["status"]
            run_str = result["run_id"][:12] if result["run_id"] else "N/A"

            if status_str != "completed":
                print(
                    f"  ERROR: run for {qid} ended with status '{status_str}'.",
                    file=sys.stderr,
                )
                failed.append((qid, f"status={status_str}"))
                continue

            print(f"{qid:30s}  run={run_str}  status={status_str}")
            succeeded.append((qid, result["run_id"]))

    print(file=sys.stderr)
    print(f"Completed: {len(succeeded)}/{len(questions)}", file=sys.stderr)
    if failed:
        print("Failed:", file=sys.stderr)
        for qid, reason in failed:
            print(f"  {qid}: {reason}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
