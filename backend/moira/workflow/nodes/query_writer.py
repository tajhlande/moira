"""Delegated query-writer pass (retrieval-quality plan Phase 3).

Query generation leaves the overloaded research prompt: a narrow,
independently-testable pass emits 2-3 register-diverse queries for one
targeted fact. The research loop replaces the model's freeform query
with the writer's first variant and holds the rest as the fan-out list
(Phase 4 consumes them).

The writer is the same workflow model under a cheap dedicated prompt
(plan open question resolved as "separate prompt, same model" — a
smaller model can be selected later via ``research.query_writer_model``).
Output is normalized defensively: the caller never sees a query that is
empty, overlong, duplicated, or a repeat of something already tried, and
any model/parsing failure degrades to an empty list so the original
model query survives.
"""

import logging
from typing import Any

from moira.inference.client import ChatResponse, InferenceClient
from moira.prompts import render_prompt
from moira.workflow.nodes._helpers import _parse_json_object

logger = logging.getLogger(__name__)

# Contract bounds shared with the prompt and the unit tests.
MAX_QUERIES = 3
MAX_QUERY_CHARS = 60
# Deliberately low: query writing should be near-deterministic.
WRITER_TEMPERATURE = 0.2

_KNOWN_REGISTERS = ("technical", "question", "site_scoped")


def _normalize_query(raw: Any) -> str:
    """Collapse whitespace and enforce the length contract."""
    return " ".join(str(raw or "").split())[:MAX_QUERY_CHARS]


def _already_tried_key(query: str) -> str:
    """Comparison key for 'already tried' suppression (case/format blind)."""
    return " ".join(query.lower().split())


def _build_user_prompt(
    fact_needed: str,
    subject: str,
    evidence_needed: str,
    queries_tried: list[str],
    snippets_seen: list[str],
) -> str:
    """Render the writer's user message.

    Kept as plain-text sections (not a Jinja-style template) so the
    context shapes what the model sees: tried queries and snippets are
    optional and omitted entirely when empty rather than rendered as
    placeholder text.
    """
    parts = [f"Fact needed: {fact_needed or '(unspecified)'}"]
    if subject:
        parts.append(f"Subject: {subject}")
    if evidence_needed:
        parts.append(f"Evidence direction: {evidence_needed}")
    if queries_tried:
        tried = "\n".join(f"- {q}" for q in queries_tried[-10:])
        parts.append(f"Queries already tried (do NOT repeat these):\n{tried}")
    if snippets_seen:
        snippets = "\n".join(f"- {s}" for s in snippets_seen[:5])
        parts.append(f"Snippets already seen (borrow their vocabulary):\n{snippets}")
    parts.append("Write the queries now.")
    return "\n\n".join(parts)


def _normalize_response(parsed: dict, queries_tried: list[str]) -> list[dict]:
    """Validate/trim the model's JSON into the [{query, register}] contract.

    Enforces, in order: string cleaning, length cap, dedup within the
    set, suppression of already-tried queries, and the 3-query cap.
    Register labels outside the known vocabulary are kept (lowercased,
    truncated) — the register taxonomy may grow without this module
    silently discarding new labels.
    """
    tried = {_already_tried_key(q) for q in queries_tried if q}
    seen: set[str] = set()
    out: list[dict] = []
    for entry in parsed.get("queries") or []:
        if not isinstance(entry, dict):
            continue
        query = _normalize_query(entry.get("query"))
        if not query:
            continue
        key = _already_tried_key(query)
        if key in tried or key in seen:
            continue
        seen.add(key)
        register = str(entry.get("register") or "technical").lower()[:24]
        out.append({"query": query, "register": register})
        if len(out) >= MAX_QUERIES:
            break
    return out


async def write_queries(
    fact_needed: str,
    subject: str,
    evidence_needed: str,
    queries_tried: list[str],
    snippets_seen: list[str],
    client: InferenceClient,
    model_id: str = "",
    temperature: float = WRITER_TEMPERATURE,
) -> list[dict]:
    """Emit up to 3 register-diverse queries for one needed fact.

    Returns ``[]`` on any failure (malformed JSON, transport error) —
    callers treat that as "keep the model's own query", so the writer
    can only add information, never block a search from happening.
    """
    messages = [
        {"role": "system", "content": render_prompt("query_writer.system")},
        {
            "role": "user",
            "content": _build_user_prompt(
                fact_needed, subject, evidence_needed, queries_tried, snippets_seen
            ),
        },
    ]
    try:
        response: ChatResponse = await client.chat_completion(
            messages=messages,
            model=model_id,
            temperature=temperature,
        )
        parsed = _parse_json_object(response.content or "")
        if not isinstance(parsed, dict):
            return []
        return _normalize_response(parsed, queries_tried)
    except Exception:
        # The writer is an enhancement, never a dependency: log and
        # degrade to "no suggestion" so the research loop is unaffected.
        logger.warning("query writer call failed; keeping model query", exc_info=True)
        return []
