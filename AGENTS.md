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


## The Name

"MOiRA" stands for "My Own intelligent Research Agent".  Use `moira` as the base name or namespace for code or configuration for this project. 

## Architecture

See [agent-docs/architecture.md](agent-docs/architecture.md) for application architecture specification. 


