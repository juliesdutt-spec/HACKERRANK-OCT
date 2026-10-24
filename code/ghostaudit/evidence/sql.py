"""SQLite-compatible snapshot exports.

Three layouts were observed:
* generic tables (customer_data_primary and its replicas) holding record_json per row,
  with origin_system and collection_name columns;
* typed tables such as customer_profiles_primary / customer_profiles_failover_replica;
* inventories: typed tables plus a snapshot_scope(origin_system, captured_at) table and a
  trailing "-- snapshot_completed_at=" comment.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict

from .model import (
    PRESENT,
    ParseContext,
    derive_state,
    flatten,
    identifiers_from_fields,
    parse_time_or_none,
    related_resources_from_fields,
)

HEADER_TIME_RE = re.compile(r"^--\s*(?:Captured at:\s*|snapshot_completed_at=)(\S+)\s*$", re.MULTILINE)
TOPOLOGY_RE = re.compile(r"^--\s*Database topology:\s*(\S+)\s*$", re.MULTILINE)


def _deny_attach(action: int, *_args: object) -> int:
    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def store_role(table: str) -> tuple[str | None, str]:
    """Split a table name into (role, base collection): customer_profiles_failover_replica ->
    ("failover_replica", "customer_profiles"); customer_data_replica_eu_west -> ("replica_eu_west",
    "customer_data"); tables without a role suffix return (None, table)."""
    if table.endswith("_primary"):
        return "primary", table[: -len("_primary")]
    match = re.search(r"_(read_replica|reporting_replica|failover_replica|replica_[a-z_]+)$", table)
    if match:
        return match.group(1), table[: match.start()]
    return None, table


def parse_sql(ctx: ParseContext) -> None:
    report = ctx.report
    report.parser = "sqlite_snapshot"
    text = ctx.path.read_text(encoding="utf-8")
    header_times = HEADER_TIME_RE.findall(text)
    header_time = parse_time_or_none(header_times[0]) if header_times else None
    if header_times and header_time is None:
        report.warnings.append(f"unparseable header capture time {header_times[0]!r}")
    topology = TOPOLOGY_RE.search(text)

    db = sqlite3.connect(":memory:")
    try:
        _load_and_emit(ctx, db, text, header_time, topology)
    finally:
        db.close()


def _load_and_emit(ctx: ParseContext, db: sqlite3.Connection, text: str, header_time, topology) -> None:
    report = ctx.report
    db.set_authorizer(_deny_attach)
    try:
        db.executescript(text)
    except sqlite3.Error as exc:
        report.errors.append(f"sqlite could not load the snapshot: {exc}")
        return
    tables = [name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]

    if "snapshot_scope" in tables:
        for system, captured in db.execute("SELECT origin_system, captured_at FROM snapshot_scope ORDER BY rowid"):
            parsed = parse_time_or_none(captured)
            if parsed is None:
                report.warnings.append(f"snapshot_scope has unparseable time {captured!r} for {system}")
            else:
                ctx.scope_times[system] = parsed
        report.note("snapshot_scope_row", len(ctx.scope_times))

    if header_time is not None:
        base_time, time_basis = header_time, "file:header_capture_time"
    elif ctx.captured_at is not None:
        base_time, time_basis = ctx.captured_at, "manifest:captured_at"
        report.warnings.append("no capture time in the SQL header; using the manifest capture time")
    else:
        base_time, time_basis = None, "none"
        report.warnings.append("no capture time available; records cannot establish state")
    if ctx.scope_times:
        time_basis = "pending:snapshot_scope"  # replaced per system once systems are resolved

    for table in tables:
        if table == "snapshot_scope":
            continue
        columns = [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]
        rows = [dict(zip(columns, row)) for row in db.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
        report.units_total += len(rows)
        role, base_collection = store_role(table)
        if "resource_id" not in columns:
            report.warnings.append(f"table {table} has no resource_id column; {len(rows)} rows kept without one")
        if "record_json" in columns:
            _generic_table(ctx, table, role, rows, base_time, time_basis, topology)
        else:
            _typed_table(ctx, table, role, base_collection, rows, base_time, time_basis, topology)


def _generic_table(ctx, table, role, rows, base_time, time_basis, topology) -> None:
    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    for index, row in enumerate(rows, start=1):
        try:
            payload = json.loads(row["record_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            ctx.report.warnings.append(f"{table} row {index}: record_json is not valid JSON ({exc})")
            ctx.report.account("row_with_invalid_record_json")
            continue
        fields = flatten(payload)
        fields.update({k: v for k, v in row.items() if k != "record_json"})
        _emit_row(ctx, table, index, role, row.get("origin_system"), row.get("collection_name"),
                  fields, base_time, time_basis, topology)
        groups[(row.get("origin_system"), row.get("collection_name"))].add(row.get("resource_id"))
    for (system, collection), ids in sorted(groups.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))):
        ctx.add_scope(
            location=f"table={table} origin_system={system} collection={collection}",
            scope_kind="table_snapshot", system_id=system, collection=collection, store_role=role,
            captured_at=base_time, time_basis=time_basis, resource_ids=frozenset(i for i in ids if i),
        )


def _typed_table(ctx, table, role, base_collection, rows, base_time, time_basis, topology) -> None:
    for index, row in enumerate(rows, start=1):
        _emit_row(ctx, table, index, role, None, base_collection, dict(row), base_time, time_basis, topology)
    ctx.add_scope(
        location=f"table={table}", scope_kind="table_snapshot", system_id=None, collection=base_collection,
        store_role=role, captured_at=base_time, time_basis=time_basis,
        resource_ids=frozenset(row["resource_id"] for row in rows if row.get("resource_id")),
    )


def _emit_row(ctx, table, index, role, system, collection, fields, base_time, time_basis, topology) -> None:
    resource_id = fields.get("resource_id")
    state, basis = derive_state(fields)
    if state is None and basis is None:
        state, basis = PRESENT, "row_present"
    elif state is None:
        ctx.report.warnings.append(f"{table} row {index}: unmapped state value {basis}")
    if role:
        fields["_store_role"] = role
    if topology:
        fields["_topology"] = topology.group(1)
    ctx.add_record(
        location=f"table={table} row={index}",
        system_id=system, resource_id=resource_id, collection=collection,
        record_type=fields.get("record_type"), state=state, state_basis=basis,
        observed_at=base_time, time_basis=time_basis,
        identifiers=frozenset(identifiers_from_fields(fields)),
        related_resources=related_resources_from_fields(fields, resource_id),
        fields=fields,
    )
    ctx.report.units_used += 1
