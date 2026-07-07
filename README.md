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

MOiRA is a general-purpose research system that can automatically acquire new capabilities through API ingestion.

## What it can do

**Structured research workflow**

MOiRA generates verified answers to user questions by reasoning about facts and conclusions, with full traceability.

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

MOiRA includes a standalone evaluation harness (`moira_eval`) that computes
deterministic metrics from completed research runs stored in SQLite. It reads
the knowledge snapshot, tool trace, and verification outputs without making
live tool calls or LLM requests.

**Score a run:**

```bash
./run.sh eval --db data/moira.db --run-id <uuid>
# or omit --run-id to score the latest completed run
./run.sh eval --db data/moira.db
```

**Example output:**

```
run <uuid>: web_search=14 | total_tools=22 | unknown_facts=3
unsupported=1 | halluc_ids=0 | budget=48/60 (80%)
```

**What the metrics tell you:**

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

**Run eval tests:**

```bash
./run.sh test:eval
```

Future iterations will add a frontier-model judge, result storage, diffing,
and a question set. See [agent-docs/evaluation-harness.md](agent-docs/evaluation-harness.md)
for the full plan.

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