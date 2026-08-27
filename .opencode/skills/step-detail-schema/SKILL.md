---
name: step-detail-schema
description: Validate moira workflow_steps.detail JSON against its schema. Use when inspecting or forensically analyzing workflow runs in data/moira.db, when changing what workflow nodes write into their detail dict (research.py, planning.py, report_generation.py, etc.), or when "step detail", "workflow_steps.detail", or step-detail schema drift comes up.
---

# Step-detail schema checking

Every workflow node persists a `detail` JSON blob into `workflow_steps.detail`
in the SQLite database (`data/moira.db` at the repo root). The documented
shape lives in `backend/moira/schemas/step_detail.schema.json`, with the
node-to-definition mapping in `backend/moira/schemas/__init__.py`
(`NODE_DETAIL_DEFS`).

There is no validate-at-write enforcement (deliberate design decision). The
schema is enforced at two points instead:

1. **Test level** — `backend/tests/test_step_detail_schema.py` pins the
   contract for handcrafted payloads and keeps the eval capture layer
   (`moira_eval/capture.py`) aligned with the schema's `tool_result` keys.
2. **Offline batch** — the CLI below validates stored rows.

## Validating runs

From `backend/` (note the live DB is at the repo root, hence `../data`):

```bash
uv run python -m moira.schemas.validate_step_details --db ../data/moira.db --run-id <uuid>
uv run python -m moira.schemas.validate_step_details --db ../data/moira.db --all
```

Flags: `--limit N` caps runs scanned with `--all`; `--show-valid` prints clean
steps too. Exit code is 1 when any step violates or has unparsable detail.

## Interpreting results

- Each step prints `OK (<def>)` naming the schema definition it matched, or a
  list of violations. Nodes with several legitimate shapes (research:
  full pass / budget exit / round error; LLM nodes: success vs error rows)
  match the first def that validates cleanly.
- `matched=None` violations are real drift — a payload that fits no
  documented shape. Fix the node writer or, if the shape is legitimately new,
  update the schema.
- **Historical noise**: detail keys accumulated over time, so old runs lack
  newer keys and violate. Known groups: runs before 08-25 lack
  `request_attempt_counts`/`issued_query_count`, runs before 08-27 lack
  `new_facts`/`stalled` on research steps, the earliest June runs lack even
  more. One July row uses the retired evaluation route value `reject`. All
  expected — judge violations by whether they appear on RECENT runs.
- `tool_result` entries use the key `result` (written by `active_run`'s
  `tool_result` handler); round-error payloads embed the node's internal log
  which uses `output` instead. Both are legal per the schema's anyOf.
- `report_generation` has two legacy minimal shapes (`generation_reason`-only
  or `generation_path`-only) from before the detail dict carried LLM keys;
  the retired `verification` node is also registered so historical runs
  sweep as clean as possible.

## Changing node detail shapes

When adding or renaming a key a node writes into `detail`:

1. Update `step_detail.schema.json` (the relevant `$def`).
2. Update `NODE_DETAIL_DEFS` only if adding a new node or variant def.
3. Update or extend `tests/test_step_detail_schema.py` (happy path for the
   new shape, plus the capture-alignment test if `tool_results` changed).
4. Run `uv run python -m moira.schemas.validate_step_details --all` once to
   see which historical runs the change reclassifies as drift, and mention
   that in the change summary.
