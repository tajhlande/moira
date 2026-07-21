# recall_source Tool

## Overview

A built-in tool that lets the research model re-read the full stored content of
previously-fetched sources (citations) without re-fetching URLs. Currently,
prior tool-result content is stored in `Citation.content` (up to 5000 chars)
and `Citation.snippets`, but only the reviewer and evaluator nodes can read
it. The planning and research models on retry see only citation IDs, titles,
and URLs — not the actual content.

This creates a blind spot where the model re-fetches URLs it already
consulted (getting deduped or "Loading..." for JS SPAs) instead of re-reading
the content that's already stored and accessible.

## Problem

In retry cycles (`research_review` route=retry -> planning -> research), the
research model starts with a fresh messages list. It sees:

- Established fact claims (up to 200 chars each) with citation IDs
- Citation IDs / titles / URLs only (content deliberately stripped)
- Reviewer coverage assessment + missing areas

It does **not** see:

- Search snippets from prior rounds
- Page content from prior `url_content` fetches

When the model tries to re-fetch a URL, it gets a 500-char excerpt from the
dedup synthetic result — not the full 5000 chars stored in
`Citation.content`.

## Design

- **Always available** as a default built-in tool (`is_default=True`), same
  as `web_search`, `url_content`, `calculator`, etc. No conditional
  presentation — the model learns to use it when it has citations to recall.
- **Interception pattern**: follow the `url_content` dedup precedent — the
  research loop intercepts `recall_source` calls before they reach the
  executor, synthesizes results from the in-scope `citations` list.
- **Free** (`invocation_cost=0.0`, `metadata["synthetic"]=True`) — reading
  existing state costs nothing. Call limits prevent overuse.
- **No duplicate citations**: `recall_source` results skip citation creation
  in `_process_execution_results` — the citations already exist.

## Phase Tracking

| Phase | Description                                            | Status      |
|-------|--------------------------------------------------------|-------------|
| 1     | Tool class and registration                            | Complete    |
| 2     | Research loop interception                             | Complete    |
| 3     | Skip citation creation in `_process_execution_results` | Complete    |
| 4     | Tests                                                  | Complete    |

## Phased Implementation

### Phase 1: Tool class and registration

**Files:**
- `backend/moira/tools/builtin/recall_source.py` (new)
- `backend/moira/tools/standard.py` (modify — add to `STANDARD_TOOLS`)

**Tool class:**

```python
class RecallSourceTool(BaseTool):
    tool_name = "recall_source"
    tool_description = (
        "Recall the full stored content of a previously-fetched source by "
        "citation ID. Use this to re-examine evidence from sources already "
        "consulted in prior research rounds, without re-fetching the URL. "
        "Check the 'Sources already consulted' section in your context for "
        "available citation IDs."
    )
    tool_group = "standard"
    tool_argument_schema = {
        "type": "object",
        "properties": {
            "citation_id": {
                "type": "string",
                "description": "The citation ID to recall (e.g., 'cit004')",
            },
        },
        "required": ["citation_id"],
    }

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        # Should never be called in normal operation — the research loop
        # intercepts recall_source calls and synthesizes results from
        # in-scope citations. This stub exists for registration/testing.
        return ToolResult(
            tool_name="recall_source",
            output="recall_source must be called within a research workflow.",
            success=False,
        )
```

**Registration:**

```python
RecallSourceTool.make_definition(
    invocation_cost=0.0, call_limit_per_run=10, call_limit_per_step=5
)
```

### Phase 2: Research loop interception

**File:** `backend/moira/workflow/nodes/research.py` (modify)

Add constant:
```python
_RECALL_SOURCE_TOOL_NAME = "recall_source"
```

Add result builder:
```python
def _build_recall_source_result(
    call: ToolCall, citations: list[Citation]
) -> ToolResult:
    """Synthesize a ToolResult for recall_source from in-scope citations."""
    citation_id = call.arguments.get("citation_id", "")

    for c in citations:
        if c["id"] == citation_id:
            parts = [f"Source: {citation_id}"]
            if c.get("title"):
                parts.append(f"Title: {c['title']}")
            if c.get("url"):
                parts.append(f"URL: {c['url']}")

            snippets = c.get("snippets", [])
            if snippets:
                parts.append("\nSearch result snippets:")
                for s in snippets:
                    parts.append(f"  - {s}")
            elif c.get("excerpt"):
                parts.append(f"\nExcerpt: {c['excerpt']}")

            content = c.get("content", "")
            if content and content.strip():
                parts.append(f"\nPage content:\n{content}")

            return ToolResult(
                tool_name=_RECALL_SOURCE_TOOL_NAME,
                output="\n".join(parts),
                success=True,
                duration_ms=0,
                metadata={"synthetic": True},
            )

    # Not found — list available IDs to help the model
    available = ", ".join(c["id"] for c in citations) if citations else "(none)"
    return ToolResult(
        tool_name=_RECALL_SOURCE_TOOL_NAME,
        output=(
            f"Citation '{citation_id}' not found. "
            f"Available citations: {available}"
        ),
        success=False,
        duration_ms=0,
        metadata={"synthetic": True},
    )
```

In both `_run_native_tool_loop` and `_run_text_tool_loop`, after
`_validate_and_filter_calls` and before `_execute_with_url_dedup`,
partition out `recall_source` calls:

```python
# Partition out recall_source calls (always synthetic — never executed)
recall_calls = [c for c in valid_calls if c.name == _RECALL_SOURCE_TOOL_NAME]
non_recall_calls = [c for c in valid_calls if c.name != _RECALL_SOURCE_TOOL_NAME]

# Execute non-recall calls (url_content dedup + executor)
if non_recall_calls:
    results = await _execute_with_url_dedup(
        non_recall_calls, _fetched_urls, executor, citations, call_counts
    )
    results_by_id = {c.id: r for c, r in zip(non_recall_calls, results)}
else:
    results_by_id = {}

# Synthesize recall_source results
for call in recall_calls:
    results_by_id[call.id] = _build_recall_source_result(call, citations)

# Reassemble in original valid_calls order
all_results = [results_by_id[c.id] for c in valid_calls]
```

Then pass `all_results` to `_process_execution_results` as before.

### Phase 3: Skip citation creation in `_process_execution_results`

**File:** `backend/moira/workflow/nodes/research.py` (modify)

At the top of the per-result loop, add a check:

```python
for result, call in zip(results, valid_calls):
    name = call.name

    # recall_source returns content from existing citations — don't
    # create new citations or charge budget (synthetic).
    if name == _RECALL_SOURCE_TOOL_NAME:
        tool_summary_parts.append(result.output)
        tool_results_log.append({
            "tool": name,
            "args": call.arguments,
            "result": result.output[:_SNIPPET_MAX_LENGTH],
            "duration_ms": result.duration_ms,
            "success": result.success,
        })
        continue

    # ... existing citation creation logic ...
```

### Phase 4: Tests

**Files:**
- `backend/tests/test_recall_source_tool.py` (new)
- `backend/tests/test_research_recall_source.py` (new)

Test cases:

1. **Tool class**: argument schema structure, description non-empty,
   `execute()` stub returns failure
2. **Registration**: appears in `STANDARD_TOOLS`, `is_default=True`,
   `invocation_cost=0.0`
3. **Interception — found**: `recall_source("cit004")` produces synthetic
   ToolResult with title, URL, snippets, and content from the matching
   citation
4. **Interception — not found**: unknown citation ID returns error with
   available IDs listed
5. **Interception — no citations**: returns "(none)" when citations list
   is empty
6. **No budget charge**: synthetic flag prevents budget deduction in
   `_process_execution_results`
7. **No duplicate citations**: `recall_source` results don't create new
   Citation objects
8. **Call ordering**: results returned in the same order as the model's
   calls, even when mixed with `web_search` and `url_content` calls
9. **Call limits**: respects `call_limit_per_run` and `call_limit_per_step`

## Verification

```bash
cd backend/
uv run pytest tests/ -q -x --ignore=tests/test_url_content.py
.venv/bin/ruff check
.venv/bin/ruff format --check
```

## Design Decisions

- **Always available, not conditional**: The tool is a default tool, always
  in `candidate_tools`. In the first research round (no citations), the model
  gets a "no citations" response and learns. In retry rounds, it becomes
  valuable. Conditional presentation would require changes to
  `tool_identification` (which doesn't re-run on review retries) or the
  research node, adding complexity for little benefit.

- **Free (invocation_cost=0.0)**: Reading existing state costs nothing.
  `metadata["synthetic"]=True` ensures budget isn't charged. Call limits
  (10/run, 5/step) prevent context bloat.

- **citation_id only**: Direct lookup by ID is the simplest interface. The
  model sees citation IDs in the retry context's "prior citations" section
  and can recall specific sources. A fuzzy-search `query` parameter could be
  added later if needed.

- **No changes to BaseTool or executor**: The interception pattern reuses
  the established `url_content` dedup approach — tool calls are intercepted
  in the research loop before reaching the executor, and results are
  synthesized from in-scope state.
