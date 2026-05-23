# Architecture Adjustments Plan

Based on external review in architecture-critique.md.

---

## Blocking Issues

### 1. Fix retry-budget check contradiction

**Problem**: "Verification Retry Loop" says check against full cycle cost (16), but the Verification node spec says `budget_remaining >= cost_weight(Planning)` (just 2). These produce very different behavior near the budget edge.

**Resolution**: Use full cycle cost everywhere. With the split of Tool Discovery/Selection into two nodes, the full cycle is: Planning (2) + Tool Discovery (1) + Tool Selection (2) + Research Execution (5) + Compression (1) + Draft Synthesis (3) + Verification (4) = 18. The retry loop checks `budget_remaining >= 18` before routing back to Planning.

**Files affected**: architecture.md — Verification Routing Logic, Verification node example, Verification Retry Loop, Step-Cost Budget interaction.

### 2. Report Generation must always run (budget-exempt)

**Problem**: Report Generation has cost weight 3 and is asserted to be the always-terminal node. But nothing reserves 3 units. If the retry loop consumes budget to <3, the budget rule prevents Report Generation from running — contradicting "the graph always terminates through this node."

**Resolution**: Report Generation is exempt from the budget check. It always runs as the terminal node regardless of remaining budget. Its cost weight is tracked for accounting/logging purposes but never gates execution.

**Files affected**: architecture.md — Step-Cost Budget mechanism, cost weight table, Report Generation Phase.

---

## State and Type Definition Changes

### 3. Add VerificationReport TypedDict

**Problem**: `verification_history: list[str]` throws away the structured failure data that the Planning node needs to adjust its plan.

**Resolution**: Define a `VerificationReport` TypedDict with structured fields:

```python
class VerificationReport(TypedDict):
    failed_claims: list[str]
    contradictions: list[str]
    missing_evidence: list[str]
    suggestions: list[str]
```

Change `verification_history` from `list[str]` to `list[VerificationReport]`.

**Files affected**: architecture.md — State-Centric Design section.

### 4. Add Finding TypedDict

**Problem**: `findings: list` and `compressed_findings: list` are untyped. Report Generation needs to assemble citations and support from these, but the data shape is unspecified.

**Resolution**: Define a `Finding` TypedDict:

```python
class Finding(TypedDict):
    content: str
    source: str
    citation_url: NotRequired[str]
    type: str  # "evidence" | "citation" | "contradiction" | "note"
```

Change `findings` and `compressed_findings` to `list[Finding]`.

**Files affected**: architecture.md — State-Centric Design section.

### 5. Remove verification_attempts from ResearchReport

**Problem**: `verification_attempts` appears as a stored field in `ResearchReport` but is also described as "derived from `len(verification_history)`." Redundant.

**Resolution**: Remove from `ResearchReport` TypedDict. Compute at report generation time from `len(verification_history)`.

**Files affected**: architecture.md — State-Centric Design, Report Generation Phase.

### 6. Add unverified_claims to ResearchState

**Problem**: `unverified_claims` only exists in `ResearchReport`. The Verification node produces these and they need to survive in `ResearchState` between verification and report generation, especially on the budget-exhausted path.

**Resolution**: Add `unverified_claims: list[str]` to `ResearchState`.

**Files affected**: architecture.md — State-Centric Design section.

### 7. Clarify active_tools semantics

**Problem**: Unclear whether `active_tools` is the top-K retrieved or the model-selected subset.

**Resolution**: `active_tools` is the model-selected subset — the tools the Tool Selection node committed to for the current workflow step. The raw top-K retrieval results are ephemeral and do not persist in state.

**Files affected**: architecture.md — State-Centric Design section.

---

## Workflow Diagram Changes

### 8. Split Tool Discovery / Selection into two nodes

**Problem**: "Tool Discovery / Selection" conflates retrieval (system-driven LanceDB query) with selection (model chooses from top-K). They have different cost weights and different models.

**Resolution**: Split into:
- **Tool Discovery** — system-driven, embedding search over LanceDB, no model, cost weight 1
- **Tool Selection** — intelligence model, chooses from top-K results, cost weight 2

**Files affected**: architecture.md — Canonical Research Workflow diagram, cost weight table, all references to the combined node.

### 9. Clean up workflow diagram ASCII art

**Problem**: The `└──→ Report Generation ←──┘` convergence lines are visually messy.

**Resolution**: Redraw the diagram for clarity.

**Files affected**: architecture.md — Canonical Research Workflow.

---

## Inference Section Changes

### 10. Clarify llama-swap / llama.cpp as reference implementations

**Problem**: Architecture diagram names llama-swap and llama.cpp specifically, but Inference Backend section only describes OpenAI-compatible APIs generically.

**Resolution**: Update diagram to `Model Router (e.g. llama-swap)` and `Inference Backends (e.g. llama.cpp)`. Add note that these are reference implementations; any OpenAI-compatible endpoint works.

**Files affected**: architecture.md — Application Design Principles architecture diagram, Inference Infrastructure section.

---

## New Sections to Add

### 11. Conversation and Turn Semantics

**Content**:
- Each user message starts a fresh graph run
- The prior ResearchReport is always included as context for the new run
- The full chat transcript is available to the agent via a dedicated tool (not stuffed into context by default)
- The report serves as a sufficient summary of prior work; the transcript provides detail on demand

**Location**: New section under Application Design Principles, or new top-level section before High-Level Workflow.

### 12. Tool Catalog Provisioning

**Content**:
- MVP: YAML/Python config file listing tools with schemas, endpoints, and metadata, parsed at startup
- Tools are stored in SQLite (metadata) and indexed in LanceDB (embeddings) at startup
- Future: auto-ingestion of OpenAPI endpoints and MCP server tool sets from a URL + auth info
- Future: UI form for adding/editing tools

**Location**: New subsection under Tool Architecture.

### 13. Embedding Model

**Content**:
- Tool discovery requires an embedding model to produce vectors from tool descriptions and queries
- MVP: local embedding model via sentence-transformers (e.g. all-MiniLM-L6-v2), runs on CPU, ~80MB
- Embedding is a separate concern from vector storage — LanceDB stores vectors, the embedding model produces them
- The embedding interface should be swappable (local model → OpenAI-compatible endpoint) behind an abstraction layer
- Scale is small: catalog is dozens to low hundreds of tools, embedding happens at ingestion and query time

**Location**: New subsection under Inference Infrastructure or Semantic Tool Discovery.

### 14. Auth and Identity

**Content**:
- Single-user by default for MVP — no authentication required
- Data model supports user identity from the start (user ID field on sessions, projects, etc.) so multi-user can be added without schema migration
- Future: multi-user with simple auth (username/password or API key)

**Location**: New section under Application Design Principles or UI/UX Guidelines.

### 15. Node-Level Failure Handling

**Content**:
- Every node should have a LangGraph `RetryPolicy` for transient failures (model timeout, API errors)
- Every node should have an `error_handler` that routes to Report Generation with an error flag (similar to budget exhaustion)
- LangGraph checkpoints enable state recovery after failures — the system can resume from the last successful node
- Report Generation handles three terminal conditions: verification pass, budget exhaustion, and node failure

**Location**: New section under Reliability Principles, or expand existing "Tool Failures Must Be First-Class" to cover node failures.

### 16. Theme Packaging

**Content**:
- A theme is a ZIP archive containing:
  - `manifest.json` — theme metadata (name, author, version), references to CSS overrides, font URLs, image assets, NaiveUI token overrides
  - CSS files for style overrides
  - Font files or font URL references
  - Image assets (title bar, footer, etc.)
- The app ships with default light and dark themes in this format
- Third-party themes are distributed as ZIP archives in the same format

**Location**: Expand existing Visual Theming section.

---

## Minor Fixes

### 17. Update Verification node example

- Routing check uses full cycle cost (18 with the split), not just `cost_weight(Planning)`
- Add `unverified_claims` to outputs

### 18. Update all retry logic descriptions consistently

- Verification Routing Logic, Verification Retry Loop, Step-Cost Budget interaction — all three must say the same thing
- Full cycle cost = 18
- Report Generation is budget-exempt

### 19. Update Report Generation Phase

- Report as one of three terminal conditions: verification pass, budget exhaustion, node failure
- `verification_attempts` is computed, not stored
- Add error flag behavior for node failures

### 20. Update cost weight table

- Split Tool Discovery (1, system, no model) and Tool Selection (2, intelligence model)
- Note that Report Generation's cost is tracked for accounting but does not gate execution

---

## Execution Order

1. State and type definition changes (items 3-7) — foundational
2. Workflow diagram changes (items 8-9) — depends on state changes
3. Budget fixes (items 1-2) — depends on state and diagram changes
4. Inference section changes (item 10) — independent
5. New sections (items 11-16) — independent of each other
6. Minor fixes (items 17-20) — depends on all above
