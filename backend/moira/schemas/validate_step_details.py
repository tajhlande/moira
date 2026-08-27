"""Offline batch validation of workflow_steps.detail against the step-detail schema.

Forensics tool: scans the live database (or a single run) and reports every
step whose detail payload no longer matches its node's documented shape. Used
after schema or node changes to find historical drift, and as the enforcement
backend for the step-detail-schema agent skill.

Usage (from backend/):
    uv run python -m moira.schemas.validate_step_details --db data/moira.db --all
    uv run python -m moira.schemas.validate_step_details --run-id <uuid>
    uv run python -m moira.schemas.validate_step_details --run-id <uuid> --show-valid
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys

from . import validate_detail


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m moira.schemas.validate_step_details",
        description="Validate workflow_steps.detail payloads against step_detail.schema.json.",
    )
    parser.add_argument(
        "--db",
        default="data/moira.db",
        help="Path to the SQLite database (default: data/moira.db).",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--run-id", help="Validate only steps belonging to this workflow run.")
    group.add_argument("--all", action="store_true", help="Validate every run in the database.")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Cap the number of runs scanned (with --all). 0 = no cap.",
    )
    parser.add_argument(
        "--show-valid",
        action="store_true",
        help="Also print steps that validated cleanly.",
    )
    return parser.parse_args(argv)


def _select_run_ids(conn: sqlite3.Connection, args: argparse.Namespace) -> list[str]:
    if args.run_id:
        return [args.run_id]
    query = "SELECT DISTINCT workflow_run_id FROM workflow_steps ORDER BY started_at"
    if args.limit:
        query += f" LIMIT {int(args.limit)}"
    return [row[0] for row in conn.execute(query)]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.run_id and not args.all:
        print("Specify --run-id <uuid> or --all (see --help).", file=sys.stderr)
        return 2

    conn = sqlite3.connect(args.db)
    violation_count = 0
    step_count = 0
    skipped_unparsable = 0

    for run_id in _select_run_ids(conn, args):
        rows = conn.execute(
            "SELECT node_name, detail FROM workflow_steps"
            " WHERE workflow_run_id = ? ORDER BY started_at, id",
            (run_id,),
        ).fetchall()
        if not rows:
            print(f"run {run_id}: no steps found")
            continue

        run_violations = 0
        for node_name, detail_text in rows:
            step_count += 1
            try:
                detail = json.loads(detail_text)
            except (TypeError, json.JSONDecodeError):
                skipped_unparsable += 1
                print(f"run {run_id} [{node_name}]: detail is not valid JSON")
                run_violations += 1
                continue

            errors, matched_def = validate_detail(node_name, detail)
            if matched_def is not None and not errors:
                if args.show_valid:
                    print(f"run {run_id} [{node_name}]: OK ({matched_def})")
                continue
            run_violations += 1
            print(f"run {run_id} [{node_name}]: {len(errors)} violation(s)")
            for err in errors:
                print(f"    - {err}")

        violation_count += run_violations
        if run_violations == 0:
            print(f"run {run_id}: OK")

    print(
        f"\n{step_count} steps checked, {violation_count} violating,"
        f" {skipped_unparsable} unparsable (db: {args.db})"
    )
    return 1 if violation_count or skipped_unparsable else 0


if __name__ == "__main__":
    sys.exit(main())
