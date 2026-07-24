# Roadmap

A set of outstanding features and capabilities to be build into MOiRA.

These are roughly organized by area and not in a plan order.

## Bugs

- Start conversation, leave, return while running - execution time at the bottom says 0:00
- Conversation that has an error, then restarts successfully still has "The research run encountered an error and could not generate a report." messages in it, after the report.

## Major Capabilities

- Classification of wanted facts as a verifiable claim or an opinion/consensus claim,
  and opinion facts/conclusions become acceptable when the range of views is
  represented with attribution
- Runtime tool registration from non OpenAPI REST APIs
- Runtime inference provider configuration
- Projects as top level organizing feature
  - grouped conversations into project
  - tuning differences
  - customized default toolsets
  - knowledge base tool (add documents in UI, content available to agent via tool)

## Content and intelligence

- Handle conflicts by downgrading fact if possible
- Include pre-existing facts and conclusions in subsequent conversation input
- Normalize tool calling to one shape
- Source grading and classification (authoritative, consensus, other)
- Extract source age from tool output (have tools discover and express source age)
- Reduce tool calling volume
- Thinking budgets
- Better citation formatting and referencing
- Web content prompt injection safety
- long conversation context management
- "Grind" verification option to split draft text into sentences and
  verify each sentence independently.
- Memory
  - Fact storage and recall
  - Fact hygiene
  - Reasoning with facts
- Sub-agents and parallel research branches
- Constrained decoding

## Built-in Tools

- Ask the user a question tool
- Try refactoring web_search tool to return only URLs, so that fetch_url is used for content
- Kagi search tool
- Wikipedia search
- Arxiv search
- Wolfram Alpha
- OpenAlex
- Wikidata
- Pluggable document storage and retrieval

## Deployment

- A graphic logo

## Identity and security

- User identity and conversation/credential ownership
- OAuth2 integration for authentication
- OpenID Connect integration for authentication
- built-in authentication? (undecided)
- admin role
- authorized access controls and sharing

## User experience

- UI Themes
  - light/dark/system theme application
  - built in
  - installable third-party packaged themes
- Organization for conversations
  - chronological
  - search
- render images from web content
- suggested follow-up prompts
- Share links (should work now)
- export and import of tool groups
- fix horizontal divider line styling at joints

- export and import system settings
- export and import conversation history

# Bugs and cleanup

- Set proper user agent headers for tool calls and inference
- Replace httpx with httpx2 when huggingface-hub and langgraph-sdk migrate to httpx2
- Fix Warning: You are sending unauthenticated requests to the HF Hub. Please set a HF_TOKEN to enable higher rate limits and faster downloads.
- On dev shutdown, UserWarning: resource_tracker: There appear to be 1 leaked semaphore objects to clean up at shutdown: {'/loky-80962-3am4i1x0'}
- `npx vitest run` warning: (node:86890) ExperimentalWarning: localStorage is not available because --localstorage-file was not provided.
  (Use `node --trace-warnings ...` to show where the warning was created)
   ✓ src/components/__tests__/StructuredOutputRenderer.spec.ts (16 tests) 37ms
   ✓ src/components/__tests__/ChatView.spec.ts (12 tests) 289ms
