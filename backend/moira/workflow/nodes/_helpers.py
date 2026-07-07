"""Shared utilities for research loop nodes.

This module contains only pure functions with no dependencies on
``langgraph``, ``langchain_core``, or ``moira.inference.client`` (``httpx``). 
Functions that require those dependencies live in ``_helpers_deps``.

This split allows ``moira_eval`` to import the JSON-parsing and
formatting helpers without pulling in the heavy orchestration stack.
"""

import json
import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# JSON repair + parsing
# ---------------------------------------------------------------------------

_VALID_ESCAPES = frozenset('"\\/bfnrtu')


def _fix_json_control_chars(text: str) -> str:
    """Escape literal control characters inside JSON string values.

    Quantized local models sometimes emit raw control bytes *inside* JSON
    string values instead of the proper escape sequences.  Python's
    ``json.loads`` rejects any character in the U+0000–U+001F range that
    appears inside a string (per RFC 8259 § 7).

    This function performs a single-pass scan that tracks whether the cursor
    is inside a JSON string (between unescaped double quotes) and replaces
    any literal control characters found there with their JSON escape-sequence
    equivalents.

    Characters outside strings (structural whitespace between JSON tokens)
    are left untouched — they are valid JSON whitespace.

    On already-valid JSON this is a no-op: properly escaped ``\\n`` sequences
    are seen as ``\\`` + ``n`` (escape mode), not as a literal control char.
    """
    result: list[str] = []
    in_string = False
    escaped = False
    for ch in text:
        if escaped:
            result.append(ch)
            escaped = False
            continue
        if ch == "\\":
            result.append(ch)
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        if in_string:
            # RFC 8259 requires all control chars (U+0000–U+001F) inside
            # strings to be escaped. Use named escapes where JSON defines
            # them; fall back to \uXXXX for the rest.
            code = ord(ch)
            if code == 0x0A:  # line feed
                result.append("\\n")
            elif code == 0x0D:  # carriage return
                result.append("\\r")
            elif code == 0x09:  # tab
                result.append("\\t")
            elif code == 0x08:  # backspace
                result.append("\\b")
            elif code == 0x0C:  # form feed
                result.append("\\f")
            elif code < 0x20:  # other control chars (NUL, VT, etc.)
                result.append(f"\\u{code:04x}")
            else:
                result.append(ch)
        else:
            result.append(ch)
    return "".join(result)


def _fix_invalid_escapes(text: str) -> str:
    r"""Double-escape invalid backslash escapes inside JSON string values.

    JSON (RFC 8259 § 7) permits only these escape sequences inside strings:
    ``\"  \\  \/  \b  \f  \n  \r  \t  \uXXXX``.

    Models frequently emit LaTeX commands (``\sim``, ``\psi``, ``\infty``),
    regex patterns (``\d``, ``\w``), or Windows paths (``\Users``) inside JSON
    string values without escaping the backslash.  Python's ``json.loads``
    rejects any ``\X`` where *X* is not in the allowed set, causing the entire
    object to fail parsing.

    This function scans inside JSON string values and replaces every invalid
    ``\X`` with ``\\X`` (a properly escaped backslash followed by the literal
    character).  Valid escape sequences and structural text outside strings
    are left untouched.

    On already-valid JSON this is a no-op.
    """
    result: list[str] = []
    in_string = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if not in_string:
            result.append(ch)
            if ch == '"':
                in_string = True
            i += 1
            continue
        # Inside a JSON string
        if ch != "\\":
            result.append(ch)
            if ch == '"':
                in_string = False
            i += 1
            continue
        # ch is a backslash inside a string — check what follows
        if i + 1 < n:
            next_ch = text[i + 1]
            if next_ch in _VALID_ESCAPES:
                # Valid escape — pass through both characters unchanged
                result.append(ch)
                result.append(next_ch)
            else:
                # Invalid escape — double-escape the backslash so json.loads
                # interprets it as a literal backslash followed by the char
                result.append("\\\\")
                result.append(next_ch)
            i += 2
        else:
            # Lone trailing backslash — pass through as-is
            result.append(ch)
            i += 1
    return "".join(result)


def _strip_trailing_commas(text: str) -> str:
    """Remove trailing commas before ``}`` or ``]``.

    Local models frequently emit ``{"a": 1,}`` which ``json.loads`` rejects.
    This repair is safe because a comma immediately before a closing brace
    is never valid JSON.
    """
    return re.sub(r",(\s*[}\]])", r"\1", text)


def _fix_double_braces(text: str) -> str:
    """Replace ``{{`` → ``{`` and ``}}`` → ``}`` outside of JSON strings.

    Models sometimes mimic the double-brace escaping used in Python
    ``str.format()`` templates (``{{…}}``) when they see it in prompt
    examples.  This collapses double braces to single braces so the
    resulting JSON is parseable.

    Braces inside JSON string values are left untouched.
    """
    result: list[str] = []
    in_string = False
    escaped = False
    i = 0
    while i < len(text):
        ch = text[i]
        if escaped:
            result.append(ch)
            escaped = False
            i += 1
            continue
        if ch == "\\":
            result.append(ch)
            escaped = True
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            i += 1
            continue
        if not in_string:
            if ch == "{" and i + 1 < len(text) and text[i + 1] == "{":
                result.append("{")
                i += 2
                continue
            if ch == "}" and i + 1 < len(text) and text[i + 1] == "}":
                result.append("}")
                i += 2
                continue
        result.append(ch)
        i += 1
    return "".join(result)


def _extract_balanced_braces(text: str) -> str | None:
    """Find the outermost balanced ``{ ... }`` in *text* using depth counting.

    Unlike a regex, this handles arbitrary nesting depth and correctly
    ignores braces that appear inside JSON string values.  Returns ``None``
    when no balanced object is found.

    If multiple top-level objects exist, the **longest** one is returned —
    models sometimes emit a small fragment (e.g. ``{"query": "..."}``)
    before the real response object.
    """
    candidates: list[str] = []
    i = 0
    length = len(text)
    while i < length:
        # Find the next opening brace
        start = text.find("{", i)
        if start == -1:
            break
        depth = 0
        in_string = False
        escaped = False
        end = -1
        for j in range(start, length):
            ch = text[j]
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = j
                    break
        if end != -1:
            candidates.append(text[start : end + 1])
            i = end + 1
        else:
            # Unbalanced — no point searching further from this start
            break
    if not candidates:
        return None
    # Return the longest candidate; models sometimes prepend a small
    # JSON fragment before the real response.
    return max(candidates, key=len)


def _try_parse_json(text: str) -> dict | None:
    """Attempt ``json.loads``, then retry with common model-output repairs:
    trailing-comma removal and double-brace collapsing.
    """
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass
    # Apply repairs incrementally so we can see which one helped
    for repair in (_strip_trailing_commas, _fix_double_braces, _fix_invalid_escapes):
        repaired = repair(text)
        if repaired != text:
            try:
                result = json.loads(repaired)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass
    # Apply all repairs together as a last resort
    repaired = _fix_invalid_escapes(_fix_double_braces(_strip_trailing_commas(text)))
    if repaired != text:
        try:
            result = json.loads(repaired)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
    return None


def _parse_json_object(text: str) -> dict:
    """Extract the first JSON object from model output.

    Applies a multi-strategy pipeline:

    1. Strip ``<think>`` blocks and repair literal control characters.
    2. Direct ``json.loads`` on the full text (handles clean responses).
    3. Markdown-fence extraction (``\\`\\`\\`json ... \\`\\`\\``).
    4. **Balanced-brace extraction** — a depth-counting scan that finds the
       outermost ``{ ... }`` while ignoring braces inside string values.
       This replaces a regex that could only handle one level of nesting.
    5. Trailing-comma repair applied to each candidate.

    Returns ``{}`` when all strategies fail.
    """
    text = text.strip()
    # Strip <think>...</think> blocks and standalone closing tags
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"</think>", "", text).strip()
    # Repair literal control characters inside JSON string values.
    # No-op on valid JSON; fixes broken JSON from quantized models.
    text = _fix_json_control_chars(text)
    # Repair invalid escape sequences (e.g. \sim, \psi from LaTeX).
    # No-op on valid JSON; fixes broken JSON from models that forget to
    # double-escape backslashes inside string values.
    text = _fix_invalid_escapes(text)

    # Strategy 1: direct parse
    parsed = _try_parse_json(text)
    if parsed is not None:
        return parsed

    # Strategy 2: markdown fence
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        parsed = _try_parse_json(fenced.group(1).strip())
        if parsed is not None:
            return parsed

    # Strategy 3: balanced-brace extraction (handles deep nesting and
    # braces inside string values — the regex fallback could do neither).
    extracted = _extract_balanced_braces(text)
    if extracted:
        parsed = _try_parse_json(extracted)
        if parsed is not None:
            return parsed

    logger.warning(
        "Failed to extract JSON object from model output "
        "(len=%d, first 200 chars: %s, last 200 chars: %s)",
        len(text),
        text[:200],
        text[-200:],
    )
    return {}


# ---------------------------------------------------------------------------
# Response metadata
# ---------------------------------------------------------------------------


def _response_meta(response) -> dict:
    """Extract inference metadata from a ChatResponse for node_end payload.

    Captures token counts and timing so they get persisted to the
    workflow_steps row via the run_manager event handler.
    """
    return {
        "input_tokens": getattr(response, "input_tokens", None),
        "output_tokens": getattr(response, "output_tokens", None),
        "thinking_tokens": getattr(response, "thinking_tokens", None),
        "prompt_time_ms": getattr(response, "prompt_time_ms", None),
        "gen_time_ms": getattr(response, "gen_time_ms", None),
    }


# ---------------------------------------------------------------------------
# Citation content formatting (shared by evaluation and research_review)
# ---------------------------------------------------------------------------

_SNIPPET_MAX_LENGTH = 500
_PER_CITATION_CONTENT_CAP = 2000
_TOTAL_CITATION_CONTENT_CAP = 32000


def _format_citation_content(citations: list, conclusions: list, facts: list) -> str:
    """Format citation source content for cross-referencing in prompts.

    Applies a per-citation cap and a total budget.  Citations referenced by
    the conclusions under evaluation (traced via conclusion → fact → citation)
    are prioritized; unreferenced citations are included only when budget
    remains.  Citations without ``content`` are skipped silently.
    """
    # Trace conclusion → supporting_fact_ids → fact → citation_ids to find
    # which citations are directly relevant to the conclusions being judged.
    fact_by_id = {f["id"]: f for f in facts}
    referenced_cit_ids: set[str] = set()
    for c in conclusions:
        for fid in c.get("supporting_fact_ids", []):
            fact = fact_by_id.get(fid)
            if fact:
                referenced_cit_ids.update(fact.get("citation_ids", []))

    # Partition into referenced-first, preserving citation list order.
    prioritized: list = []
    for cit in citations:
        if cit["id"] in referenced_cit_ids and cit.get("content"):
            prioritized.append(cit)
    for cit in citations:
        if cit["id"] not in referenced_cit_ids and cit.get("content"):
            prioritized.append(cit)

    lines: list[str] = []
    total = 0
    for cit in prioritized:
        if total >= _TOTAL_CITATION_CONTENT_CAP:
            break
        content = cit["content"][:_PER_CITATION_CONTENT_CAP]
        budget_left = _TOTAL_CITATION_CONTENT_CAP - total
        if len(content) > budget_left:
            content = content[:budget_left]
        lines.append(
            f"[{cit['id']}] {cit['source']} | {cit.get('url') or ''} | "
            f"{cit.get('title') or ''}\n{content}"
        )
        total += len(content)

    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Retry context formatting (shared by planning and research)
# ---------------------------------------------------------------------------


def _format_established_facts(facts: list) -> str:
    """Format verified facts with their claims for retry context.

    Only facts with status 'verified' and a non-empty claim are included,
    so the model sees what has already been established and can avoid
    re-discovering it.
    """
    lines = []
    for f in facts:
        if f.get("status") == "verified" and f.get("claim"):
            cit_ids = ", ".join(f.get("citation_ids") or [])
            claim = (f["claim"] or "")[:200]
            lines.append(f"{f['id']} | {f.get('subject', '')} | {claim} | [{cit_ids}]")
    return "\n".join(lines)


def _format_prior_conclusions(conclusions: list) -> str:
    """Format conclusions compactly for retry context."""
    lines = []
    for c in conclusions:
        facts_str = ", ".join(c.get("supporting_fact_ids") or [])
        conclusion = (c.get("conclusion") or "")[:200]
        lines.append(f"{c['id']} | {conclusion} | [{facts_str}] | {c.get('status', '')}")
    return "\n".join(lines)


def _format_prior_citations(citations: list) -> str:
    """Format a compact citation list (ID, title, URL) for retry context.

    Omits snippet/content to keep the prompt lean — the model just needs
    to know which sources were already consulted.
    """
    lines = []
    for c in citations:
        lines.append(f"{c['id']} | {c.get('title') or ''} | {c.get('url') or ''}")
    return "\n".join(lines)
