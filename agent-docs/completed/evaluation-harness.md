# Evaluation Harness

Active plan for building the evaluation harness described in
[`claude-assessment.md`](./claude-assessment.md) ("How to make the harness for
evaluation" section). The goal: replace "did that feel better?" with "did the
score move?" — a repeatable harness that scores MOiRA runs against a rubric
and lets you diff results across commits.

## Problem

The critique in `claude-assessment.md` (point 4 under "Where the unique value
is thinner than it looks") and in `completed/research-loop-critique.md`
(weakness #2) is the same: **there is no evaluation harness.** Without a fixed
test set and a repeatable scoring path, it is impossible to know whether
architectural changes are producing better answers or just more elaborate
ones.

What exists today is partial:

- `backend/moira_eval/capture.py` — pulls tool trace and search counts from
  SQLite into `artifacts/<run_id>.json`. **Stale:** it still queries a
  `verification` node, but the verification split landed (`research_review` +
  `evaluation`), and it does not extract the knowledge snapshot.
- `backend/moira_eval/rubric_pokemon.py` — the **Pokemon-specific** 8-category rubric
  (0/1/2, hard-fail categories) for the Tyranitar canary from
  `completed/research-loop-critique.md`. This is *not* the general rubric
  `claude-assessment.md` describes.

Missing: a general rubric, a frontier-model judge, a stable question set,
mechanical-metric computation from the knowledge snapshot, per-commit storage,
and a diff command.

## Conceptual design

### What the harness is

The harness is a **scoring pipeline over completed runs.** It does not run the
research graph and it does not make tool calls. Its input is a finished run
sitting in SQLite; its output is a small, diffable result file. The sole
purpose is to turn a run into numbers you can compare against another run's
numbers.

This separation is the load-bearing design choice. Coupling scoring to live
execution would make every score depend on the weather — search provider
availability, model temperature, network. By scoring only completed runs, the
pipeline becomes repeatable, runnable in CI, and immune to the fragility the
agent itself has.

### The pipeline

A run flows through five stages. Only the first reads from the database; the
rest are pure functions of the previous stage's output.

```
 ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
 │ completed│    │ artifacts│    │  metrics │    │  scores  │    │  result  │
 │   run in │──▶ │  (full   │──▶ │(determin-│──▶ │(frontier │──▶ │  file    │
 │  SQLite  │    │ snapshot)│    │  istic)  │    │  model)  │    │ (per     │
 └──────────┘    └──────────┘    └──────────┘    └──────────┘    │ commit)  │
   capture           capture         metrics         judge        └──────────┘
                                                                     │
                                                                     ▼
                                                                  ┌────────┐
                                                                  │  diff  │ (compares result files)
                                                                  └────────┘
```

| Stage | Input | Output | LLM? |
|---|---|---|---|
| **Capture** | run_id | `artifacts` dict: report + knowledge snapshot + tool trace + all review/evaluation attempts | No |
| **Metrics** | `artifacts` | `metrics` dict: counts and ratios derived mechanically from the snapshot | No |
| **Judge** | `artifacts` + `metrics` + rubric | `scores`: per-category scores with rationale, one overall result | Yes (frontier model) |
| **Store** | `scores` + `metrics` + question context | result file on disk, keyed by commit + question | No |
| **Diff** | two result-file directories | side-by-side delta view | No |

Only one stage calls an LLM. Everything else is deterministic, so most
regressions show up for free without spending judge tokens.

### Why metrics and judge are separate

They catch different things and they cost different amounts.

- **Metrics are deterministic and free.** `web_search_calls`,
  `unsupported_conclusion_count`, `hallucinated_fact_id_count`,
  `unknown_fact_count` — these fall out of the captured snapshot with no model
  in the loop. A change that adds two `web_search` calls or one hallucinated
  fact ID shows up the moment you run the harness, regardless of whether the
  judge is configured.
- **The judge catches what metrics can't.** "Does this conclusion actually
  follow from these facts?" and "did verification rubber-stamp a shaky draft?"
  require reading. The frontier model applies the rubric to the artifacts and
  returns structured scores with rationale.

This is also why metrics are passed *into* the judge as part of its prompt —
the judge shouldn't have to re-derive "web_search was called 14 times" from
the trace; it should be told, and asked to weigh it.

### Why two rubrics

The two rubrics serve different purposes and operate at different granularities.

- **The general rubric** (5 criteria, 1–5 each) applies to any question. It
  scores the disciplines MOiRA claims to be good at: grounding, fact
  atomicity, citation support, critique quality, goal alignment. Every
  question in the set is scored against this.
- **The Pokemon rubric** (8 categories, 0/1/2 each, with hard-fail categories)
  is domain-specific. It exists because the Tyranitar Gen9 OU canary is the
  project's primary stress test (see `comment-on-pokemon-as-test.md`), and the
  domain has correctness checks generic rubrics can't express: type
  correctness, ability correctness, OU legality vs. metagame relevance,
  typical-move discipline. A run that scores well on the general rubric but
  gets a type matchup wrong is still a failure.

Both rubrics share a dataclass shape so the judge, runner, and diff treat them
uniformly. The question definition declares which rubric (or both) applies.

### The role of the knowledge snapshot

This is the input the harness has that no hosted research agent offers. The
snapshot persisted in `workflow_runs.knowledge_snapshot` contains the full
structured graph — facts with status lifecycles (`unknown → unverified →
verified/contradicted`), conclusions with `supporting_fact_ids` and status
(including `unsupported`), citations carrying their source text, full
review/evaluation history. Most metrics derive from this graph directly, and
the judge gets it as structured input rather than having to reconstruct claims
from prose.

This is also why `hallucinated_fact_id_count` is a first-class metric: it's a
graph-integrity check that only makes sense because the snapshot is a graph,
not a report with footnotes.

### Data model at a glance

The pipeline carries four shapes between stages:

- **Artifacts** — the full captured state of one run. Large. Contains:
  question, report (answer + critiques), knowledge (facts, conclusions,
  citations, entities, concepts), every review attempt, every evaluation
  attempt, the complete tool trace, budget consumption.
- **Metrics** — flat dict of numbers. Small. Deterministic.
- **Scores** — per-category `{score, rationale}` plus an overall `{passed,
  total, max_total}`. Small. Probabilistic.
- **Result file** — question + run_id + metrics + scores + timestamp + model
  info. The canonical stored artifact, keyed by `<commit>/<question-id>.json`.

The artifacts dict is the only large object; it is captured once and reused by
both metrics and judge. Result files store everything except the artifacts
dict by default (to keep the diffable surface small); the full artifact dump
is opt-in.

### Scoring lifecycle

The intended rhythm:

1. **Make a change** to MOiRA (prompt edit, node logic, budget tuning, etc.).
2. **Seed runs** for the question set — either via the UI as normal, or via
   `invoke.py` which POSTs questions to a running backend:
   ```
   ./run.sh eval:invoke --all
   ```
3. **Score the runs** — either one at a time with `run.py`, or all at once
   with `batch.py`:
   ```
   # single question
   ./run.sh eval --db backend/moira.db --question-id tyranitar-ou

   # all questions at once
   ./run.sh eval:batch --db backend/moira.db
   ```
   Both write result files under `moira_eval/results/<commit-sha>/`.
4. **Diff against the previous commit's results** with `diff.py` to see
   whether the change moved the scores.
5. **Log meaningful advances** with `log.py`, which appends a structured
   entry to a tracked `EVAL_LOG.md`. Most runs are not worth logging; this
   is a deliberate "this result matters" act, not an automatic step.

Result files are **not** committed — see Iteration 4. They live under
`moira_eval/results/` which is gitignored. The `EVAL_LOG.md` file *is*
committed — it's the durable score history, one entry per meaningful run.

## Design decisions

These were resolved upfront; they fall out of the conceptual design above.

1. **Capture-only core.** The scoring path reads completed runs from SQLite
   and never makes live tool calls. A separate thin `invoke.py` script kicks
   off runs through the running backend's HTTP API for convenience. Scoring
   and execution are decoupled — this is what makes the pipeline repeatable
   and CI-runnable.
2. **Judge via env vars.** `MOIRA_EVAL_JUDGE_ENDPOINT`,
   `MOIRA_EVAL_JUDGE_MODEL`, `MOIRA_EVAL_JUDGE_API_KEY`. The judge stands up
   an `InferenceClient` directly — bypasses `ModelRegistry` and the
   DB-backed provider system so the judge can be a different provider than
   what the agent used (and so the harness has no DB dependency).
3. **Both rubrics.** Ship the general 5-criterion rubric from
   `claude-assessment.md` *and* keep the existing 8-category Pokemon rubric
   for the Tyranitar canary. Each question declares which rubric applies.
4. **MOiRA-only baselines.** Commit-to-commit diff within MOiRA. The storage
   shape leaves room to import external baseline answers (OpenAI DR, Claude
   Research) later without rework, but that is out of scope now.
5. **`--question-id` is always optional.** The question text is available in
   the knowledge snapshot (`knowledge.question`), so the judge can score any
   run without a predefined question. Rubric resolution:

   | Invocation | Question text | Rubric |
   |---|---|---|
   | `--question-id tyranitar-ou` | From `questions.py` | Pokemon (declared by question) |
   | `--question-id multi-entity` | From `questions.py` | General (declared by question) |
   | No `--question-id` (ad hoc run) | From `knowledge.question` | General |

   In Iteration 2 (only the Pokemon rubric exists), ad hoc runs without
   `--question-id` fall back to metrics-only. Once the general rubric lands
   in Iteration 3, ad hoc runs get full judge scoring with the general
   rubric. Result files for ad hoc runs use `adhoc-<run-id-short>` as the
   question-id slot in the filename.

## File layout

The pipeline stages map directly to modules. Each stage is one file plus its
tests.

```
backend/moira_eval/
  __init__.py
  capture.py          # REFACTOR (Iter 1) — stage 1: artifacts extraction
  metrics.py          # NEW (Iter 1) — stage 2: deterministic metrics
  run.py              # NEW (Iter 1, extended in Iter 2-3) — orchestrator CLI
  judge.py            # NEW (Iter 2) — stage 3: frontier-model judge
  prompts.py          # NEW (Iter 2) — POKEMON_JUDGE_SYSTEM; GENERAL added in Iter 3
  rubric_pokemon.py           # EXISTING — Pokemon 8-category rubric (used in Iter 2)
  rubric_general.py   # NEW (Iter 3) — general 5-criterion rubric
  questions.py        # NEW (Iter 3) — stable question set
  diff.py             # NEW (Iter 4) — compare two result-set directories
  log.py              # NEW (Iter 4) — append meaningful results to tracked EVAL_LOG.md
  invoke.py           # NEW (Iter 5) — convenience: POST questions to a running backend
  batch.py            # NEW (Iter 5) — batch-evaluate all predefined questions
  results/            # NEW (Iter 2, formalized in Iter 4) — gitignored per-commit results

backend/moira_eval_tests/
  test_evaluation_capture.py    # (Iter 1)
  test_evaluation_metrics.py    # (Iter 1)
  test_evaluation_judge.py      # (Iter 2)
  test_evaluation_run.py        # (Iter 2)
  test_evaluation_rubric_general.py  # (Iter 3)
  test_evaluation_questions.py  # (Iter 3)
  test_evaluation_diff.py       # (Iter 4)
  test_evaluation_log.py        # (Iter 4)
  test_evaluation_invoke.py     # (Iter 5)
  test_evaluation_batch.py      # (Iter 5)
```

`rubric_pokemon.py` and `rubric_general.py` are siblings rather than one file because
they have different category counts, different scales (0/1/2 vs 1–5), and
different hard-fail semantics. They share a dataclass shape so the rest of
the pipeline is rubric-agnostic.

## Implementation iterations

The harness is built in vertical slices. Each iteration delivers a runnable,
useful capability end-to-end — not a horizontal layer that only becomes
useful when combined with later phases. Run the verification commands (below)
after each iteration.

### Iteration 1 — Mechanical metrics on any run

**Goal:** given a completed run, print its mechanical metrics without calling
any LLM.

**Why this first:** metrics are deterministic, free, and catch most
regressions on their own. This iteration also fixes the stale `verification`-
node bug in `capture.py` and makes the knowledge snapshot — MOiRA's unique
structured input — available as a scoring surface for the first time.

**What you can do after this iteration:**

```
uv run python -m moira_eval.run --db backend/moira.db --run-id <uuid>
```

Output (example):

```
run <uuid>: web_search=14 | total_tools=22 | unknown_facts=3
unsupported_conclusions=1 | hallucinated_ids=0 | budget=48/60 (80%)
```

No judge, no rubric, no storage — just capture + metrics + print. Already
enough to see whether a change made `web_search` calls go up or
hallucinated fact IDs appear.

**What's built:**

1. **Refresh `capture.py`** (fixes stale node names + adds knowledge snapshot):
   - Replace `_extract_verification_output` (which queries a `verification`
     node that no longer exists — see `completed/verification-split.md`)
     with `_extract_review_output(steps)` (reads `research_review` step's
     `detail.structured_output`: a `ReviewOutcome`) and
     `_extract_evaluation_output(steps)` (reads `evaluation` step's
     `detail.structured_output`: an `EvaluationOutcome`).
   - Capture **all** retry attempts, not just the first — return as
     `review_attempts: list[dict]` and `evaluation_attempts: list[dict]`
     (matching the shape in `api/routes/conversations.py:91-120`).
   - Add `_extract_knowledge(run)` — parses `workflow_runs.knowledge_snapshot`
     JSON into `{question, user_goal, topic, entities, concepts, facts,
     conclusions, citations}`. This is where the knowledge model in
     `moira/models/knowledge.py` becomes scoring input.
   - Add `critiques` extraction from the final report.
   - Keep `capture_artifacts(db_path, run_id)` signature stable; enrich
     the returned dict.

2. **New `metrics.py`** — `compute_metrics(artifacts: dict) -> dict`. Pure
   functions, no LLM. All the mechanical metrics:

   | Metric | Source | Why it matters |
   |---|---|---|
   | `web_search_calls` | step `tool_results` | overuse failure mode |
   | `total_tool_calls` | step `tool_results` | baseline effort |
   | `tools_used` | step `tool_results` | tool diversity |
   | `specialized_tool_use_ratio` | `tools_used` vs candidate tool catalog | fact-type -> tool routing health |
   | `unknown_fact_count` | `knowledge.facts` where `status="unknown"` | decomposition/research gaps |
   | `unverified_fact_count` | `knowledge.facts` where `status="unverified"` | verification discipline |
   | `contradicted_fact_count` | `knowledge.facts` where `status="contradicted"` | contradiction detection |
   | `unsupported_conclusion_count` | `knowledge.conclusions` where `status="unsupported"` | synthesis overreach |
   | `hallucinated_fact_id_count` | `conclusion.supporting_fact_ids` not in `facts[].id` | graph integrity (same check `research_review` does at runtime) |
   | `uncited_conclusion_count` | conclusions with empty `citation_ids` | grounding discipline |
   | `budget_consumed_ratio` | `budget_consumed / budget_limit` | efficiency |
   | `retry_count` | `research_count`, `review_count`, `evaluation_count` | fragility signal |

3. **Minimal `run.py`** — CLI that wires capture -> metrics -> print. This
   is the seed of the orchestrator; later iterations extend it with judge
   scoring, storage, and batch modes.

**Tests:** `moira_eval_tests/test_evaluation_capture.py` (fixture DB with knowledge snapshot
+ review/eval steps -> assert enriched dict, all retry attempts captured);
`moira_eval_tests/test_evaluation_metrics.py` (pure-function tests over fixture artifacts,
including the hallucinated-ID case explicitly).

### Iteration 2 — First judged score (the canary, end-to-end)

**Goal:** score one run with a frontier-model judge against the existing
Pokemon rubric, write the result to disk.

**Why this next:** this is the first "did the score move?" moment. Uses the
existing Tyranitar canary (`CANARY_QUESTION` in `rubric_pokemon.py`) and the existing
8-category Pokemon rubric — no new rubric needed yet. Proves the judge +
storage path end-to-end before generalizing.

**What you can do after this iteration:**

```
# judge env vars set, canary question
uv run python -m moira_eval.run --db backend/moira.db --run-id <uuid> \
    --question-id tyranitar-ou

# ad hoc run (no --question-id) — metrics only in Iter 2,
# full judge scoring with general rubric from Iter 3 onwards
uv run python -m moira_eval.run --db backend/moira.db --run-id <uuid>
```

Output (with question-id):

```
tyranitar-ou: 12/16 (PASS) | web_search=14, unsupported=1, halluc_ids=0
  -> moira_eval/results/<commit>/tyranitar-ou.json
```

If judge env vars are not set, or if no `--question-id` is given (no general
rubric exists yet in this iteration), falls back to metrics-only mode
(Iteration 1 behavior) with a warning.

**What's built:**

1. **New `judge.py`** — reads env vars `MOIRA_EVAL_JUDGE_ENDPOINT`,
   `MOIRA_EVAL_JUDGE_MODEL`, `MOIRA_EVAL_JUDGE_API_KEY`. Stands up
   `moira.inference.client.InferenceClient` directly (no DB, no registry).
   One public method:

   ```python
   async def score_run(artifacts, metrics, rubric) -> JudgeResult
   ```

   `JudgeResult` = list of `(category, score, rationale)` + overall notes.
   Parses structured JSON the judge returns; reuse the JSON-repair helpers
   from `moira.workflow.nodes._helpers` if general enough, otherwise add a
   small local parser.

2. **New `prompts.py`** — starts with just `POKEMON_JUDGE_SYSTEM`, wrapping
   the existing 8-category rubric with 0/1/2 anchors and hard-fail
   categories from `rubric_pokemon.py`. The user message includes: question, report
   answer, full fact/conclusion/citation graph, verification outputs,
   mechanical metrics, and a strict "grade the rubric, don't re-answer"
   instruction. The "unsupported != contradicted" distinction is preserved
   (per the assessment's praise of the existing evaluator prompt).

   **Critique-quality scoring stance:** the judge grades whether verification
   *did* catch the weaknesses visible in the run — not whether it *should*
   have caught weaknesses the judge identifies independently. This is
   simpler and more stable to score than asking the judge to first discover
   flaws and then check whether verification found them. The judge sees the
   agent's own review/evaluation output and assesses whether it pushed back
   appropriately on what was actually weak in that specific run.

3. **Storage (minimal)** — `run.py` writes
   `moira_eval/results/<commit-sha-short>/<question-id>.json` containing
   question, run_id, metrics, judge scores, timestamp, model info. Add
   `moira_eval/results/` to `.gitignore` now (see Iteration 4 for the full
   storage rationale).

4. **Extend `run.py`** — after metrics, conditionally call the judge (if
   env vars set), write result file, print one-line summary.

**Judge caveat** (from `claude-assessment.md`, baked into the prompt and
docs): the judge is calibrated to itself, not to truth. It catches
reasoning/grounding regressions reliably; it misses subtle factual errors
where the judge doesn't know the domain. For domains MOiRA cares most
about, a periodic hand-graded pass keeps the LLM judge honest.

**Tests:** `moira_eval_tests/test_evaluation_judge.py` (mock `InferenceClient`; assert prompt
construction, JSON parsing, malformed-JSON tolerance); extend
`moira_eval_tests/test_evaluation_run.py` with end-to-end test using mocked judge, assert
result file written, metrics-only fallback works without env vars.

### Iteration 3 — General rubric + question set

**Goal:** score *any* question, not just the canary, with a rubric
appropriate to the question. Ad hoc runs (no `--question-id`) now get full
judge scoring with the general rubric.

**Why this next:** the canary proved the path. Now generalize so the
question set can grow beyond Pokemon and so non-domain-specific questions
get the discipline-based scoring the assessment calls for. This also
unlocks ad hoc run scoring — any workflow run can be judged, not just
predefined questions.

**What you can do after this iteration:**

```
# predefined question with general rubric
uv run python -m moira_eval.run --db backend/moira.db --run-id <uuid> \
    --question-id multi-entity
# run.py resolves the question's rubric (general) and scores accordingly

# ad hoc run — question text from knowledge snapshot, general rubric
uv run python -m moira_eval.run --db backend/moira.db --run-id <uuid>
# output uses adhoc-<run-id-short> as the question label
```

**What's built:**

1. **New `rubric_general.py`** — five criteria from `claude-assessment.md`,
   each scored 1–5: grounding, fact atomicity, citation support, critique
   quality, goal alignment. Mirrors the dataclass shape of `rubric_pokemon.py`
   (`CategoryScore`, `RunScore`, `create_empty_scorecard`,
   `CATEGORY_DESCRIPTIONS`) so the judge and runner are rubric-agnostic.
   Pass/fail is driven by three mechanisms (see judge.py `_RubricSpec`):
   any category scoring ≤2 triggers a hard fail, a total below 15 triggers
   a hard fail, and there are no named hard-fail categories (those are
   Pokemon-specific).

2. **Add `GENERAL_JUDGE_SYSTEM`** to `prompts.py` — describes the 5
   criteria, 1–5 scale anchors, "unsupported != contradicted" distinction,
   output JSON schema.

   **Include the available tool catalog in the user message.** Both the
   Pokemon and general judge prompts should list the tools that were
   available to the agent during the run (name + brief description). Without
   this, the judge guesses about tools it *thinks* should exist and penalizes
   the agent for not using tools it never had access to. The tool list can
   be captured from the run's config or a static catalog in `capture.py`.
   This applies retroactively to the Pokemon prompt from Iteration 2 as
   well — add it there when implementing.

3. **New `questions.py`** — `Question` dataclass (`id`, `text`, `rubric`,
   `tags`, `notes`) and the starter question set. Ship 4 to start:

   | ID | Question | Rubric | Failure mode stressed |
   |---|---|---|---|
   | `tyranitar-ou` | Existing Tyranitar Gen9 OU canary (reuse `CANARY_QUESTION`) | pokemon | fact-rich retrieval; type/ability/move/legality; synthesis discipline |
   | `oversearch-bait` | A question where the agent historically burned `web_search` (TBD) | general | search discipline, tool routing |
   | `synthesis-trap` | A question where individually-true facts tempt an unsupported conclusion | general | synthesis overreach, verification pushback |
   | `multi-entity` | A question requiring many named entities with interacting facts | general | decomposition coverage, ID discipline |

   `tyranitar-ou` reuses the existing canary. The other three need user
   input — see open questions in [`QUESTIONS.md`](./QUESTIONS.md).

4. **Extend `run.py`** — `--question-id` resolves the question definition
   and its rubric; the runner picks the right judge prompt based on rubric
   type. Without `--question-id`, the question text comes from
   `knowledge.question` in the snapshot and the general rubric is used.
   The Pokemon and general rubrics flow through the same judge +
   storage path. Result files for ad hoc runs use `adhoc-<run-id-short>`
   as the question-id slot.

**Tests:** `moira_eval_tests/test_evaluation_rubric_general.py` (dataclass shape parity with
`rubric_pokemon.py`; scorecard factory; hard-fail threshold logic);
`moira_eval_tests/test_evaluation_questions.py` (question set loads, rubric assignment
resolves, `tyranitar-ou` text matches `CANARY_QUESTION`).

### Iteration 4 — Diff + log (the comparison and tracking layer)

**Goal:** compare two scored runs side-by-side, and record meaningful
advances in a durable log.

**Why this next:** this is what turns individual scores into signal over
time. Without diff, you're staring at numbers in isolation. Without log,
meaningful results disappear when you wipe local results/.

**What you can do after this iteration:**

```
# diff two commits' results
uv run python -m moira_eval.diff \
    --a moira_eval/results/<commit-1> \
    --b moira_eval/results/<commit-2>

# log a meaningful result to the tracked log
uv run python -m moira_eval.log \
    --result moira_eval/results/<commit>/tyranitar-ou.json \
    --note "tightened synthesis prompt; type-correctness 1->2"
```

**What's built:**

1. **New `diff.py`** — side-by-side table per question: total score change,
   per-category deltas, metric deltas (web_search calls, unsupported
   conclusions, hallucinated IDs). Color-coded +/- in the terminal. This is
   the "did the score move?" view the whole harness exists to produce.

2. **New `log.py`** — deliberate, manual step. Takes an existing result file
   (or a whole `results/<commit>/` directory in batch mode) and appends a
   structured entry to tracked `EVAL_LOG.md`. This is the only path by which
   a result enters version control. The log file is markdown, append-only,
   chronological (newest at bottom). Each entry: timestamp, commit SHA
   (short) + branch, question ID, rubric, score/max + pass/fail, key
   metrics, judge model, optional `--note`.

   In batch mode (default when `--result` is omitted), reads all result
   JSONs from the most recently modified `results/<commit>/` directory and
   formats them as a single summary table entry:

   ```markdown
   ## 2026-07-12 batch (commit cefe112f, main)

   | Question | Rubric | Score | web_search | Status |
   |----------|--------|-------|------------|--------|
   | flaming-hot-cheetos | general | 19/25 | 8 | PASS |
   | tyranitar-ou | pokemon | 6/16 | 11 | FAIL |

   - Agent model: z-ai/glm-5.2-20260616
   - Note: searxng en-US fix baseline
   ```

   `EVAL_LOG.md` lives at the repository root (not `backend/moira_eval/`);
   it is committed while `results/` is not. This split keeps the durable
   score history small and reviewable while bulky per-run artifacts stay
   local. **Idempotency:** `log.py` refuses duplicate entries (same
   commit + question ID for single mode, same commit for batch mode)
   unless `--force` is passed.

3. **Formalize storage shape + .gitignore** — the directory structure
   `run.py` already writes to (since Iteration 2) gets its full rationale
   here:

   ```
   backend/moira_eval/results/        # gitignored
     <commit-sha-short>/
       meta.json                      # {commit, branch, timestamp, judge_model}
       <question-id>.json             # {question, run_id, metrics, judge_scores, ...}
   ```

   `results/` is gitignored because result files are environment-dependent
   (provider, live search results, judge model version) and most runs are
   not meaningful enough to track. This matches the frozen-baseline model
   from `claude-assessment.md`: run carefully, save the trace locally,
   compare; don't continuously rerun and pretend every result is comparable.

   **Artifact storage default:** result files store metrics + scores +
   question only — small and sufficient for diffing. The full artifact dump
   (snapshot, tool trace, all verification attempts) is opt-in via
   `--include-artifacts` and written alongside the result file under the same
   gitignored `results/` directory. This keeps disk usage and result-file
   size minimal by default while preserving a path to full debugging data
   when needed.

   Add to `backend/.gitignore`:
   ```
   moira_eval/results/
   ```

**Tests:** `moira_eval_tests/test_evaluation_diff.py` (two fixture result dirs -> assert
table output, +/- deltas); `moira_eval_tests/test_evaluation_log.py` (fixture result file +
temp log file; assert entry shape, duplicate detection, `--force` override).

### Iteration 5 — Invoke + batch + docs (convenience + polish)

**Goal:** seed runs programmatically without clicking through the UI,
batch-evaluate the full question set, and document the harness.

**Why this last:** `invoke.py` and `batch.py` are pure convenience — they
don't add scoring capability, just remove the UI-click bottleneck and
the manual-per-question evaluation overhead.

**What you can do after this iteration:**

```
# seed a run from the command line
./run.sh eval:invoke --question tyranitar-ou
# prints run_id; then score it with run.py as usual

# seed all benchmark questions at once
./run.sh eval:invoke --all --budget 80

# evaluate all benchmark questions against their latest completed runs
./run.sh eval:batch --db backend/moira.db

# evaluate only specific questions
./run.sh eval:batch --db backend/moira.db \
    --only tyranitar-ou,telescope-mount-cost
```

**What's built:**

1. **`invoke.py`** — thin CLI. Creates a conversation via
   `POST /api/conversations`, POSTs the message that starts the run
   (`POST /api/conversations/{id}/messages`), then polls
   `GET /api/conversations/{id}` until the run completes.
   Does **not** score — that's `run.py`'s job. Supports `--question`,
   `--text`, `--all`, `--timeout`, `--budget`. Each run completes
   before the next starts (strictly serial). Aborts immediately on
   timeout, run error, or stopped status.

2. **`batch.py`** — batch evaluation CLI. For each predefined question,
   finds the most recent completed run matching the question text (via
   prefix matching on the user message), then runs the full pipeline
   (capture → metrics → judge → save). Prints a summary table with
   scores, web_search counts, and status per question. Supports
   `--db`, `--only`.

3. **Docs** — this section. Plus `run.sh` commands `eval:invoke` and
   `eval:batch`.

**Tests:** `moira_eval_tests/test_evaluation_invoke.py` (25 tests: mocked
HTTP; assert conversation creation, message POST, polling loop, budget
passthrough, CLI modes, HTTP error handling, serial execution,
abort-on-failure);
`moira_eval_tests/test_evaluation_batch.py` (18 tests: DB fixture with all
questions, run-finding prefix tolerance, latest-match, non-completed
filtering, evaluate_one with/without judge, judge error fallback, summary
table formats, CLI `--only` filter, full batch run).

## Test coverage summary

Organized by iteration. All tests runnable via
`uv run pytest moira_eval_tests/ -q`.

| Iteration | Module | Tests |
|---|---|---|
| 1 | `capture.py` (refactor) | fixture DB with knowledge snapshot + review/eval steps -> assert enriched dict, all retry attempts captured |
| 1 | `metrics.py` | pure-function tests over fixture artifacts (incl. hallucinated-ID case, unknown/unsupported counts) |
| 2 | `judge.py` | mock `InferenceClient`; assert prompt construction, JSON parsing, malformed-JSON tolerance |
| 2 | `run.py` | end-to-end with mocked judge; assert result file written; metrics-only fallback without env vars |
| 3 | `rubric_general.py` | dataclass shape parity with `rubric_pokemon.py`; scorecard factory; hard-fail threshold logic (category ≤2, total <15) |
| 3 | `questions.py` | question set loads, rubric assignment resolves, `tyranitar-ou` text matches `CANARY_QUESTION` |
| 4 | `diff.py` | two fixture result dirs -> assert table output, +/- deltas |
| 4 | `log.py` | fixture result file + temp log file; assert entry shape, duplicate detection, `--force` override |
| 5 | `invoke.py` | mocked HTTP; assert conversation creation, message POST, polling, budget passthrough, CLI modes, HTTP error handling |
| 5 | `batch.py` | DB fixture with all questions, run-finding prefix tolerance, latest-match, evaluate_one with/without judge, summary table, CLI `--only` filter |

## Verification

After each iteration:

```
uv run pytest tests/ -q -x --ignore=tests/test_url_content.py   # backend
uv run pytest moira_eval_tests/ -q                               # eval harness
.venv/bin/ruff check
.venv/bin/ruff format --check
```

Each iteration is complete when its tests pass and the CLI produces the
documented output shape on a real run.

## Relationship to existing critique docs

- `claude-assessment.md` — source of the harness design, the general rubric,
  and the LLM-judge caveat.
- `completed/research-loop-critique.md` — source of the Tyranitar canary, the
  Pokemon 8-category rubric, and the mechanical metric list (web_search
  calls, tool-routing rate, citation-backed fact coverage, contradiction
  rate). Phase 0 of that critique is what this harness implements.
- `comment-on-pokemon-as-test.md` — justifies why Pokemon is a strong stress
  test rather than a toy.

## Out of scope (deferred)

- External baselines (OpenAI DR, Claude Research, plain tool-agent). The
  storage shape supports importing their answers later, but no automation
  for it now.
- Knowledge persistence across conversations (`claude-assessment.md` step 2).
  Separate roadmap item.
- Deterministic fact-type -> tool routing (`claude-assessment.md` step 3).
  Separate roadmap item; the harness *measures* this failure mode but does
  not fix it.
- Cross-fact contradiction detection at the knowledge-model layer
  (`claude-assessment.md` step 4). The harness surfaces
  `contradicted_fact_count` as a metric; the deterministic check itself is
  separate work.
