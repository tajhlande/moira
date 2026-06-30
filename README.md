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
with a guided tool onboarding wizard, and semantig 

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

- Transparency, You can see:
  - what facts MOiRA sought
  - what it found
  - how it found it
  - how conclusions were drawn
  - what claims are contradicted 

## Why I built this

I wanted to:

* build a research agent
* learn LangGraph
* produce something that can pass the Pokemon strategy test (a project I'm doing with my son)


**What we are trying to achieve**

A research agent whose answers are *more trustworthy than single-pass or multi-pass tool-using agents. 
Specifically:

- Fewer fabricated citations
- Fewer confidently-wrong claims
- Honest "I couldn't verify this" flagging when verification cannot confirm a claim
- Visible chain-of-evidence: every claim is mappable to a source the user can inspect

## Getting started 

TBD


## Configuration

You will need to create a `moira-config.yaml` file. 
There is a template in [/config/moira-config-template.yaml](/config/moira-config-template.yaml).
Copy it and make changes appropriate to your environment.

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