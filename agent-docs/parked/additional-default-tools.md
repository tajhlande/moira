# Additional default tools

other tools we could implement as default tools:

## Tool class metadata (added 2026-08-29)

As the catalog grows, `ToolDefinition` should carry a coarse `tool_class`
field so consumers reason over classes instead of hardcoded tool-name lists.

**The class is execution mechanics, not subject matter.** What a tool's
material *is about* belongs to the material-class flag
(`summarize-source.md`, web_source store) and the source-type taxonomy
(`source-quality-and-verification.md`); the tool class says how the tool
runs and what it costs:

| class | mechanics | cost / failure profile | members |
|-------|-----------|------------------------|---------|
| `local` | no network, no model call, deterministic | free; cannot fail interestingly | calculator, `recall_source` (pure store read — even its snippet refusal is deterministic) |
| `web` | page acquisition over HTTP | paid; extraction nondeterminism | `web_search`, `url_content` |
| `api` | structured endpoint, usually JSON | paid; deterministic-ish, schema-stable | `pokeapi__*`, RESTTool instances, the tools below |
| `analysis` | runs its own model sub-call over material | priced like inference; bound by model quality | `summarize_source` (planned); grind-mode per-conclusion verification calls would land here too |

Consumers that want it today:

- **Metrics.** `metrics.py` computes `domain_first_request_share` as "first
  candidate ∉ {web_search, url_content}" — a literal name list that rots as
  tools are added. First refactor when the field lands: name list → class
  check.
- **Planner ordering.** The binding candidate-order rule currently teaches
  priorities per tool, by name. One sentence per class ("structured
  evidence → api first; page evidence → web; re-reading what you already
  acquired → local and free; deep reading → analysis and expensive") makes
  every future tool inherit the guidance — direct attack on prompt bloat.
- **Judge payload.** The tool catalog is already sent to the judge; a class
  column sharpens tool-choice scoring.
- **tools table / admin UI.** Trivial column add.

Related design principle agreed in discussion: **push sequencing into the
tool, not the model.** Dependent multi-call chains (e.g. recall → notice
truncation → fetch → deep read) are where the 35B drops the thread;
`summarize_source` owning its acquisition is the pattern
(`summarize-source.md` Decision 1).

## Tool list
- Wikipedia + Wikidata (one tool or two): the "what/who is X" lane. This is what reclaims the most volume from Reddit/Facebook results. Wikidata gives you QIDs as the entity-grounding spine; Wikipedia gives readable prose with revision IDs for citation.
- OpenAlex + Crossref: the "what does the research say" lane. OpenAlex for discovery (clean JSON, no key, abstracts inline), Crossref as the DOI authority for verifying/normalizing any citation—including ones that arrive from web search.
- FRED + World Bank: the "what's the number" lane for macro/economic time series. This is where web search is both worst and least citable.
- SEC EDGAR: corporate and financial facts via CIK + the structured company-facts (XBRL) endpoint. Primary filings, impeccable provenance.
- Europe PMC: the biomedical lane (friendlier than raw E-utilities, full text for OA, abstracts inline). Health is where result quality matters most and where you least want a forum thread.
- ClinicalTrials.gov v2: registered trials. Optional as a sixth, but it pairs naturally with Europe PMC for any evidence question and has a genuinely good modern API.

Everything else: UniProt, PubChem, CourtListener, GBIF, Overpass geo

## How to shape the bespoke tools
Since you're hand-building these, the design choices matter more than the source list. A few principles that pay off specifically for local-model + traceability:
Two-tier by default. Every tool exposes search → {id, title, snippet/abstract, date} and a separate fetch(id) → full record/text. Search never returns bodies. This is the single biggest lever for keeping a small model's context clean—it decides which IDs are worth expanding instead of drowning in full documents. OpenAlex, Europe PMC, and EDGAR all support this split natively.
Slots, not syntax. For the query-language sources, expose verbs the model can't get wrong: resolve_entity(name) → QID, get_property(QID, property), geocode(place). The model never sees SPARQL. You absorb the brittleness once, in code you can test, rather than at inference time on every call.
Make the tool emit the citation, not the model. Have each tool return a structured provenance block alongside the content—{source, stable_id, resolver_url, retrieved_at, version}—rather than trusting the model to format a citation from the prose. For Wikipedia, that means capturing the oldid revision, not the bare URL. For FRED/World Bank, capture the retrieval date and any vintage, since those numbers get revised under stable IDs. This way your traceability is mechanical and uniform across sources, and the model's job is reduced to "attach this block to this claim," which small models do far more reliably than constructing citations.
Normalize to one record shape. Different APIs, one internal schema (id, type, title, authors/entity, date, snippet, full-text-handle, provenance). Your routing layer and your model both benefit from never having to learn six response formats, and it makes the "why this source" routing decision easier to log as part of the trace.
