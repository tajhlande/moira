# MOiRA

<div align="center">
<pre>
 ██████   ██████    ███████     ███  ███████████     █████████  
▒▒██████ ██████   ███▒▒▒▒▒███  ▒▒▒  ▒▒███▒▒▒▒▒███   ███▒▒▒▒▒███ 
 ▒███▒█████▒███  ███     ▒▒███ ████  ▒███    ▒███  ▒███    ▒███ 
 ▒███▒▒███ ▒███ ▒███      ▒███▒▒███  ▒██████████   ▒███████████ 
 ▒███ ▒▒▒  ▒███ ▒███      ▒███ ▒███  ▒███▒▒▒▒▒███  ▒███▒▒▒▒▒███ 
 ▒███      ▒███ ▒▒███     ███  ▒███  ▒███    ▒███  ▒███    ▒███ 
 █████     █████ ▒▒▒███████▒   █████ █████   █████ █████   █████
▒▒▒▒▒     ▒▒▒▒▒    ▒▒▒▒▒▒▒    ▒▒▒▒▒ ▒▒▒▒▒   ▒▒▒▒▒ ▒▒▒▒▒   ▒▒▒▒▒ 
</pre>
</div>

MOiRA is a general-purpose research system that constructs structured knowledge models to answer user questions with fully visible traceability from sources to rigorous verification of facts and conclusions. 

## What it can do

**Structured research workflow**

MOiRA generates verified answers to user questions by discovering and reasoning about facts and conclusions in a knowledge model, with direct user visibility as MOiRA tracks and verifies them.  
At each step of the workflow, the user can see the current state of knowledge, which facts are verified or unverified, 
what facts MOiRA is seeking to find information to learn, and what conclusions can be drawn from those facts to answer
the user's question.

**Automatic capability acquisition**

MOiRA has capabilities to automatically register tools from any OpenAPI compatible API,
with a guided onboarding wizard and semantic tool discovery.

**Local-first execution**

MOiRA is built to work with models you can host locally, on a single GPU. It will work with 
more powerful models, too.

**Multi-provider inference**

Connect local and cloud providers simultaneously; pick the best model for each research conversation.

**Stop and resume** 

Long-running research can be paused and resumed as needed.

**Cost-aware execution** 

The research workflow is limited to a synthetic budget with per-step costs
and smart retries. 

## What problems does MOiRA solve better than existing agents?

- Reliability. MOiRA:
  - identifies unsupported claims
  - cites evidence
  - refuses unsupported conclusions
  - provides critiques of its own work

- Capability growth
  - Install API -> New research capability appears, with little manual effort

- Transparency. You can see:
  - what facts MOiRA sought
  - what it found
  - how it found it
  - how conclusions were drawn
  - what claims are contradicted 

## Capabilities

- **Per-conversation model selection** — override the intelligence model for individual conversations without affecting others
- **Native and emulated tool calling** — per-model toggle between API-level function calling and text-parsed tool calls
- **Encrypted credential storage** — API keys and tool secrets stored encrypted in the database
- **Semantic tool discovery** — LanceDB embeddings match research queries to relevant tools from the catalog
- **LLM-powered tool description enrichment** — tool descriptions are automatically expanded for better semantic matching
- **OpenAPI and Swagger ingestion** — guided wizard parses specs, selects operations, and flags auth requirements for later credential binding
- **Checkpointed stop and resume** — pause any research run and resume from where it left off
- **Budget-aware execution** — synthetic budget with per-node cost weights and configurable retry limits
- **Inference metrics** — token counts, thinking tokens, and timing tracked per model and purpose
- **Tool metrics** — call counts, latency, and cost tracked per tool with rolling hourly buckets
- **SSE streaming** — real-time step-by-step updates as research progresses
- **Configurable system prompts** — override any workflow step's prompt via a single markdown file


## Why I built this

I wanted to:

* build a research agent
* learn LangGraph
* produce something that can pass the Pokemon strategy test (a project I'm doing with my son)


**What we are trying to achieve**

A research agent whose answers are *more trustworthy* than single-pass or multi-pass tool-using agents. 
Specifically:

- Fewer fabricated citations
- Fewer confidently-wrong claims
- Honest "I couldn't verify this" flagging when verification cannot confirm a claim
- Visible chain-of-evidence: every claim is mappable to a source the user can inspect

## Getting started

### Prerequisites

- An OpenAI-compatible LLM endpoint (e.g., [Ollama](https://ollama.com), [vLLM](https://github.com/vllm-project/vllm), [OpenRouter](https://openrouter.ai))

Choose one of the three quickstart paths below.

### Docker quickstart

> Requires: [Docker](https://docs.docker.com/get-docker/) with the Compose plugin.

1. **Clone the repository** (you only need `docker-compose.yml` and the config template).

2. **Create your config file:**
   ```bash
   cp config/moira-config-template.yaml config/moira-config.yaml
   ```

3. **Create a `.env` file** in the repo root:
   ```bash
   cat > .env << 'EOF'
   MOIRA_SECRETS_KEY=$(openssl rand -base64 32)
   EOF
   ```
   Replace the `$(...)` with an actual generated key — Docker Compose does not
   expand command substitutions in `.env` files.

   > **Important:** Store `MOIRA_SECRETS_KEY` somewhere safe. It encrypts stored
   > credentials (e.g. inference provider API keys). If it changes, previously stored
   > credentials become undecryptable.

4. **Pull and start:**
   ```bash
   docker compose pull
   docker compose up -d
   ```

   To build from source instead: `docker compose build && docker compose up -d`.

5. **Configure an inference provider:**
   Open [http://localhost:8000](http://localhost:8000), then navigate to
   **Settings → Inference** to add a provider, discover models, and assign
   intelligence and task roles.

6. **Start researching** — create a new conversation and ask a question.

### Local production quickstart

> Requirements:
> - [Python](https://www.python.org/) >= 3.13
> - [Node.js](https://nodejs.org/) 20.19+ (LTS) or 22.12+ (LTS)
> - [uv](https://docs.astral.sh/uv/) (Python package manager)


1. **Clone the repository.**

2. **Create your config file:**
   ```bash
   cp config/moira-config-template.yaml config/moira-config.yaml
   ```

3. **Create a `.env` file** in the repo root:
   ```bash
   cat > .env << 'EOF'
   export MOIRA_CONFIG_FILE="$(pwd)/config/moira-config.yaml"
   export MOIRA_DATA_DIR="$(pwd)/data"
   export MOIRA_SECRETS_KEY="$(openssl rand -base64 32)"
   EOF
   chmod 600 .env
   ```

   > **Important:** Store `MOIRA_SECRETS_KEY` somewhere safe. It encrypts stored
   > credentials (e.g. inference provider API keys). If it changes, previously stored
   > credentials become undecryptable.

4. **Install dependencies and start:**
   ```bash
   ./run.sh setup   # one-time: installs Python + Node dependencies
   mkdir -p data    # one-time: create the data directory
   ./run.sh prod    # builds frontend + starts backend on port 8000
   ```

5. **Configure an inference provider:**
   Open [http://localhost:8000](http://localhost:8000), then navigate to
   **Settings → Inference** to add a provider, discover models, and assign
   intelligence and task roles.

6. **Start researching** — create a new conversation and ask a question.

### Local development quickstart

> Requires: same as [Local production quickstart](#local-production-quickstart).

1. Follow steps 1–3 in [Local production quickstart](#local-production-quickstart) above.

2. **Start both dev servers:**
   ```bash
   ./run.sh dev
   ```

   This starts the backend on `http://localhost:8000` and the frontend dev server
   on `http://localhost:5173`. Use `Ctrl-C` to stop.

3. Open [http://localhost:5173](http://localhost:5173).

### Environment configuration

The following environment variables are required:

| Environment variable | Description                                                 |
|----------------------|-------------------------------------------------------------|
| `MOIRA_CONFIG_FILE`  | The path to your configuration file                         |
| `MOIRA_DATA_DIR`     | The path to the directory where MOiRA will store its data   |
| `MOIRA_SECRETS_KEY`  | Encryption key to secure stored credentials in the database |

The following environment variables are optional:

| Environment variable | Description                                                                         |
|----------------------|-------------------------------------------------------------------------------------|
| `MOIRA_PROMPT_FILE`  | Path to a markdown file to override the system prompts for each research cycle step |
| `HF_TOKEN`           | HuggingFace API token, used to check or download the embedding model                |

## Roadmap

Lots of work remains to be done for MOiRA to be fully usable!  
See the notional list at [roadmap.md](roadmap.md).

## Evaluation

MOiRA includes a standalone evaluation harness (`moira_eval`) that scores
completed research runs stored in SQLite. It computes deterministic metrics
from the knowledge snapshot, tool trace, and verification outputs, and can
optionally call a frontier-model judge for rubric-based scoring.

The full workflow is: **seed runs** (`eval:invoke`) → **score them**
(`eval:batch` or `eval`) → **diff and log** results.

### Seeding runs

`eval:invoke` triggers workflow runs through the running backend's API,
without clicking through the UI:

```bash
# single question
./run.sh eval:invoke --question tyranitar-ou

# all benchmark questions, with a budget cap
./run.sh eval:invoke --all --budget 80

# custom text
./run.sh eval:invoke --text "What is the airspeed of an unladen swallow?"
```

Requires the backend to be running (`./run.sh dev:backend`). Prints
`run_id` for each question.

### Evaluating runs

**Batch mode** — evaluate all predefined questions at once:

```bash
./run.sh eval:batch --db data/moira.db
```

Finds the latest completed run matching each question, scores it, and
prints a summary table:

```
Question                        Rubric    Score       web_search  unsupported  Status
------------------------------------------------------------------------------------------
tyranitar-ou                    pokemon   15/16 FAIL          6            0  FAIL
flaming-hot-cheetos             general   21/25 PASS          8            0  PASS
telescope-mount-cost            general   17/25 PASS         22            3  PASS
...
Total: 7 evaluated, 0 skipped, 0 error(s)
```

**Single-question mode** — score one run at a time:

```bash
# latest completed run
./run.sh eval --db data/moira.db

# specific run + rubric
./run.sh eval --db data/moira.db --run-id <uuid> --question-id tyranitar-ou

# ad hoc (no --question-id; derives question from knowledge snapshot, general rubric)
./run.sh eval --db data/moira.db --run-id <uuid>
```

### Metrics

When the judge is not configured (or fails), the harness falls back to
metrics-only mode:

| Metric | What it catches |
|---|---|
| `web_search_calls` | Overuse of generic search vs specialized tools |
| `specialized_tool_use_ratio` | Tool-routing health (fraction of non-generic calls) |
| `unknown_fact_count` | Decomposition/research gaps |
| `unsupported_conclusion_count` | Synthesis overreach |
| `hallucinated_fact_id_count` | Conclusions referencing facts that don't exist |
| `uncited_conclusion_count` | Conclusions without grounding citations |
| `budget_consumed_ratio` | Efficiency |
| `review_count` / `evaluation_count` | Verification retry fragility |

### Judge scoring

To score runs against a rubric with a frontier-model judge, set these
environment variables in a local `.env-eval` file (gitignored) at the repo
root:

| Env var | Required | Description |
|---|---|---|
| `MOIRA_EVAL_JUDGE_ENDPOINT` | Yes | Base URL of an OpenAI-compatible API (e.g. `https://api.openai.com/v1`) |
| `MOIRA_EVAL_JUDGE_MODEL` | Yes | Model ID to use as the judge (e.g. `gpt-4o`) |
| `MOIRA_EVAL_JUDGE_API_KEY` | No | Bearer token for the API (omit for local endpoints that don't require auth) |

`.env-eval` example:

```
MOIRA_EVAL_JUDGE_ENDPOINT=https://api.openai.com/v1
MOIRA_EVAL_JUDGE_MODEL=gpt-4o
MOIRA_EVAL_JUDGE_API_KEY=sk-...
```

`./run.sh eval` and `./run.sh eval:batch` automatically load `.env-eval`
via `uv run --env-file`.

**Two rubrics:**

- **Pokemon rubric** (8 categories, 0–2 each, hard-fail categories) — used
  for the Tyranitar Gen9 OU canary. Domain-specific correctness checks
  (type matchups, ability, OU legality).
- **General rubric** (5 criteria, 1–5 each, no hard-fail) — used for all
  other questions. Scores grounding, fact atomicity, citation support,
  critique quality, and goal alignment.

Each question declares which rubric applies.

**Benchmark question set:**

| Question ID | Rubric | What it tests |
|---|---|---|
| `tyranitar-ou` | pokemon | Pokemon correctness, tool routing |
| `flaming-hot-cheetos` | general | Competing narratives, search discipline |
| `jazz-trumpeters` | general | Consensus/opinion, information availability |
| `telescope-mount-cost` | general | Niche technical, tool-routing failure |
| `trade-policy-manufacturing` | general | Causal reasoning, synthesis overreach |
| `future-nostalgia` | general | Source ambiguity, speculative grounding |
| `water-blood-pressure` | general | Medical grounding precision |

### Comparing results

Result files are written to `moira_eval/results/<commit-sha-short>/\
<question-id>.json` and are gitignored — they are local working data.

To diff two commits' results:

```bash
uv run python -m moira_eval.diff \
    moira_eval/results/<old-sha> moira_eval/results/<new-sha>
```

To log results to the tracked score history:

```bash
# Log the most recent batch (default — reads latest results/<commit>/)
./run.sh eval:log --note "what changed and why"

# Log a specific commit's results
./run.sh eval:log --commit cefe112f --note "..."

# Log a single question
./run.sh eval:log --result backend/moira_eval/results/<sha>/<question>.json
```

This appends a structured entry to `EVAL_LOG.md` (tracked in git).

### Run eval tests

```bash
./run.sh test:eval
```

See [agent-docs/evaluation-harness.md](agent-docs/evaluation-harness.md)
for the full design document.

## License

Copyright 2026 Tajh L. Taylor

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this software except in compliance with the License.
You may obtain a copy of the License at

    https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.