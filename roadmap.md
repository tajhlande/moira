# Roadmap

A set of outstanding features and capabilities to be build into MOiRA.

These are roughly organized by area and not in a plan order.

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

- Include pre-existing facts and conclusions in subsequent conversation input
- Normalize tool calling to one shape
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

## Built-in Tools

- Try refactoring web_search tool to return only URLs, so that fetch_url is used for content
- Kagi search tool
- Wikipedia search
- Arxiv search
- Wolfram Alpha
- OpenAlex
- Wikidata

## Deployment

- README improvements
- Docker container
- Docker compose
- Packaged releases
- Quickstart and onboarding
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
  - installable third party packaged themes
- Organization for conversations
  - chronological
  - search
- Latex rendering support
- toggle Markdown rich text/source on report
- paragraph spacing issues in report
- render images from web content
- suggested follow-up prompts
- Share links
- export and import of tool groups
- fix horizontal divider line styling at joints
- make "additional sources" block collapsed by default
- in conversation list, put individual buttons in a popup menu to make more room for titles
- in sources list, put web search snippets in chevron collapsible area

# Cleanup

- Set proper user agent headers for tool calls and inference