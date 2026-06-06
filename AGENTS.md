# Agent Guidelines for the project

## General guidelines for the agent

- Stay inside the project directory. Do not take actions outside the project directory.
- Don't make git commits unless specifically asked.
- Ensure all tests pass before declaring a task completed.
- Maintain full test coverage for new code.
- Consider the requirements of the project and the architectural guidelines before making major changes.
- When the guidelines and existing project don't clearly show an architectural direction or preference for library, ask the user.
- Open design or architecture questions that the user needs to answer should be written to /agent-docs/QUESTIONS.md.  As those questions are answered, and the answers reflected in the design and implementation files, they should be removed fron the QUESTIONS.md doc.

## Application purpose

The MOiRA project is a self-hosted interactive research agent platform built around:

* local and self-hosted LLM inference
* structured agentic workflows
* dynamic tool usage
* verification-oriented research loops
* conversational UI interaction

The system is not intended to be a generic chatbot. It is intended to function as a:

> stateful, tool-using research and reasoning system with interactive conversational access.

The platform should support:

* REST APIs
* MCP tools
* retrieval systems
* semantic tool discovery
* multi-model orchestration
* verification and citation workflows

The system must prioritize:

* transparency
* inspectability
* deterministic orchestration
* extensibility
* robustness under large tool catalogs

## Coding Guidelines

- Use idiomatic Python or Typescript code where possible.  Where not possible, add comments explaining why the code differs from idiomatic Python or Typescript.
- Use safe implementation strategies to prevent injection attacks, memory leaks, and crashes.
- Avoid memory allocation bloat where it is easily avoidable, but don't over-complicate the code to do so.
- Keep code readable and formatted for readability.
- Avoid repeated boilerplate code. Create helpers as the need for or presence of boilerplate repetition is determined.
- Add docstrings or comment documentation to functions and methods to explain what they do, and what specific design or implementation choices they represent.

## Environment
- macOS on Apple Silicon.
- Prefer `rg` over `grep`.
- Run python commands using `uv run`.
- In general, use `uv` to manage the Python environment.
- Use `shellcheck` to error check and lint shell scripts. 

## Research
- Always use Context7 MCP when I need library or API documentation, code generation, setup, or configuration steps, without me having to explicitly ask to use Context7.

## Codebase Workflow
- Read files before editing them.
- Use `rg` to locate relevant sections before opening large files.
- Keep changes scoped to the request.
- Ask before refactors that touch more than 3 files or change public behavior, such as API surface, return types, function signatures, or exported names.
- Preserve existing style, naming, formatting, and architecture unless there is a clear reason to change them.
- Use comments to explain important implementation decisions to reading developers.
- Use logging to log important code events.

## Verification
- After code changes, run the project's relevant typecheck, lint, and tests when available.
- Do not claim work is complete without saying what verification ran.
- If verification could not be run, say why.
- Implementation phases are not complete until they run with clean typecheck, lint, and testing for both typescript/frontend and python/backend.

## Output Style
- Be direct.
- No unnecessary preamble.
- Push back on bad ideas or risky assumptions.
- When asked for code, provide complete corrected code blocks unless a diff or partial snippet is specifically requested.
- Surface important command errors instead of hiding them.

## Stop Conditions
- If the same test fails twice with the same root cause, stop and explain the blocker.
- If a tool returns an unexpected error, report it before trying a substantially different approach.
- If 5 or more tool calls make no progress on the same subproblem, stop and ask for direction.

## The Name

"MOiRA" stands for "My Own intelligent Research Agent".  Use `moira` as the base name or namespace for code or configuration for this project. 

## Other resources

The [agent-docs](agent-docs) directory contains specific guidance for architecture, design, 
user experience, and critique notes. 
See [agent-docs/index.md](agent-docs/index.md) for a list of documents and what they contain. 
See [agent-docs/core/](agent-docs/core/) for the core application architecture and description docs.
A general roadmap is available in [roadmap.md](roadmap.md).
