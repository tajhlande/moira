# Research loop overhaul

## Problem statement

I have a few goals: 

* Produce better tool use and output than the same base model with OpenWebUI and the tool use it has * Have a wide array of available tools, with much easier tool integration by wrapping APIs as tools 
* Control over the prompts and quality gates I've also been using this and other projects to teach my kid about LLMs, to understand what they can and can't do, hence the testing on competitive Pokemon questions, which turn out to be much harder than other things I ask it to do. 

Here is the main workflow: 
* Planning 
* Discovering Tools 
* Selecting Tools 
* Researching 
* Drafting 
* Verifying 
* Generating Report 

The main problems right now: 

* The model heavily prefers to use web_search over more specific tools, even after being prompted to prefer more specific tools 
* The Research and Verification steps tend to use a lot of tool calls, mostly web_search. 12-15 calls per step is typical. 
* Overuse of search has caused my SearXNG instance to be temporarily banned from useful sources like Brave Search API. So lots of searches now come back empty, which triggers even more calls. I have seen a step with as many as 30 calls. 
* I am not sure any of this is resulting in better research reports, because the model isn't using the tools that provide the right facts 
* The model is also combining small bits of information that are individually correct in ways that make the combination incorrect, as with type strength and weakness information. 

I am considering the following: 
* How can I help the model select the right tools better, without building something domain specific to Pokemon? 
* Should I introduce a different kind of tool that can do factual reasoning, such as a symbolic reasoner? I am not sure how to furnish enough facts to make that useful for a specific research area, except maybe as a "project" within the tool, something I am considering. Fact discovery has to be automated somehow, as manual entry won't cut it. 
* What other approaches should I try to improve the output of this agent?

##  Recommendations from GPT-5.5:

- Fact requirement extraction (before research) 
- Deterministic tool routing from fact type → tool
- Tool description update - defining tools by the questions they answer
- Fact synthesis into structured facts
- Claim-level verification instead of report-level verification
- Verifier supplies replacement facts, not just criticism
- Tool exhaustion tracking / cost infrastructure overhaul

Target workflow:
this conversation has gone way down into the weeds with a bit too much detail. let's focus on the "what" of your recommendation, and not the "how".  You said:

- Fact requirement extraction (before research) 
- Deterministic tool routing from fact type → tool
- Tool description update - defining tools by the questions they answer
- Fact synthesis into structured facts
- Claim-level verification instead of report-level verification
- Verifier supplies replacement facts, not just criticism
- Tool exhaustion tracking / cost infrastructure overhaul

I am thinking about the following workflow:

- Question decomposition - Topic, Entity, Concept and  Desired fact extraction and ideation from the user question
- Tool identification - find matching tools based on these inputs
- Planning for how to discover facts using tools (with cost limits and costs per tool use visible to the planner)
- Research (aka fact discovery) to search for facts using tools (can request additional facts not identified in the plan). Produces known and unknown 
- Fact synthesis - compose facts into conclusions, show logical reasoning
- Verification - confirm facts can be found at cited sources, confirm logic leading to conclusions as two separate steps. Send back to decomposition, tool identification, 
- Report generation - synthesize prose from facts and conclusions, WITHOUT further reasoning

Other work:
- Change tool ingest to have task model write descrption for each discovered tool to include what questions that tool can answer, or what facts that tool can provide

Does this sound like a reasonable approach given the feedback you've provided? Am I missing anything major?

### Decomposition

Not using tools: 

Given this question, what information must be known to answer it?

We want to identify a few things here:

- the goal of the question
- topics
- entities
- concepts
- facts needed

Open question: is task model strong enough for this? I think not because world knowledge is needed.

**Example:**

User question:
"What Pokemon synergize with Tyranitar in Gen9 OU?"

Output, formatted as structured and formatted JSON:

```json
{
    "user_goal": "The user wants reliable competitive team-building advice, with emphasis on factual verification of mechanics and legality.",
    "topic": "competitive pokemon",
    "entities": [
        "Tyranitar",
        "Gen9",
        "OU"
    ],
    "concepts": [
        "Synergy between Pokemon",
        "team building"
    ], 
    "unknown_facts": [
        "Tyranitar typing",
        "Tyranitar abilities",
        "Tyranitar weaknesses",
        "OU legality",
        "Partner typings",
        "Partner abilities"
    ]
}
```

### Tool identification

Given the desired to be known facts, find matching tools using LanceDB. 
Don't ask the LLM to reduce this tool list. 
Tools must have matching cost figures. 

### Planning

Not using tools:

Design a set of tool calls that we think will produce the desired facts,
consdiering the cost of tool calls, the remaining budget, and 
how much of the remaining budget other steps will use. 

Produce a list of proposed tool calls to guide the research step, that
the planner thinks will provide enough factual information to achieve the 
user goal.

### Research

Using tools:

Review unknown facts, 
call tools, learn facts, record known and unknown facts. 
Find additional facts as needed. Record them with citations to sources. 
Record additional unknown facts if not previously recorded.

Recorded facts should be structured according to a schema, so they can be easily understood later on.

### Synthesis

Not using tools: 

given the user's goal, and the known and unknown facts,
derive conclusions into a structured list. 

### Verification

Using tools:

As before, evaluate facts as supported, unsupported, or contradicted by citations or new source material.

New work: evaluate conclusions from facts as supported, unsupported, or contradicted by citations or new source material from new tool calls.

Also separately: evaluate whether the user goal is met. 

Create list of critiques. Accumulate new unverified facts if needed. 

Create final judgment as to whether user question is sufficiently answered by the facts and conclusions. 

Route back to tool discovery - decomposition isn't needed again, we already have decomposed state.

### Report generation

Not using tools:

Write a narrative summary using only the facts (identified as known and unknown) and conclusions.
Do not use any built-in world knowledge. 

Produce the citations as before, and the citation list, and the unverified and contradicted claims (both facts and conclusions). 

## Architectural change to how knowledge is stored and used

The knowledge model that is built at the decomposition step and is carried from step to step, with
each step using a piece of it, has a consistent structure, and becomes carried state. It is effectively
an artifact belonging to the workflow run, and updated as it runs. 

If needed, individual steps in the workflow run can "snapshot" the knowledge model as of the end of that step.

This is distinct from the detail that is recorded of step operations, such as prompts, thinking blocks, and tool calls.

The structured outputs from each step should be composed into the state model as appropriate - in some cases,
the bits of state replace other bits of state (as with unverified facts), and in other cases, they add
new sections of state, or decorate portions of state. 

We need a full schema for this knowledge model.

For debugging purposes, it will be useful for the UI to show the knowledge model as a navigable object. 

## Refactor budget to have costs for tool invocations as well as steps, and then to do planning with budget information

This will be used to help cut down on excessive web_search tool calls, as those will be weighted higher.

Cost becomes part of the tool information model.

ingested methods will have cost 1 by default. 

web searches will have cost 5. 
url retrieval will have  cost 3. 
calculator will have cost 0.
date time will have cost 0. 

(if zero cost is undesirable, then minimal epsilon costsuch as 0.1).

## Change tool ingest to have task model describe what questions each tool answers

This will help with tool discovery, especially as the agent frame is to seek answers to fact questions / unverified facts



