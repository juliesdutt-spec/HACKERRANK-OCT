"""JSON record exports. The structure is detected from content keys, never from the file name:

* snapshot bundles: {"exported_at", "snapshots": [{"captured_at", "data_store", "collection",
  "records": [...]}]}; a section whose records carry system/result is an operational ledger;
* service exports: {"exported_at", "service_exports": {system: {collection: [items]}}}, where
  search index_queries nest their hits under hits.documents;
* flat listings and ledgers: {"captured_at" or "exported_at", "collection"?, "records": [...]}.
"""
from __future__ import annotations

import json
from typing import Any

from .model import (
    ABSENT,
    PRESENT,
    ParseContext,
    derive_state,
    flatten,
    identifiers_from_fields,
    observation_time,
    parse_time_or_none,
    related_resources_from_fields,
)


def parse_json(ctx: ParseContext) -> None:
    report = ctx.report
    report.parser = "json_records"
    try:
        data = json.loads(ctx.path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        report.errors.append(f"invalid JSON: {exc}")
        return
    if not isinstance(data, dict):
        report.errors.append(f"unrecognized JSON structure: top level is {type(data).__name__}")
        return

    file_time, file_basis = _file_time(ctx, data)
    if isinstance(data.get("snapshots"), list):
        report.parser = "json_snapshot_bundle"
        _bundle(ctx, data["snapshots"], file_time, file_basis)
    elif isinstance(data.get("service_exports"), dict):
        report.parser = "json_service_export"
        _service_export(ctx, data["service_exports"], file_time, file_basis)
    elif isinstance(data.get("records"), list):
        report.parser = "json_record_listing"
        _listing(ctx, data, file_time, file_basis)
    else:
        report.errors.append(f"unrecognized JSON structure: top-level keys {sorted(data)}")


def _file_time(ctx: ParseContext, data: dict[str, Any]):
    for key in ("captured_at", "exported_at"):
        if key in data:
            parsed = parse_time_or_none(data[key])
            if parsed is None:
                ctx.report.warnings.append(f"unparseable top-level {key} {data[key]!r}")
                return None, "none"
            return parsed, f"file:{key}"
    if ctx.captured_at is not None:
        ctx.report.warnings.append("no capture time in the JSON; using the manifest capture time")
        return ctx.captured_at, "manifest:captured_at"
    ctx.report.warnings.append("no capture time available; records cannot establish state")
    return None, "none"


def _record_time(ctx, fields, fallback, fallback_basis, where):
    when, key, malformed = observation_time(fields)
    if malformed:
        ctx.report.warnings.append(f"{where}: unparseable {key} {fields.get(key)!r}; record has no usable time")
        return None, f"record:{key}:unparseable"
    if when is not None:
        return when, f"record:{key}"
    return fallback, fallback_basis


def _emit(ctx, where, item, *, system, collection, when, basis, default_state, extra=None) -> str | None:
    if not isinstance(item, dict):
        ctx.report.warnings.append(f"{where}: expected an object, found {type(item).__name__}")
        return False
    fields = flatten(item)
    if extra:
        fields.update(extra)
    resource_id = fields.get("resource_id")
    if not resource_id:
        ctx.report.warnings.append(f"{where}: record has no resource_id; kept without one")
    state, state_basis = derive_state(fields)
    if state is None and state_basis is None:
        state, state_basis = default_state
    elif state is None:
        ctx.report.warnings.append(f"{where}: unmapped state value {state_basis}")
    record_system = fields.get("system") or system
    ctx.add_record(
        location=where, system_id=record_system, resource_id=resource_id, collection=collection,
        record_type=fields.get("record_type"), state=state, state_basis=state_basis,
        observed_at=when, time_basis=basis,
        identifiers=frozenset(identifiers_from_fields(fields)),
        related_resources=related_resources_from_fields(fields, resource_id), fields=fields,
    )
    return resource_id


def _count_unit(ctx, outcome) -> None:
    """outcome is False when the item could not become a record, otherwise its resource ID (or None)."""
    if outcome is False:
        ctx.report.account("item_not_an_object")
    else:
        ctx.report.units_used += 1


def _is_ledger(record: Any) -> bool:
    return isinstance(record, dict) and "system" in record and "result" in record


def _bundle(ctx, snapshots, file_time, file_basis) -> None:
    for s_index, snapshot in enumerate(snapshots):
        where = f"$.snapshots[{s_index}]"
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("records"), list):
            ctx.report.warnings.append(f"{where}: snapshot without a records list")
            ctx.report.note("section_without_records_list")
            continue
        captured = parse_time_or_none(snapshot.get("captured_at"))
        if captured is None:
            ctx.report.warnings.append(f"{where}: missing or unparseable captured_at; its records have no usable time")
            section_basis = "none"
        else:
            section_basis = "section:captured_at"
        store, collection = snapshot.get("data_store"), snapshot.get("collection")
        records = snapshot["records"]
        ctx.report.units_total += len(records)
        ids = set()
        for r_index, record in enumerate(records):
            item_where = f"{where}.records[{r_index}]"
            when, basis = _record_time(ctx, record if isinstance(record, dict) else {}, captured, section_basis, item_where)
            resource_id = _emit(ctx, item_where, record, system=store, collection=collection,
                                when=when, basis=basis, default_state=(PRESENT, "listed_in_snapshot"))
            _count_unit(ctx, resource_id)
            if resource_id:
                ids.add(resource_id)
        if records and all(_is_ledger(r) for r in records):
            ctx.report.note("ledger_section_without_scope")
            continue  # lookup history, not a dump of the collection
        ctx.add_scope(location=where, scope_kind="collection_snapshot", system_id=store, collection=collection,
                      captured_at=captured, time_basis=section_basis, resource_ids=frozenset(ids))


def _service_export(ctx, exports, file_time, file_basis) -> None:
    for system in sorted(exports):
        collections = exports[system]
        if not isinstance(collections, dict):
            ctx.report.warnings.append(f"$.service_exports.{system}: expected an object of collections")
            continue
        for collection in sorted(collections):
            items = collections[collection]
            where = f"$.service_exports.{system}.{collection}"
            if not isinstance(items, list):
                ctx.report.warnings.append(f"{where}: expected a list")
                ctx.report.note("collection_not_a_list")
                continue
            ctx.report.units_total += len(items)
            ids = set()
            for i_index, item in enumerate(items):
                item_where = f"{where}[{i_index}]"
                when, basis = _record_time(ctx, item if isinstance(item, dict) else {}, file_time, file_basis, item_where)
                if isinstance(item, dict) and isinstance(item.get("hits"), dict):
                    found, emitted = _search_hits(ctx, item_where, item, system, collection, when, basis)
                    ids |= found
                    if emitted:
                        ctx.report.units_used += 1
                    else:
                        ctx.report.account("search_query_without_documents")
                        ctx.report.warnings.append(f"{item_where}: search query with hits but no documents")
                    continue
                resource_id = _emit(ctx, item_where, item, system=system, collection=collection,
                                    when=when, basis=basis, default_state=(PRESENT, "listed_in_export"))
                _count_unit(ctx, resource_id)
                if resource_id:
                    ids.add(resource_id)
            ctx.add_scope(location=where, scope_kind="service_listing", system_id=system, collection=collection,
                          captured_at=file_time, time_basis=file_basis, resource_ids=frozenset(ids))


def _search_hits(ctx, where, item, system, collection, when, basis) -> tuple[set[str], bool]:
    """One record per returned document; a query with zero hits records its resource as ABSENT."""
    hits = item["hits"]
    documents = hits.get("documents") if isinstance(hits.get("documents"), list) else []
    query = {"query": item.get("query"), "query_resource_id": item.get("resource_id")}
    ids, emitted = set(), False
    for d_index, document in enumerate(documents):
        resource_id = _emit(ctx, f"{where}.hits.documents[{d_index}]", document, system=system,
                            collection=collection, when=when, basis=basis,
                            default_state=(PRESENT, "search_hit"), extra=query)
        emitted = emitted or resource_id is not False
        if resource_id:
            ids.add(resource_id)
    if not documents and hits.get("total") == 0 and item.get("resource_id"):
        fields = flatten({k: v for k, v in item.items() if k != "hits"})
        ctx.add_record(
            location=where, system_id=system, resource_id=item["resource_id"], collection=collection,
            state=ABSENT, state_basis="hits.total=0", observed_at=when, time_basis=basis,
            identifiers=frozenset(identifiers_from_fields(fields)), fields=fields,
        )
        emitted = True
    return ids, emitted


def _listing(ctx, data, file_time, file_basis) -> None:
    records = data["records"]
    collection = data.get("collection")
    ctx.report.units_total += len(records)
    ids = set()
    for r_index, record in enumerate(records):
        where = f"$.records[{r_index}]"
        when, basis = _record_time(ctx, record if isinstance(record, dict) else {}, file_time, file_basis, where)
        resource_id = _emit(ctx, where, record, system=None, collection=collection, when=when, basis=basis,
                            default_state=(PRESENT, "listed_in_records"))
        _count_unit(ctx, resource_id)
        if resource_id:
            ids.add(resource_id)
    if records and all(_is_ledger(r) for r in records):
        ctx.report.note("ledger_listing_without_scope")
        return
    ctx.add_scope(location="$.records", scope_kind="record_listing", system_id=None, collection=collection,
                  captured_at=file_time, time_basis=file_basis, resource_ids=frozenset(ids))
