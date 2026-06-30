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

- [Python](https://www.python.org/) >= 3.13
- [Node.js](https://nodejs.org/) 20.19+ (LTS) or 22.12+ (LTS)
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- An OpenAI-compatible LLM endpoint (e.g., [Ollama](https://ollama.com), [vLLM](https://github.com/vllm-project/vllm), [OpenRouter](https://openrouter.ai))

### Setup

1. **Clone the repository**

2. **Install backend dependencies:**
   ```bash
   cd backend
   uv sync
   cd ..
   ```

3. **Install frontend dependencies:**
   ```bash
   cd frontend
   npm install
   cd ..
   ```

4. **Create your config file:**
   ```bash
   cp config/moira-config-template.yaml config/moira-config.yaml
   ```
   The defaults work for local development. See the template for customization options.

5. **Set environment variables:**
   ```bash
   export MOIRA_CONFIG_FILE=/path/to/moira/config/moira-config.yaml
   export MOIRA_DATA_DIR=/path/to/moira/data
   export MOIRA_SECRETS_KEY="$(openssl rand -base64 32)"
   ```
   > **Important:** Store `MOIRA_SECRETS_KEY` somewhere safe. It encrypts stored
   > credentials (e.g. inference provider API keys). If it changes, previously stored 
   > credentials become undecryptable.

6. **Start the backend:**
   ```bash
   cd backend
   uv run uvicorn moira.main:app --reload
   ```

7. **Start the frontend** (in a separate terminal):
   ```bash
   cd frontend
   npm run dev
   ```
**OR**

start both at the same time using the `run.sh` script:

```bash
./run.sh dev
```

Use `CTRL-C` to exit. 

8. **Configure an inference provider:**
   Open [http://localhost:5173](http://localhost:5173), then navigate to
   **Settings → Inference** to add a provider, discover models, and assign
   intelligence and task roles.

9. **Start researching**

Create a new conversation and ask a question.


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