# What the document describes

MOiRA ("My Open intelligent Research Agent") is a self-hosted interactive research agent. The user opens a chat UI, asks a research question, and the system runs a deterministic
LangGraph workflow — Planning → Tool Discovery → Research Execution → Compression → Draft → Verification → Report Generation — using two LLM tiers (a larger "intelligence" model and a
smaller "task" model) accessed via OpenAI-compatible endpoints. Tools include REST APIs and MCP servers, retrieved by semantic similarity from a LanceDB index so the model only ever
sees a small relevant subset. A step-cost budget caps total work and governs how many times verification can fail and retry through Planning before the system gives up and writes a
report flagging unverified claims. Sessions, projects, tool metadata, and checkpoints persist in SQLite; everything goes through a repository abstraction so the storage layer can later
move to Postgres+pgvector. The TypeScript/NaiveUI frontend is treated as a window onto workflow state — it shows stage transitions, tool calls, budget consumption, and citations, and
supports per-user workspaces and light/dark themes.

That picture is internally sensible and matches the stated principles (transparency, structured tool use, verification as a separate phase, no hidden orchestration). The thing reads as
a coherent project.

# Coherence problems

**Contradiction in the verification retry budget check.** §"Verification Retry Loop" (line 610) says the graph checks budget_remaining against the cost of another full cycle (Planning +
Tool Discovery + Research Execution + Compression + Draft Synthesis + Verification — sums to 16 with the defaults). The verification node spec at line 311 says if budget_remaining >=
cost_weight(Planning) — i.e. just 2. These give very different behavior near the budget edge. Pick one and use it in both places.

**Report Generation can be locked out by the budget.** Report Generation has cost weight 3 and is asserted to be the always-terminal node. But nothing reserves 3 units for it. If the retry
loop consumes the budget down to <3, the document's own rule ("when budget_remaining falls below a node's cost weight, that node cannot execute and the graph must terminate") prevents
Report Generation from running — which contradicts "the graph always terminates through this node." Either Report Generation is exempt from the budget check, or the retry-loop check
needs to reserve its cost.

**verification_attempts is both a stored field and a derived value.** It appears in the ResearchReport TypedDict (line 250), and line 642 says it's "derived from len(verification_history)". One or the other.

**verification_history: list[str] is at odds with how the document describes verification output.** Verification produces unsupported claims, contradictions, and missing-evidence findings
— structured signals that the next Planning pass is supposed to use to "adjust the research plan to address specific failures." Plain strings throw that structure away. Consider
list[VerificationReport].

**llama-swap / llama.cpp vs. the Inference Backend section.** The architecture diagram (lines 117–129) puts llama-swap as the model router and llama.cpp as the inference backend. But
§"Inference Backend" only names OpenAI-compatible completions/responses APIs and §"Model Routing" describes routing to logical names like "intelligence" and "task" without mentioning
llama-swap by name. Either llama-swap is the canonical router (and should be named in the section) or it's one example among several (and the diagram should say so).

# Completeness gaps

**Conversation turns vs. workflow runs are undefined.** The system is described as conversational, but there is no description of what happens turn-to-turn. Does each user message start a
  new graph run? Does the next turn see prior turns' state, the prior ResearchReport, or only the chat transcript? This is load-bearing for a "stateful, tool-using research and reasoning
   system with interactive conversational access" and currently isn't specified.

**Where citations and support accumulate.** ResearchReport has citations and support, but ResearchState only has findings: list. The document doesn't say how research output is structured
during the run so that Report Generation can assemble these. Today it would be implicit that Report Generation re-mines findings, but the data shape should be stated.

**Tool catalog provisioning is unspecified.** Tools are persisted in SQLite, embedded in LanceDB, retrieved at runtime — but nothing says how a tool gets there. Manual config file? UI
form? Auto-discovery from MCP servers? This is the kind of detail that determines whether semantic tool discovery is usable on day one.

**MCP integration is underspecified.** The @tool def mcp_search(...) example hand-wraps a single MCP call, but real MCP servers expose dynamic tool sets. How a server's tools become typed,
schema-bearing entries in the catalog — and whether wrappers are generated at startup vs. lazily — is not described.

**Embedding model.** Semantic tool discovery requires an embedding model. Not specified — same OpenAI-compatible endpoint as inference? Separate config? Local-only?

**Auth/identity model.** Schemas mention users and "user separated workspace environments," but it's not stated whether this is single-user self-hosted, multi-user with auth, or
LAN-multi-user-trust. For a self-hosted tool that's a real decision.

**Node-level failure handling.** Tool failures are first-class (retries, timeouts). Node failures (model timeout, malformed structured output, transient exceptions) aren't mentioned.
LangGraph checkpoints are noted in storage but not tied to recovery semantics.

**Theme packaging.** Themes can override NaiveUI tokens, fonts, and images, but the unit of distribution (CSS file? JSON manifest? plugin?) isn't defined, which makes "third-party or custom themes" hard to scope.

# Smaller things worth a pass

- The flowchart at lines 210–214 is hard to parse visually — the └──→ Report Generation ←──┘ lines suggest two arrows feeding one node, which is the intent, but the ASCII is messy.
- "Tool Discovery / Selection" as one node conflates retrieval (the system embeds & queries) with selection (the model chooses from top-K). Reads fine in one direction; worth splitting if they have different cost weights or models.
- active_tools: list in state — is this the top-K retrieved, or the subset the model committed to using this step? Both are reasonable; pick one.
- unverified_claims lives only in ResearchReport. The Verification node clearly needs to write candidates somewhere in ResearchState between attempts.

Nothing here invalidates the overall design. The two issues I'd treat as blocking are the retry-budget contradiction and Report Generation being budget-lockable, because they directly
change runtime behavior. The conversation/turn semantics gap is the biggest missing piece — without it, you can't actually build the UI loop.