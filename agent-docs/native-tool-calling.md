# Native Tool Calling

Design for migrating the research node's tool-calling mechanism from a
prompt-engineered JSON protocol parsed from model text to native tool calling
via the inference server's tool-calling API.

## Problem

The research node currently instructs the model to emit a single JSON object
containing `tool_calls`, `discovered_facts`, and `sources`. This text is parsed
from `response.content` with extensive defensive logic
(`_fix_json_control_chars`, `_parse_json_object`, `_parse_tool_calls`,
parse-retry loop). All of this machinery exists because local/quantized models
sometimes produce malformed JSON when asked to format tool calls as text.

Native tool calling moves the tool-invocation protocol into the inference
server's chat template and structured response format, eliminating the text
parsing layer for tool calls. Qwen3 models are specifically trained for this,
and both vLLM and llama.cpp's OpenAI-compatible server support the `tools`
parameter, so native tool calling works with self-hosted models, not just
cloud providers.

## Tradeoffs

| | Text-based (current) | Native tool calling |
|---|---|---|
| Model compatibility | Works with any model that can follow instructions and output JSON | Requires a model with tool-calling support (chat template or cloud API) |
| Reliability | Fragile — multi-format parsing, retry on malformed output, model confusion between prose and tool calls | Structured parsing, no regex, no retry hacks |
| Tool name constraints | None (tool names are just strings in parsed JSON) | Must match provider regex (see below) |
| Argument validation | None — raw dicts parsed from text | Provider validates against the JSON Schema parameter definition |
| Token efficiency | Tool descriptions consume prompt tokens as text | Tool descriptions are passed separately; some providers handle them more efficiently |
| Transparency | Tool calls visible in model's text response | Tool calls in a separate response field (still inspectable, different location) |
| Streaming | Tool calls arrive as text tokens, parsed after completion | Some providers stream tool call arguments incrementally |

## Design Goals

1. **Feature-flagged** — the existing text-based protocol is retained, not
   replaced. A per-model configuration flag controls which mode is used.
2. **Multi-provider compatible** — the design accommodates OpenAI Chat
   Completions, OpenAI Responses, and Anthropic Messages APIs, even though the
   initial implementation targets OpenAI-compatible servers (vLLM).
3. **Hybrid fact extraction** — discovered_facts and sources remain as
   structured text output from the model's `content` field. Native tool calling
   only handles tool invocation; fact/source extraction stays text-based.
4. **No disruption to existing runs** — the text-based protocol continues to
   work unchanged when the feature flag is disabled.

## Feature Flag

The flag lives on the **model** configuration. Tool calling
support is a property of the model via its chat template and training.
A single provider (e.g., one vLLM instance) can serve
multiple models where only some support tool calling:

```yaml
inference:
  providers:
    - name: local
      base_url: "http://llmhost.example/v1"
      api_key: ""
      provider_type: "openai"         # "openai" | "anthropic" | "responses"
      models:
        - id: "Qwen3.6-35B-A3B"
          native_tool_calling: true
        - id: "Qwen3.5-2B-No-Thinking"
          native_tool_calling: false
```

`provider_type` stays on the provider config — it identifies the API dialect,
which is a server property. `native_tool_calling` defaults to `false`. When
`false` for the resolved model, the research node uses the existing text-based
protocol.

## Model Considerations

Not all models handle native tool calling equally:

- **Models with tool-calling chat templates** (e.g., Qwen, Mistral, Llama
  3.1+): produce clean, structured tool calls via the `tools` parameter. These
  models have special tokens and templates for tool use.
- **Models without tool-calling templates**: may ignore the `tools` parameter
  entirely, produce malformed tool call objects, or fall back to generating
  text. These models work better with the text-based approach where tool
  descriptions are in the prompt.
- **Cloud models** (OpenAI, Anthropic): native tool calling is the intended
  interface — more reliable and structured than text-in-prompt.

Because capability varies per model, the feature flag is on the model
configuration, not the provider. Models with known tool-calling templates
should have the flag enabled; for others, leave it disabled.

The `ModelRegistry.resolve()` method propagates both fields through
`ResolvedModel` so the research node can check capabilities without reaching
into config:

```
ResolvedModel
    model_id: str
    client: InferenceClient
    native_tool_calling: bool
    provider_type: str
```

## Provider API Differences

The three target provider APIs differ in tool definition format, tool call
response format, tool result message format, and finish reason signaling:

### Tool definitions (request side)

| Provider | Wire format |
|---|---|
| OpenAI Completions | `{"type": "function", "function": {"name", "description", "parameters": <JSON Schema>}}` |
| Anthropic Messages | `{"name", "description", "input_schema": <JSON Schema>}` |
| OpenAI Responses | `{"type": "function", "name", "description", "parameters": <JSON Schema>}` |

The existing `ToolDefinition.argument_schema` is already standard JSON Schema.
Conversion is a thin envelope wrapper that varies by provider.

**Tool name constraints** — providers enforce regex patterns on tool names:
OpenAI requires `^[a-zA-Z0-9_]+$`; Anthropic requires `^[a-zA-Z0-9_-]{1,128}$`.
The naming scheme described in `dynamic-tool-discovery.md` uses
`^[a-zA-Z0-9_]+$` compatible names with `__` delimiters, which satisfies both
constraints preemptively. No naming changes are needed.

### Tool calls in responses

| Provider | Location | Format |
|---|---|---|
| OpenAI Completions | `message.tool_calls[]` | `{"id", "type": "function", "function": {"name", "arguments": "<JSON string>"}}` |
| Anthropic Messages | `content[]` blocks | `{"type": "tool_use", "id", "name", "input": <dict>}` |
| OpenAI Responses | `output[]` items | `{"type": "function_call", "id", "name", "arguments": "<JSON string>"}` |

Key difference: OpenAI sends `arguments` as a JSON **string** that must be
parsed. Anthropic sends `input` as a parsed **dict**.

### Tool results (fed back to model)

| Provider | Message format |
|---|---|
| OpenAI Completions | `{"role": "tool", "tool_call_id": "<id>", "content": "<text>"}` |
| Anthropic Messages | `{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "<id>", "content": "<text>"}]}` |
| OpenAI Responses | Separate `function_call_output` item in `input` array |

### Finish reason

| Provider | Field | Value when calling tools |
|---|---|---|
| OpenAI Completions | `finish_reason` | `"tool_calls"` |
| Anthropic Messages | `stop_reason` | `"tool_use"` |
| OpenAI Responses | `status` | `"in_progress"` (with function_call items) |

## Canonical Internal Format

To isolate the research node from provider differences and unify both modes,
a canonical representation normalizes all tool-related data:

**ToolCall** — a single tool invocation extracted from a model response:
```
ToolCall
    id: str           # call ID — server-assigned in native mode, generated by the parser in text mode
    name: str         # tool name
    arguments: dict   # parsed arguments (always a dict, never a JSON string)
```

`ToolCall` is used for **both** the native and text-based modes. The current
text-based path represents tool calls as bare `tuple[str, dict]` tuples (in
`_extract_tool_calls`, `_parse_tool_calls`, and `executor.execute_batch`).
Introducing `ToolCall` replaces those tuples with a named structure, so the
downstream validation, execution, and citation-building code works with one
type regardless of mode. Both modes always produce fully-populated `ToolCall`
instances — the text-mode parser generates its own IDs (e.g., UUID or
sequential) during parsing, so no downstream code needs to branch on whether
an ID is present.

The `ChatResponse` dataclass gains a `tool_calls: list[ToolCall]` field.
Provider-specific parsing happens in the inference client layer; the research
node only sees the canonical form.

## Tool Calling Adapter

A provider-specific adapter handles the format conversions. Each adapter knows
how to:

1. **Format tool definitions** — convert `list[ToolDefinition]` to the
   provider's wire format
2. **Parse tool calls from response** — extract and normalize tool calls from
   the provider's response message structure into `list[ToolCall]`
3. **Format tool result messages** — convert a tool execution result into the
   provider's expected message format

The adapter is selected based on `provider_type` from the resolved model
configuration. The inference client delegates tool-related operations to the
adapter when `native_tool_calling` is enabled.

Three adapter implementations:

- **OpenAICompletionsAdapter** — wraps tools in `{"type": "function", ...}`,
  parses `message.tool_calls`, emits `{"role": "tool", ...}` results. This is
  the initial implementation.
- **AnthropicMessagesAdapter** — wraps tools in `{"name", "input_schema", ...}`,
  parses `content[].type == "tool_use"`, emits tool results as user messages
  with `tool_result` content blocks. Note: Anthropic also requires a different
  HTTP endpoint and request body, which implies broader client changes beyond
  tool calling.
- **OpenAIResponsesAdapter** — wraps tools without the `function` nesting,
  parses `output[]` items, emits `function_call_output` items. This API is
  still evolving; the adapter can be implemented when the Responses API is
  available on self-hosted servers.

The Anthropic and Responses adapters are described for design completeness.
The initial implementation delivers only the OpenAI Completions adapter.

## Research Node Changes

When `native_tool_calling` is enabled on the resolved model, the research node:

1. **Builds tool definitions** — converts `candidate_tools` to the provider's
   tool format via the adapter, passes them to `chat_completion`
2. **Replaces the inner parse-execute-feedback cycle** — instead of parsing
   JSON from `response.content` for tool calls, reads `response.tool_calls`
   directly
3. **Uses native message format** — assistant messages carry `tool_calls`;
   results go back as `tool` role messages (or provider equivalent) via the
   adapter
4. **Keeps hybrid fact extraction** — when the model emits text `content`
   alongside tool calls (or when it stops calling tools), `discovered_facts`
   and `sources` are parsed from that text using the existing `_parse_json_object`
   helper

### Loop structure (native mode)

The loop retains its `MAX_ROUNDS` structure, post-loop summary, and post-loop
fact extraction. The inner cycle changes:

1. Call `chat_completion` with `tools` and `tool_choice`
2. If `response.tool_calls` is non-empty:
   - Append assistant message with tool_calls to conversation
   - Parse any `discovered_facts`/`sources` from `response.content` (hybrid)
   - Validate calls (allowed names, call limits, required args)
   - Execute via `executor.execute_batch`
   - Append tool result messages via adapter format
   - Continue to next round
3. If `response.tool_calls` is empty (model is done):
   - Parse `discovered_facts`/`sources` from `response.content`
   - Break out of loop

### What is not invoked in native mode

- `_parse_tool_calls(raw)` — legacy bare-array parser
- `_looks_like_failed_tool_calls(raw)` — heuristic for detecting failed calls
- Parse-retry loop (`DEFAULT_MAX_PARSE_RETRIES`, correction prompts)
- `_fix_json_control_chars` for tool call parsing (still needed for
  `discovered_facts`/`sources` text parsing)

### What is retained from the existing non-native approach

- `_parse_json_object` — for parsing `discovered_facts`/`sources` from content
- `_apply_discovered_facts` / `_apply_sources` — unchanged
- `ToolCall` type — shared across both modes, always fully populated (parser-generated IDs in text mode, server-assigned in native mode)
- Citation building (`_find_or_merge_citation`) — unchanged
- Post-loop summary and fact extraction — unchanged
- Budget deduction and call limit enforcement — unchanged

## Prompt Changes

When native tool calling is enabled, the model receives tool definitions
through the server's chat template, not through the user prompt. Two new prompt
sections are needed:

**`research.system_native_tools`** — variant system prompt that:
- Removes the `tool_calls` JSON format instructions
- Removes the "RESPONSE FORMAT" section about tool_calls structure
- Removes tool call parameter format guidance
- Keeps `discovered_facts` and `sources` JSON format instructions (the model
  still emits these as text in `content`)
- Keeps the rules about fact discovery, not drawing conclusions, tool limits
- Includes a brief instruction: "When you have gathered enough information,
  respond with your discovered_facts and sources as JSON. Do not include
  tool_calls — use the tool calling interface instead."

**`research.user_native`** — variant user prompt that:
- Removes `{tool_call_plan}` (no text plan to follow)
- Removes `{tool_descriptions}` (server provides these)
- Keeps `{user_goal}` and `{unknown_facts}` (the model needs to know what to
  research)

The existing `research.system` and `research.user` prompts remain unchanged for
the text-based fallback mode.

## Backward Compatibility

The text-based protocol is fully retained. The research node checks
`resolved.native_tool_calling` at entry:

- If `True`: use native prompts, pass `tools` to `chat_completion`, read
  `response.tool_calls`, use adapter for message format
- If `False`: use existing text-based prompts, parse JSON from
  `response.content`, use correction prompts on parse failure

Both code paths coexist. No existing tests change. New tests cover the native
mode. The feature flag can be toggled at any time via config.

This code can be refactored into separate functions, classes, or another file
to enable clarity about the central tasks of the research node vs the functions that exist
to support tool calling.

## Configuration Changes

### `InferenceEndpointConfig` (config.py)

Add one field:

- `provider_type: str = "completions"` — identifies the API dialect (server property)

The value should be one of: "completions", "responses", or "messages"

to indicate compatibility with OpenAI completions, OpenAI responses, or Anthropic messages
formats, respectively.

This value is required.

We will defer support for simplified configuration of named inference service
providers to a later time.


### Per-model configuration

Add a model-level config structure to carry model-specific capabilities:

- `native_tool_calling: bool = False` — enables native tool calling for this
  model on this provider

This lives under the provider's model list (see the YAML example above), since
the same model ID served by different providers can have different
capabilities depending on the server's chat template configuration.

This value is optional, and should default to "False" as specified above.

### `ResolvedModel` (registry.py)

Propagate both `native_tool_calling` and `provider_type` so the research node
can check capabilities without reaching into config.

### Config template (moira-config-template.yaml)

Document the new fields with comments.

## Testing Strategy

### Unit tests

- **ToolCall tests** — verify construction, ID generation in text mode,
  argument parsing from JSON string (native) vs dict (text)
- **Adapter tests** — verify format conversion for tool definitions, tool call
  parsing, and tool result messages for each provider type
- **Client tests** — mock responses with `tool_calls` in provider-specific
  format, verify canonical `ToolCall` extraction into `ChatResponse`
- **Research node tests (native)** — mock `ChatResponse` with `tool_calls`,
  verify execution, message format, fact extraction from content
- **Research node tests (text fallback)** — existing tests unchanged, updated
  to use `ToolCall` type

### Integration test

- Full graph run with `native_tool_calling: true`, mock model that emits
  tool_calls for 2 rounds then text with discovered_facts
- Verify same knowledge model state as equivalent text-mode run

### Manual validation

- Compare native vs text mode on the evaluation canary question
- Monitor for malformed tool call arguments (server-side vs client-side parsing)
- Verify tool calling works with the Qwen3.6-35B-A3B model

## Implementation Phases

Each phase produces a runnable, tested artifact. Tests are written alongside
each component, not deferred to the end.

### Phase 1: ToolCall refactor

Replace `tuple[str, dict]` with the `ToolCall` canonical type across the
text-based path. No behavior change — this is a structural refactor.

- Introduce `ToolCall` dataclass (`id`, `name`, `arguments`)
- Text-mode parser generates call IDs during parsing
- Refactor `_extract_tool_calls`, `_parse_tool_calls` to return
  `list[ToolCall]`
- Refactor `executor.execute_batch` to accept `list[ToolCall]`
- Tests: all existing tests updated and passing, `ToolCall` unit tests
- Runnable artifact: system works exactly as before with cleaner internals

### Phase 2: Native tool calling

Deliver native tool calling end-to-end. All infrastructure components
(inference layer, config, adapter, research node, prompts) land together
since none produces observable behavior in isolation.

- **Inference layer** — `tools`/`tool_choice` params on `chat_completion`,
  `tool_calls` field on `ChatResponse`, parse from provider response
- **Adapter** — OpenAI Completions adapter (format tools, parse tool calls,
  format tool result messages)
- **Config** — `provider_type` on provider config, `native_tool_calling` on
  model config, propagate through `ResolvedModel`
- **Research node** — feature flag branch, native mode loop using adapter,
  hybrid fact extraction from `content`
- **Prompts** — `research.system_native_tools` and `research.user_native`
- Tests: adapter unit tests, client tests (mocked tool_calls), research node
  native mode tests — written alongside each component
- Runnable artifact: native tool calling works when flag is enabled, text
  mode unchanged when disabled

### Phase 3: Integration validation

Validate the feature end-to-end before production use.

- Full graph integration test with `native_tool_calling: true` (mock model
  emits tool_calls for 2 rounds, then text with discovered_facts)
- Verify same knowledge model state as equivalent text-mode run
- Manual A/B comparison against text mode on the evaluation canary question
- Token budget / performance assessment for runs with many candidate tools
- Runnable artifact: validated feature ready for production use

Future phases (not in initial scope):
- Anthropic Messages adapter (requires broader client refactoring for different
  HTTP contract)
- OpenAI Responses adapter (requires different endpoint and request structure)
- Streaming tool call arguments (some providers support incremental streaming
  of tool call arguments)
- Parallel tool call execution hints (Anthropic supports `tool_choice` options
  for directing parallel execution)
- Automatic detection of model tool-calling capability via live model
  provisioning (querying provider model lists and reading capability
  metadata). For now, the user configures the flag per model in config.
- Removal of text-based parsing machinery (only after native mode is proven
  stable across multiple models)

## Open Questions

1. **Does vLLM correctly format Qwen3.6-35B-A3B's tool calling template?**
   Needs validation before implementation. If the server's chat template is
   broken, native tool calling won't work regardless of the client code.

2. **Does the model reliably emit `discovered_facts` in `content` alongside
   tool calls?** The hybrid approach depends on this. If the model only emits
   facts when it stops calling tools, fact extraction moves to the post-loop
   pass (which already exists as a fallback).

3. **Token budget impact** — native tool definitions in the system prompt may
   use more tokens than the text description approach. Need to measure context
   window consumption for runs with many candidate tools.
