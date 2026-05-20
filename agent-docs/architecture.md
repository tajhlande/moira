# MOiRA Research Agent Platform — Project Goals, Architecture, and Implementation Guidelines

## Overview

The MOiRA project is a self-hosted interactive research agent platform built around:

* local and self-hosted LLM inference
* structured agentic workflows
* dynamic tool usage
* verification-oriented research loops
* conversational UI interaction

The system is not intended to be a generic chatbot. It is intended to function as a:

> stateful, tool-using research and reasoning system with interactive conversational access.

The platform should support:

* Adapting REST APIs as tools
* Using MCPs as tools
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

---

# System Components

The frontend is NaiveUI and Typescript. 



The backend is Python 3.13, with the following additional component libraries:
* FastAPI 
* LangGraph 
* Sqlite
* httpx





# Core Architectural Principles

## Loose coupling and component replaceablility

Persistence should be contained behind an agnostic API layer of methods and obejct classes, so that moving from Sqlite to a separate RDBMS like Postgres or MariaDB is doable


# Application Design Principles 

## The UI is NOT the agent

The conversational UI is only a frontend interface.

The agent runtime owns:

* orchestration
* workflow state
* tool selection
* verification
* model routing
* execution tracing

The architecture should always follow:

```text
UI
  ↓
Agent API / LangGraph Runtime
  ↓
Workflow Graph
  ↓
Tool Layer
  ↓
Model Router (llama-swap)
  ↓
Inference Backends (llama.cpp)
```

The UI must never directly manage:

* tools
* planning
* workflow sequencing
* retries
* verification logic

---

## The system is a workflow engine, not a prompt

Avoid architectures based on:

* giant prompts
* hidden chain abstractions
* implicit orchestration

All meaningful execution should occur through:

* explicit graph nodes
* explicit state transitions
* explicit tool provisioning

---

## Tool usage must be structured and constrained

The system must never expose the full tool catalog to the model indiscriminately.

Tool exposure should be:

* contextual
* dynamically selected
* semantically filtered

The model should choose among:

* a small, relevant subset of tools
* selected specifically for the current workflow step

---

## Verification is a separate phase

Verification is not part of drafting.

The system must explicitly:

1. produce a draft answer
2. verify the draft against tools and sources
3. refine or reject unsupported claims

Verification nodes should:

* aggressively use tools
* identify unsupported statements
* identify contradictions
* identify missing evidence

---

# High-Level Workflow

## Canonical Research Workflow

```text
User Query
    ↓
Planning
    ↓
Tool Discovery / Selection
    ↓
Research Execution
    ↓
Compression / Summarization
    ↓
Draft Synthesis
    ↓
Verification
    ↓
Refinement
    ↓
Final Response
```

---

# LangGraph Design Guidelines

## State-Centric Design

All workflows should operate on explicit shared state.

Example state structure:

```python
class ResearchState(TypedDict):
    question: str
    plan: str
    active_tools: list
    findings: list
    compressed_findings: list
    draft: str
    verification: str
    final: str
```

State must remain:

* inspectable
* serializable
* resumable

---

## Node Design

Each node should define:

* purpose
* model selection
* available tools
* input state requirements
* output state modifications
* failure conditions

Example:

```text
Node: Verification

Purpose:
    Validate factual claims in draft output.

Model:
    intelligence

Tools:
    web_search
    paper_search
    citation_lookup

Failure Conditions:
    unsupported claims
    contradictory evidence
    missing citations

Outputs:
    verification report
```

---

## Graph Design

Prefer:

* explicit nodes
* explicit conditional routing
* visible loops

Avoid:

* hidden recursion
* implicit autonomous loops
* opaque framework abstractions

---

# Model Architecture

## Model Strategy

### Intelligence Model: larger model for intelligent reasoning

Typical size:

* 25B–100B parameters

Responsibilities:

* planning
* tool selection
* reasoning
* synthesis
* verification
* refinement

### Task Model: smaller model for tasks

Typical size:

* 2B-4B parameters

Responsibilities:

* summarization
* compression
* chunk reduction
* formatting
* lightweight transforms

The task model should never be used to:

* perform verification
* choose tools
* perform critical reasoning
* evaluate factual correctness

---

# Inference Infrastructure

## Inference Backend

Inference should be configurable, with a list of configured
models available from a list of endpoints.

Primary inference backends supported:

* OpenAI compatible completions API
* OpenAI compatible responses API

Available models should be read from the API and assigned to purpose (intelligence or task) by the user


## Model Routing

LangGraph should not directly manage model loading.

LangGraph should instead target stable routed endpoints such as:

```text
model="intelligence"
model="task"
```

where the user has selected the intelligence and task models. 

---

# Tool Architecture

## Supported Tool Types

The system should support:

* REST APIs
* OpenAI-compatible tools
* MCP tools
* retrieval systems
* local databases
* search indexes

---

## MCP Integration

MCP tools should be wrapped into a unified internal tool interface.

Example pattern:

```python
@tool
def mcp_search(query: str) -> str:
    return mcp_client.call("search", {"query": query})
```

The workflow engine should not distinguish between:

* REST tools
* MCP tools
* internal tools

All should expose:

* structured schemas
* typed arguments
* deterministic execution

---

# Semantic Tool Discovery

## Problem

Large tool catalogs degrade:

* context quality
* tool selection accuracy
* reasoning reliability

---

## Required Solution

Implement semantic tool retrieval.

Workflow:

```text
Question / Current Step
        ↓
Embedding Search over Tool Catalog
        ↓
Top-K Relevant Tools
        ↓
Model Tool Selection
```

The model should never receive the full tool catalog.

---

## Tool Metadata Requirements

Every tool should include:

* name
* description
* argument schema
* example use cases
* tags/domains
* reliability notes

Tool descriptions should be optimized for:

* semantic retrieval
* model comprehension

---

# Research and Verification Strategy

## Research Phase

Goals:

* gather evidence
* retrieve sources
* collect structured information
* identify competing claims

Research nodes should:

* aggressively use tools
* avoid premature synthesis


---

## Compression Phase

Large tool outputs should be compressed before entering long-context reasoning.

Use the task model to:

* summarize
* normalize
* deduplicate
* extract key facts

This prevents:

* context exhaustion
* degraded reasoning quality

---

## Verification Phase

Verification should:

* re-check claims using tools
* compare against sources
* identify unsupported conclusions
* identify contradictions

Verification should be adversarial.

The verifier should assume:

> the draft may contain hallucinations or unsupported synthesis.

---

# UI / UX Guidelines

## User separated workspace environments

The UI should present each user with their own distinct workspace environment.
Each research task with questions, agent state, agent action history, and conclusions should be persisted in the database and reviewed whenever the user needs. 
Research should be organizable into user-named projects, with shared conclusion and other context between tasks in a project.

## Conversational Interface

The UI should remain chat-oriented.

However, the UI should expose:

* workflow stages
* tool calls
* intermediate outputs
* verification steps
* citations and evidence

Users should be able to inspect:

* why a conclusion was reached
* what tools were used
* what evidence was gathered

---

## Streaming Execution Visibility

The UI should stream intermediate execution state such as:

```text
[Planning]
[Selecting Tools]
[Researching]
[Summarizing]
[Drafting]
[Verifying]
[Refining]
```

This improves:

* transparency
* trust
* debuggability

## Visual theming

The UI should natively support themes and day/night mode. 

Themes can adjust any of the following, primarily through CSS:

* NaiveUI theme overrides
* Fonts for variable and fixed width body text, as well as headings
* Image placement in designated areas of the UI (like the title bar or footer)


The user can select a theme for each of light and dark mode. 
The app should ship with default light and dark themes, and eventually have the ability to add third party or custom themes.

---

# Reliability Principles

## Avoid Pure Autonomous Agent Behavior

The system should favor:

* constrained workflows
* bounded execution
* explicit graph structure

over:

* unconstrained recursive agents
* open-ended self-directed loops

---

## Prefer Deterministic Scaffolding

The workflow should guide the model.

The model should not:

* invent workflow structure
* invent execution order
* self-manage orchestration

---

## Tool Failures Must Be First-Class

Tool execution should support:

* retries
* timeout handling
* structured error propagation
* fallback behavior

---

# Observability and Debugging

The system should log:

* graph execution traces
* node transitions
* tool selections
* tool outputs
* model prompts
* verification failures

Execution history should be inspectable per session.

---

# Long-Term Goals

Potential future capabilities:

* multi-agent collaboration
* hypothesis branching
* citation graphs
* persistent memory
* domain-specialized research modes
* autonomous long-running research tasks
* user-interruptible workflows
* human approval checkpoints

---

# Recommended Initial Scope

## Phase 1

Build:

* LangGraph workflow runtime
* basic research loop
* REST tool integration
* MCP wrapper support
* semantic tool retrieval
* simple conversational UI

## Phase 2

Add:

* verification loops
* execution tracing
* streaming node visibility
* citation management
* session persistence

## Phase 3

Add:

* advanced planning
* parallel research branches
* multi-agent workflows
* long-running tasks
* persistent knowledge graph
