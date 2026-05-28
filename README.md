# MOiRA

MOiRA is a learning exercise and an experiment to do the following:

* build a research agent
* learn LangGraph
* produce something that can pass the Pokemon strategy test (a project I'm doing with my son)


**What we are trying to achieve**

A research agent whose answers are *more trustworthy than a single-pass tool-using agent*. Specifically:

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

| Environment variable | Description                                               |
|----------------------|-----------------------------------------------------------|
| `MOIRA_CONFIG_FILE`  | The path to your configuration file                       |
| `MOIRA_DATA_DIR`     | The path to the directory where MOiRA will store its data |

The following environment variables are optional:

| Environment variable | Description                                               |
|----------------------|-----------------------------------------------------------|
| `MOIRA_PROMPT_FILE` | Path to a markdown file to override the system prompts for each research cycle step |




## License

Copyright 2006 Tajh L. Taylor

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this software except in compliance with the License.
You may obtain a copy of the License at

    https://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.