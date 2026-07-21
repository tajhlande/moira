# Context Management Plan

## Problem

Self-hosted models have fixed context windows (e.g., 98K tokens for Qwen3.6-35B-A3B).
A single research run with multiple tool rounds can exceed this — we observed a 163K
token prompt into a 98K context window. The overflow came from tool results accumulating
in the `_run_tool_loop` messages list across 3-5 rounds of multi-tool execution.

This is a core concern for a self-hosted research agent: the user cannot increase
`n_ctx` indefinitely, and tool-heavy research workflows naturally produce large prompts.

## Token Bloat Sources (within a single run)

1. **`_run_tool_loop` message accumulation** (biggest offender) — each tool round
   appends `assistant` + `user` messages with full tool outputs. 3-5 rounds x N tool
   calls x full output text = massive growth. Round 3 includes the full conversation
   from rounds 1 and 2.

2. **`draft_synthesis` evidence formatting** — all findings joined into one string:
   `"\n\n".join(f"[{f['source']}] {f['content']}" for f in findings)`. Unbounded.

3. **`verification` fact-check + verdict** — two separate model calls, both include
   the full `draft` + `question` + tool evidence. If verification triggers a retry,
   this doubles.

4. **`report_generation`** — includes full `draft` + all compressed/findings evidence.

5. **`compression` input** — all raw findings joined and sent as one user message.

## Design

### Layer 1: `ContextOverflowError` — structured exception

Add a custom exception in `moira/inference/client.py` that replaces the generic
`httpx.HTTPStatusError` for context overflow:

```python
class ContextOverflowError(Exception):
    """Raised when the prompt exceeds the model's context window."""
    prompt_tokens: int
    context_limit: int
```

Detection: parse the 400 response body for known patterns:
- `exceed_context_size_error` (llama.cpp / LM Studio)
- `context_length_exceeded` (OpenAI-compatible)
- Any 400 with `n_prompt_tokens` / `n_ctx` or `prompt_tokens` / `max_tokens` fields

**Auto-detect and cache**: on the first `ContextOverflowError`, extract `context_limit`
from the error response and cache it on the `InferenceClient` instance (keyed by
model ID). Subsequent calls use the cached value for proactive truncation. No config
required, but config can override (see Layer 5).

The cached limit is per-process (in-memory). It persists across runs within the same
server session. If the user swaps models, the cache entry for the old model remains
valid and a new entry is created for the new model on first use.

### Layer 2: Proactive tool output capping

**Immediate win.** Tool results appended to the messages list in `_run_tool_loop`
are capped at a configurable max (default 2000 chars) before being added as user
messages. The full output is still stored in `all_tool_results` (for findings and
persistence) — only the in-context copy shown to the model is truncated.

Current code (line 318 in `research_nodes.py`):
```python
tool_summary_parts.append(f"Tool: {name}\nStatus: {status}\nResult:\n{result.output}")
```

After:
```python
output_for_context = _truncate_tool_output(result.output, max_chars=tool_result_max_chars)
tool_summary_parts.append(f"Tool: {name}\nStatus: {status}\nResult:\n{output_for_context}")
```

Truncation format:
```
[full text up to max_chars]...[truncated, N total chars]
```

This alone would prevent most overflow cases since tool outputs are often 10K+ chars
each and a 5-tool round with 3 retries would inject 150K+ chars of tool output alone.

### Layer 3: Adaptive truncation in `_run_tool_loop`

Before each model call, estimate the token count of the accumulated messages and
progressively truncate if approaching the context limit.

**Token estimation heuristic:**
- Primary: use `prompt_tokens` from the previous successful `ChatResponse` (exact).
- Fallback: `len(joined_text) / 3.5` (rough chars-per-token for English/mixed content).

**Truncation levels (applied in order until under budget):**

1. **Truncate oldest tool outputs** — replace full output in oldest round with
   `[tool1: success (N chars), tool2: success (N chars)]` summary line.
2. **Remove oldest tool rounds** — drop the assistant+user message pair for the
   oldest round entirely. Keep a note: "Previous round N removed for space."
3. **Truncate current round outputs** — if still over budget, truncate current
   round tool outputs more aggressively (500 chars → 200 chars → summary only).

**On `ContextOverflowError`:**
1. Catch in `_run_tool_loop`.
2. Apply aggressive truncation (skip to level 2 — remove oldest round entirely).
3. Retry once.
4. If still overflows, raise to the node handler which treats it as a node error
   (propagates to `report_generation` via the error path).

**Implementation in `_run_tool_loop`:**
- Add a `_trim_messages()` helper that takes the messages list, known context limit,
  and target utilization (e.g., 70%) and returns a trimmed copy.
- Call before each `chat_completion()` in the loop.
- Track `prompt_tokens` from each response for accurate estimation on subsequent rounds.

### Layer 4: Evidence capping in other nodes

`draft_synthesis`, `verification`, and `report_generation` all format findings/evidence
into strings before building messages. Cap these to a configurable max (default 20K chars).

**`draft_synthesis` (line 692):**
```python
evidence = _truncate_evidence(
    "\n\n".join(f"[{f['source']}] {f['content']}" for f in findings),
    max_chars=evidence_max_chars,
)
```

**`verification` fact-check evidence (line 836):**
```python
fact_check_evidence = _truncate_evidence(
    "\n\n".join(evidence_parts),
    max_chars=evidence_max_chars,
)
```

**`report_generation` evidence (line 1065):**
```python
evidence_text = _truncate_evidence(
    "\n".join(f["content"] for f in (compressed or findings or []) if f.get("content")),
    max_chars=evidence_max_chars,
)
```

**`compression` input (line 625):**
```python
findings_text = _truncate_evidence(
    "\n\n".join(f"[{f['source']}] {f['content']}" for f in findings),
    max_chars=evidence_max_chars,
)
```

Truncation appends: `\n\n...[N findings truncated, {remaining_chars} total chars]`

Each node should also catch `ContextOverflowError` from its model call and retry
with more aggressive truncation (half the max) before falling through to the error path.

### Layer 5: Configuration

Add to `moira/config.py`:

```python
class ContextConfig(BaseModel):
    max_context_tokens: int = 0  # 0 = auto-detect from server error
    evidence_max_chars: int = 20000
    tool_result_max_chars: int = 2000
    context_utilization_target: float = 0.7  # trigger truncation at 70%
```

Add `context: ContextConfig = ContextConfig()` to `MoiraConfig`.

When `max_context_tokens` is set, skip auto-detect and use the configured value.
When 0 (default), auto-detect from the first `ContextOverflowError`.

### Layer 6 (future): Conversation memory

This addresses multi-turn context growth (across conversation turns, not within
a single run). Designed separately when needed.

**Summarization node:**
- After each completed run, summarize the conversation (findings + report) into a
  compact representation stored in the DB alongside the report.
- The summary is ~500-1000 tokens and captures: question, key findings, conclusions,
  tools used, gaps identified.

**`conversation_content` tool:**
- A built-in tool available to the model during research_execution.
- Lets the model query previous conversation turns/reports without loading full text
  into context.
- The tool returns a compressed summary, not the full content.
- This enables the model to say "what did we discuss before?" without paying the
  full token cost of prior messages.

**Context windowing:**
- On multi-turn conversations, include only the last N messages + summary of earlier
  turns in the research state.
- The `prior_report` mechanism already does a simple version of this (just the answer
  field). Expand it to include the structured summary.

This layer requires its own plan document when prioritized.

## Files to Change

| File | Change |
|---|---|
| `moira/inference/client.py` | Add `ContextOverflowError`, parse error response, cache `context_limit` per model |
| `moira/config.py` | Add `ContextConfig` with `evidence_max_chars`, `tool_result_max_chars`, `context_utilization_target` |
| `moira/workflow/nodes/research_nodes.py` | Tool output capping in `_run_tool_loop`, `_truncate_tool_output()` helper, `_truncate_evidence()` helper, `_trim_messages()` adaptive truncation, evidence capping in `draft_synthesis`/`verification`/`report_generation`/`compression` |

## Implementation Order

1. **`ContextOverflowError` in `client.py`** — parse and raise structured exception, auto-detect and cache context limit. No behavioral change yet; just makes the error catchable.
2. **Proactive tool output capping** — cap tool results in `_run_tool_loop` messages to `tool_result_max_chars`. Biggest immediate win.
3. **Evidence capping in other nodes** — cap formatted evidence strings in `draft_synthesis`, `verification`, `report_generation`, `compression`.
4. **Adaptive truncation in `_run_tool_loop`** — `_trim_messages()` with token estimation, progressive truncation levels, retry on overflow.
5. **Config support** — `ContextConfig` in `config.py`, thread config through nodes via `RunnableConfig`.
6. **Future: conversation memory** — separate plan when prioritized.

## Key Decisions

- **Auto-detect over config-only**: self-hosted users shouldn't need to know their
  `n_ctx` value. The error response tells us exactly. Config is available as override.
- **Cap tool outputs proactively, not just on overflow**: waiting until overflow means
  one wasted inference call (which is expensive on local hardware). Proactive capping
  prevents the problem before it happens.
- **Full output preserved in `all_tool_results`**: truncation is for the in-context
  copy only. Findings and persistence get the complete data.
- **Progressive truncation in `_run_tool_loop`**: don't jump straight to removing
  entire rounds. Start with truncating outputs, then escalate. This preserves the
  most useful context for the model.
- **`ContextOverflowError` is recoverable**: nodes catch it, apply aggressive truncation,
  and retry once. Only if the retry also fails does it propagate as a node error.
- **`context_utilization_target` at 70%**: leaves headroom for the model's response
  tokens and avoids edge cases where the estimation is slightly off.
