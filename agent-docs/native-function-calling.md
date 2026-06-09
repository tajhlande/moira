# Native Function Calling — Design Plan

## Current Situation

Moira invokes tools through a text-based protocol:

1. Tool descriptions are formatted as text and injected into the LLM prompt via `_format_tool_descriptions()` (research_nodes.py)
2. The model responds with plain text containing JSON: `[{"tool": "web_search", "args": {"query": "..."}}]`
3. `_parse_tool_calls()` parses the response using regex and `json.loads`, handling multiple formats (JSON arrays, line-delimited JSON, markdown-fenced arrays)
4. If parsing fails but the response looks like attempted tool calls, the system retries with a correction prompt

The `InferenceClient.chat_completion()` method sends only `model`, `messages`, `temperature`, and `max_tokens` to the `/chat/completions` endpoint. No `tools` parameter is passed.

## Goal

Add support for native function calling — passing tool definitions as structured parameters to the LLM API and receiving typed tool calls in the response. Provide a user option to choose between text-based (current) and native function calling per model or globally.

## Native Function Calling

Both OpenAI and Anthropic APIs (and llama.cpp's OpenAI-compatible server) support passing a `tools` parameter with structured function definitions:

- **Request**: `tools` array with `name`, `description`, and `parameters` (JSON Schema) for each tool
- **Response**: structured `tool_calls` array with typed arguments — no text parsing needed
- **Tool name constraints**: OpenAI requires `^[a-zA-Z0-9_]+$`, Anthropic requires `^[a-zA-Z0-9_-]{1,128}$`

llama.cpp's server supports the same `tools` parameter through its OpenAI-compatible endpoint. This means Moira can use native function calling with self-hosted models, not just cloud providers.

## Model Considerations

Not all models handle native function calling equally:

- **Models with tool-calling chat templates** (e.g., Qwen, Mistral, Llama 3.1+): produce clean, structured tool calls via the `tools` parameter. These models have special tokens and templates for tool use.
- **Models without tool-calling templates**: may ignore the `tools` parameter entirely, produce malformed tool call objects, or fall back to generating text. These models work better with the text-based approach where tool descriptions are in the prompt.
- **Cloud models** (OpenAI, Anthropic): native function calling is the intended interface — more reliable and structured than text-in-prompt.

## Tradeoffs

| | Text-based (current) | Native function calling |
|---|---|---|
| Model compatibility | Works with any model that can follow instructions and output JSON | Requires model with tool-calling support (chat template or cloud API) |
| Reliability | Fragile — multi-format parsing, retry on malformed output, model confusion between prose and tool calls | Structured parsing, no regex, no retry hacks |
| Tool name constraints | None (tool names are just strings in parsed JSON) | Must match provider regex: `^[a-zA-Z0-9_]+$` (OpenAI), `^[a-zA-Z0-9_-]{1,128}$` (Anthropic) |
| Argument validation | None — raw dicts parsed from text | Provider validates against the JSON Schema parameter definition |
| Token efficiency | Tool descriptions consume prompt tokens as text | Tool descriptions are passed separately; some providers handle them more efficiently |
| Transparency | Tool calls visible in model's text response (inspectable) | Tool calls in a separate response field (still inspectable, different location) |
| Streaming | Tool calls arrive as text tokens, parsed after completion | Some providers stream tool call arguments incrementally |

## Design Direction

The system should support both modes:

- **Per-model configuration**: a setting or model metadata flag that indicates whether native function calling is available. Models with known tool-calling templates default to native; others default to text-based.
- **User override**: a setting to force text-based or native mode regardless of the model default. Useful for testing or when a model's tool-calling template produces poor results.
- **Graceful fallback**: if native function calling is enabled but the model returns no tool calls (or malformed ones), the system can fall back to text-based parsing of the response content.

## Implications for Tool Naming

The tool naming scheme in `dynamic-tool-discovery.md` uses `^[a-zA-Z0-9_]+$` compatible names with `__` double-underscore delimiters. This satisfies both OpenAI and Anthropic constraints preemptively. No naming changes are needed to support native function calling.

## Implications for Inference Client

`InferenceClient.chat_completion()` needs a new optional `tools` parameter. When provided, tools are included in the API request payload. The response parsing needs to handle both `tool_calls` in the response object (native) and text-based parsing of `content` (current). The method should return a unified result regardless of which path was used.

## Files to Modify

| File | Change |
|---|---|
| `backend/moira/inference/client.py` | Add `tools` parameter to `chat_completion`, parse `tool_calls` from response |
| `backend/moira/workflow/nodes/research_nodes.py` | Route to native or text-based tool call handling based on config |
| `backend/moira/models/state.py` | Add `native_tool_calling` flag or source from settings |
| `backend/moira/services/settings/definitions.py` | Add `inference.native_tool_calling` setting definition |
| `backend/moira/config.py` | Add per-model native tool calling option |

## Deferred

- Streaming tool call arguments (some providers support this)
- Parallel tool call execution hints (Anthropic supports `tool_choice` options)
- Automatic detection of model tool-calling capability (for now, user configures per model)
