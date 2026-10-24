"""system_activity.csv rows as claim records.

Activity rows say what a workflow reported, so they never set a state: reported_result stays in
fields and is_claim is True. Identifiers are taken only from explicit text in `details`
(LINK_RESOURCE_IDENTITY rows name the email and user they link), and other resource IDs in the
text ("repopulated downstream resource search_4801") become related_resources.
target_system is where the operation ran (sometimes a replica such as account_failover_replica),
so system_id is left for the index to resolve from the resource ID, with target_system as fallback.
"""
from __future__ import annotations

from .model import EvidenceRecord, identifiers_from_text, parse_time_or_none, resource_ids_in


def activity_records(rows: list[dict[str, str]]) -> tuple[list[EvidenceRecord], list[str]]:
    records, warnings = [], []
    for number, row in enumerate(rows, start=2):  # line 1 is the CSV header
        when = parse_time_or_none(row.get("event_time"))
        if when is None:
            warnings.append(f"{row.get('activity_id')}: unparseable event_time {row.get('event_time')!r}")
        details = row.get("details") or ""
        resource_id = row.get("resource_id") or None
        records.append(
            EvidenceRecord(
                evidence_id=row["activity_id"], source="activity", file_format="activity",
                file_path="system_activity.csv", location=f"csv line {number}",
                system_id=None, system_basis="unresolved",
                resource_id=resource_id, collection=row.get("event_type") or None, record_type=None,
                state=None, state_basis=None, observed_at=when,
                time_basis="record:event_time" if when else "none", file_captured_at=None,
                identifiers=frozenset(identifiers_from_text(details)),
                related_resources=frozenset(r for r in resource_ids_in(details) if r != resource_id),
                extraction="structured", is_claim=True, fields=dict(row),
            )
        )
    return records, warnings
