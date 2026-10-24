"""Write output.csv and check it against the challenge contract (AGENTS.md 6.3-6.6, problem statement)."""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .data import parse_time
from .steps import OPERATION_ORDER, PHASE, PHASE_ORDER, STATE, STEP_FIELDS

COLUMNS = ("case_id", "deletion_status", "steps_json")
STATUSES = ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE")


def case_sort_key(case_id: str) -> tuple[int, str]:
    """Numeric order of the case number (case_33 < case_100), as the request file lists them."""
    digits = re.sub(r"\D", "", case_id)
    return (int(digits) if digits else -1, case_id)


def serialize_steps(steps: list[dict[str, Any]]) -> str:
    return json.dumps([{k: step[k] for k in STEP_FIELDS} for step in steps], ensure_ascii=False, separators=(",", ":"))


def write_output(rows: Iterable[tuple[str, str, list[dict[str, Any]]]], path: Path) -> None:
    ordered = sorted(rows, key=lambda row: case_sort_key(row[0]))
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for case_id, status, steps in ordered:
            writer.writerow([case_id, status, serialize_steps(steps)])


def validate_output(
    path: Path,
    requests: list[dict[str, str]],
    citable_times: dict[str, datetime | None],
) -> tuple[list[str], list[str]]:
    """(errors, warnings). citable_times maps every citable evidence ID (file_id, activity_id,
    audit_id) to the time it was captured or recorded."""
    errors: list[str] = []
    warnings: list[str] = []
    audit_at = {r["case_id"]: parse_time(r["audit_at"]) for r in requests}
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        rows = list(reader)
    if tuple(header or ()) != COLUMNS:
        errors.append(f"header is {header}, expected {list(COLUMNS)}")
        return errors, warnings
    ids = [row[0] for row in rows if row]
    if len(rows) != len(requests):
        errors.append(f"{len(rows)} data rows, expected {len(requests)}")
    if sorted(set(ids)) != sorted(audit_at):
        errors.append(f"case_ids differ from the requests: missing {sorted(set(audit_at) - set(ids))[:5]}, "
                      f"unexpected {sorted(set(ids) - set(audit_at))[:5]}")
    if len(set(ids)) != len(ids):
        errors.append("duplicate case_id rows")
    if ids != sorted(ids, key=case_sort_key):
        errors.append("rows are not sorted by case_id")
    for row in rows:
        if len(row) != 3:
            errors.append(f"row {row[:1]} has {len(row)} columns")
            continue
        case_id, status, raw = row
        errors.extend(f"{case_id}: {e}" for e in _check_case(status, raw, audit_at.get(case_id), citable_times, warnings, case_id))
    return errors, warnings


def _check_case(status, raw, audit_at, citable_times, warnings, case_id) -> list[str]:
    problems: list[str] = []
    if status not in STATUSES:
        return [f"status {status!r} is not allowed"]
    try:
        steps = json.loads(raw)
    except json.JSONDecodeError as exc:
        return [f"steps_json is not valid JSON ({exc})"]
    if not isinstance(steps, list) or not all(isinstance(s, dict) for s in steps):
        return ["steps_json is not a JSON array of objects"]
    if status == "NOT_APPLICABLE":
        return [] if steps == [] else ["NOT_APPLICABLE must have steps_json []"]
    if status == "COMPLETE" and not steps:
        warnings.append(f"{case_id}: COMPLETE with no verification step")
    keys = []
    for index, step in enumerate(steps, start=1):
        if set(step) != set(STEP_FIELDS):
            problems.append(f"step {index} has fields {sorted(step)}")
            continue
        op = step["operation"]
        if step["step_no"] != index:
            problems.append(f"step {index} has step_no {step['step_no']}")
        if op not in PHASE or PHASE[op] != step["phase"]:
            problems.append(f"step {index}: operation {op} is not allowed in phase {step['phase']}")
            continue
        if STATE[op] != step["state"]:
            problems.append(f"step {index}: {op} must have state {STATE[op]}, not {step['state']}")
        evidence = step["evidence_id"]
        cited = status == "COMPLETE" or step["phase"] == "FINDING"
        if cited:
            if not evidence:
                problems.append(f"step {index}: {op} must cite evidence")
            elif evidence not in citable_times:
                problems.append(f"step {index}: unknown evidence_id {evidence}")
            elif citable_times[evidence] is None or (audit_at and citable_times[evidence] > audit_at):
                problems.append(f"step {index}: {evidence} was captured after audit_at or has no capture time")
        elif evidence != "":
            problems.append(f"step {index}: future {step['phase'].lower()} step must have an empty evidence_id")
        if status == "COMPLETE" and step["phase"] != "VERIFICATION":
            problems.append(f"step {index}: COMPLETE cases contain only verification steps")
        keys.append((PHASE_ORDER.index(step["phase"]), OPERATION_ORDER.index(op), step["system_id"], step["resource_id"]))
    if keys != sorted(keys):
        problems.append("steps are not ordered by phase, operation, system_id and resource_id")
    if status == "INCOMPLETE" and not any(s.get("phase") == "FINDING" for s in steps):
        problems.append("INCOMPLETE case without a finding")
    return problems


def citable_evidence_times(dataset_rows: dict[str, list[dict[str, str]]]) -> dict[str, datetime | None]:
    """Every ID a step may cite, from the manifest, the activity stream and the identity audit log."""
    def safe(value):
        try:
            return parse_time(value)
        except (ValueError, TypeError):
            return None

    times: dict[str, datetime | None] = {}
    for row in dataset_rows["evidence_files"]:
        times[row["file_id"]] = safe(row["captured_at"])
    for row in dataset_rows["system_activity"]:
        times[row["activity_id"]] = safe(row["event_time"])
    for row in dataset_rows["user_audit_log"]:
        times[row["audit_id"]] = safe(row["event_time"])
    return times
