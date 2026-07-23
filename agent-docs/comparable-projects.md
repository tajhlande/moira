# Comparable Projects

These are other deep research-oriented projects that are the closest self-hostable equivalents, ordered roughly by how much they overlap MOiRA.

We aim to benchmark MOiRA against these at some point.

**Architecturally closest — LangChain's local deep researcher** (the `ollama-deep-researcher` / "local deep research" reference implementation). Local-first, LangGraph-based, iterative search→summarize→reflect→search loop, works with Ollama + a SearXNG-style backend. It's the nearest cousin to MOiRA's actual construction. But it's a simpler loop — no separate verification and evaluation nodes, no fact-status lifecycle, no structured knowledge model. It gathers and writes; it doesn't adjudicate.

**Most turnkey / popular — GPT Researcher** (`assafelovic/gpt-researcher`). The most established open research agent: plans a question, hits multiple sources, produces a long cited report. Self-hostable, can run against local models. Same product category as MOiRA, but it's an autonomous-loop-plus-report design, not a structured-verification design. No first-class verified knowledge graph, no explicit contradiction handling.

**Closest on rigor/structure — STORM (Stanford)**. Produces long, multi-perspective, heavily cited, Wikipedia-style articles with a structured research process. Self-hostable. It shares MOiRA's "structured, cited, multi-viewpoint" DNA more than the others — but it's oriented to article generation, not interactive Q&A with a verification loop.

**Open reproductions of "Deep Research"** — HuggingFace's Open Deep Research (smolagents, code-agent style) and LangChain's `open_deep_research`. Both are self-hostable takes on the OpenAI/Gemini Deep Research pattern. Capable, but again: report-generation loops, not verified-fact-graph systems.

**Adjacent but more answer-engine than research-agent** — Khoj, Morphic, Farfalle (and Perplexica). Faster, shallower, conversational. These are the Perplexica end of the spectrum, not the MOiRA end.

The honest synthesis: the "self-hostable, produces cited research reports" axis is **crowded**. The "structured knowledge model with a fact-status lifecycle, separate research-review and evaluation nodes, cross-fact contradiction, and runtime tool ingestion" axis is **nearly empty** — none of the mainstream ones do the verification-as-a-first-class-phase thing that is MOiRA's actual distinctive claim. So "what's equivalent?" has two answers: on category, several things; on the thing that makes MOiRA MOiRA, not much.

Two practical implications:

1. **These are your external baselines.** The eval-harness plan deferred external baselines, but GPT Researcher and the LangChain local deep researcher are the obvious ones — same job, self-hostable, can run the same model + SearXNG. Running your 7 questions through GPT Researcher and scoring its output with your existing judge would tell you, concretely and defensibly, whether MOiRA's verification actually buys measurable quality over a strong autonomous-loop baseline. That's the comparison that would validate (or puncture) the whole thesis — far more than comparing against a plain search tool.
2. **They're idea sources.** STORM's multi-perspective structuring and GPT Researcher's source-handling are worth reading against your own design.

If you do only one thing with this list: pick GPT Researcher, point it at your model and SearXNG, run the Cheetos question and the telescope question, and judge both outputs with your harness. Whatever the result, you'll finally have the baseline number the project has been missing since the beginning.