"""Apply dataset/deletion_rules.csv to the evidence linked to one person at audit_at.

Three parts, each traceable:
* rules: loaded from the CSV; every condition is evaluated by an evaluator registered for its
  exact text, so a changed or new condition shows up as "unevaluated" instead of being guessed;
* categories: a resource's data category comes from an explicit record_type in the evidence,
  else from the collection it was observed in (CATEGORY_BY_COLLECTION, each entry justified
  below), else from its resource-ID prefix as learned from explicit record types;
* assessment: the resource's state at audit_at per storage channel (primary vs each replica),
  then the rule's required treatment decides compliant / violation / unknown.

Data flows are not a rule category: the problem statement requires that no active sync, import
or restore path can recreate data, so connectors are assessed as recreation paths (DATA_FLOW).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .data import DATASET_DIR, read_csv
from .evidence.index import MatchedRecord
from .evidence.model import (
    ABSENT,
    ACTIVE,
    ANONYMIZED,
    BLOCKED,
    DISABLED,
    IDENTIFIABLE,
    MENTIONED,
    PRESENT,
    REDACTED,
    EvidenceRecord,
    SnapshotScope,
    identifiers_from_fields,
    parse_time_or_none,
)

DATA_FLOW = "DATA_FLOW"
DIRECT_KINDS = ("email", "email_hash", "phone", "name")  # removed by redaction and anonymization
# Keys that carry what was looked up or correlated, not what the record contains.
CONTEXT_KEYS = {
    "lookup_key", "match_key", "subject_key", "subject_ref", "scan_key", "owner_key",
    "query", "query_resource_id", "correlation_key", "lookup_value",
}

# Observed collection / table / log-event names -> rule category.
# "learned": the same resource IDs carry this explicit record_type elsewhere in the evidence;
# "named": the collection or event name, and its fields, describe that category directly.
CATEGORY_BY_COLLECTION: dict[str, tuple[str, str]] = {
    "customer_profiles": ("USER_PROFILE", "learned"),
    "profile_lookups": ("USER_PROFILE", "learned"),
    "profile_lookup_completed": ("USER_PROFILE", "learned"),
    "customer_search_documents": ("SEARCH_INDEX_ENTRY", "learned"),
    "search_documents": ("SEARCH_INDEX_ENTRY", "learned"),
    "index_queries": ("SEARCH_INDEX_ENTRY", "learned"),
    "index_query_completed": ("SEARCH_INDEX_ENTRY", "learned"),
    "support_records": ("SUPPORT_MESSAGE", "learned"),
    "support_ticket_read_completed": ("SUPPORT_MESSAGE", "learned"),
    "support_conversations": ("SUPPORT_MESSAGE", "named"),
    "tickets": ("SUPPORT_MESSAGE", "named"),
    "message_pii_scan": ("SUPPORT_MESSAGE", "named"),
    "message_read": ("SUPPORT_MESSAGE", "named"),
    "ticket_attachments": ("SUPPORT_ATTACHMENT", "named"),  # rows carry storage_class=ticket_attachment
    "attachment_pii_scan": ("SUPPORT_ATTACHMENT", "named"),
    # support_replica legacy_contact_index entries: a stored contact identifier of the person
    "contact_query_completed": ("SECONDARY_IDENTIFIER", "named"),
    "deletion_marker_check": ("SECONDARY_IDENTIFIER", "named"),
    "security_audit_events": ("SECURITY_AUDIT_LOG", "learned"),
    "security_audit_records": ("SECURITY_AUDIT_LOG", "learned"),
    "audit_records": ("SECURITY_AUDIT_LOG", "learned"),
    "audit_record_read_completed": ("SECURITY_AUDIT_LOG", "learned"),
    "retained_order_records": ("LEGAL_HOLD_RECORD", "named"),
    "retained_order_loaded": ("LEGAL_HOLD_RECORD", "named"),
    "deleted_customer_references": ("ACCOUNT_TOMBSTONE", "named"),  # account_status DELETED, subject_reference kept
}
# Fields that define what a record is, whatever system holds it: a contact record carrying a
# marketing-consent flag is a marketing profile (seen once: analytics_9914 in evidence_01).
CATEGORY_BY_FIELD = {"email_opt_in": "MARKETING_PROFILE"}
# Resource-ID prefixes never seen with an explicit record_type.
CATEGORY_BY_PREFIX_DOCUMENTED = {"tombstone": "ACCOUNT_TOMBSTONE", "marketing": "MARKETING_PROFILE"}

PRESENCE_STATES = {PRESENT, IDENTIFIABLE, MENTIONED, ANONYMIZED, REDACTED, ACTIVE, DISABLED, BLOCKED}

COMPLIANT, VIOLATION, UNKNOWN = "COMPLIANT", "VIOLATION", "UNKNOWN"
# finding names follow the challenge's finding operations
REMAINING_RECORD = "REMAINING_RECORD"
EXPOSED_PII = "EXPOSED_PII"
ACTIVE_DATA_FLOW = "ACTIVE_DATA_FLOW"
RESTORE_RISK = "RESTORE_RISK"
RETAINED_RECORD_MISSING = "RETAINED_RECORD_MISSING"


@dataclass(frozen=True)
class Rule:
    rule_id: str
    data_category: str
    condition: str
    required_action: str


def load_rules(dataset_dir: Path = DATASET_DIR) -> dict[str, Rule]:
    rules = {}
    for row in read_csv("deletion_rules.csv", dataset_dir):
        rule = Rule(row["rule_id"], row["data_category"], row["condition"], row["required_action"])
        if rule.data_category in rules:
            raise ValueError(f"two rules for {rule.data_category}; the engine expects one per category")
        rules[rule.data_category] = rule
    return rules


def content_identifiers(record: EvidenceRecord) -> frozenset[tuple[str, str]]:
    """Identifiers the record itself holds (excluding what a lookup was keyed on)."""
    context = set(CONTEXT_KEYS)
    if "result" in record.fields:  # ledger rows: subject_reference names the subject that was checked
        context.add("subject_reference")
    return frozenset(identifiers_from_fields({k: v for k, v in record.fields.items() if k not in context}))


def resolve_category(records: list[EvidenceRecord], prefix_categories: dict[str, str]) -> tuple[str | None, str, list[str]]:
    """(category, basis, notes) for one resource from all of its records."""
    notes: list[str] = []
    explicit = sorted({r.record_type for r in records if r.record_type})
    if len(explicit) == 1:
        return explicit[0], "explicit_record_type", notes
    if len(explicit) > 1:
        notes.append(f"conflicting explicit record types {explicit}")
    for field_name, category in CATEGORY_BY_FIELD.items():
        if any(field_name in r.fields for r in records):
            return category, f"field_{field_name}", notes
    by_collection = sorted({CATEGORY_BY_COLLECTION[r.collection] for r in records if r.collection in CATEGORY_BY_COLLECTION})
    if len(by_collection) == 1:
        category, how = by_collection[0]
        return category, f"collection_{how}", notes
    if len(by_collection) > 1:
        notes.append(f"collections point at several categories {by_collection}")
    resource_id = next((r.resource_id for r in records if r.resource_id), None)
    prefix = resource_id.rsplit("_", 1)[0] if resource_id and "_" in resource_id else None
    if prefix == "support" and any(r.file_format == "pdf" for r in records):
        # the evidence item is itself a document held for the support case
        return "SUPPORT_ATTACHMENT", "document_file_for_support_case", notes
    if prefix in prefix_categories:
        return prefix_categories[prefix], "resource_prefix_learned", notes
    if prefix in CATEGORY_BY_PREFIX_DOCUMENTED:
        return CATEGORY_BY_PREFIX_DOCUMENTED[prefix], "resource_prefix_documented", notes
    if explicit:
        return explicit[0], "explicit_record_type_conflict", notes
    return None, "unresolved", notes


def learn_prefix_categories(records: list[EvidenceRecord]) -> dict[str, str]:
    """Prefix -> category from explicit record_type values; ambiguous prefixes (support) are left out."""
    seen: dict[str, set[str]] = defaultdict(set)
    for record in records:
        if record.record_type and record.resource_id and "_" in record.resource_id:
            seen[record.resource_id.rsplit("_", 1)[0]].add(record.record_type)
    return {prefix: next(iter(types)) for prefix, types in sorted(seen.items()) if len(types) == 1}


# ------------------------------------------------------------------ rule conditions
def _retention_open(records: list[EvidenceRecord], key: str, audit_at: datetime) -> tuple[str, str]:
    dates = sorted({d for r in records if (d := parse_time_or_none(r.fields.get(key)))})
    if not dates:
        return "assumed", f"no {key} in the evidence; the rule's condition is taken to hold"
    if dates[-1] >= audit_at:
        return "met", f"{key} {dates[-1].isoformat()} is after audit_at"
    return "not_met", f"{key} {dates[-1].isoformat()} is before audit_at"


def evaluate_condition(rule: Rule, records: list[EvidenceRecord], audit_at: datetime) -> tuple[str, str]:
    """(status, explanation) with status met, not_met, assumed or unevaluated."""
    text = rule.condition.strip()
    if text == "Always":
        return "met", "rule applies always"
    if text == "Record is still attributable to a user":
        return "met", "evaluated per observation: the treatment applies while the record is attributable"
    if text == "Log contains a user email address":
        return "met", "evaluated per observation: an email in the log content triggers redaction"
    if text == "Record is within the supplied retention period":
        return _retention_open(records, "retention_until", audit_at)
    if text == "A supplied legal hold is active":
        return _retention_open(records, "retained_until", audit_at)
    if text.startswith("Conversation contains shared customer, agent, or order context"):
        return "assumed", "support conversations concern a ticket and order; no evidence says the context is private"
    if text == "Attachment belongs only to the deletion subject":
        return "assumed", "no evidence names another person on the attachment"
    if text == "Restore access can be blocked":
        return "assumed", "restore access is a policy flag in the evidence (restore_allowed)"
    if text.startswith("Account is deleted and only a stable internal user_id is needed"):
        return "assumed", "tombstone rows carry account_status DELETED and a subject_reference"
    return "unevaluated", f"no evaluator for condition {text!r}"


# ------------------------------------------------------------------ assessment
@dataclass
class Observation:
    record: EvidenceRecord
    channel: str
    state: str  # normalized record state
    exposed: frozenset[tuple[str, str]]  # content identifiers of DIRECT_KINDS
    attributable: bool  # direct identifiers or a user reference in the content
    via_scope: SnapshotScope | None = None


@dataclass
class ResourceAssessment:
    system_id: str | None
    resource_id: str
    category: str | None
    category_basis: str
    rule: Rule | None
    condition_status: str
    condition_note: str
    required_action: str | None  # DELETE, REDACT, ANONYMIZE, RETAIN, DISABLE_FLOW or None
    outcome: str  # COMPLIANT, VIOLATION or UNKNOWN
    finding: str | None
    state_at_audit: str | None
    basis: str
    deciding_evidence: list[EvidenceRecord] = field(default_factory=list)
    deciding_scope: SnapshotScope | None = None
    channels: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    claim_only_link: bool = False  # set by the audit engine


def _channel(record: EvidenceRecord) -> str:
    role = record.fields.get("_store_role")
    return role if role and role != "primary" else "primary"


def _observation(record: EvidenceRecord) -> Observation:
    ids = content_identifiers(record)
    exposed = frozenset(k for k in ids if k[0] in DIRECT_KINDS)
    if record.state == IDENTIFIABLE and not exposed:
        exposed = frozenset({("stated", "IDENTIFIABLE")})
    attributable = bool(exposed) or any(k[0] == "user_ref" for k in ids)
    return Observation(record, _channel(record), record.state, exposed, attributable)


def _latest_by_channel(observations: list[Observation]) -> dict[str, list[Observation]]:
    by_channel: dict[str, list[Observation]] = defaultdict(list)
    for obs in observations:
        by_channel[obs.channel].append(obs)
    latest = {}
    for channel, items in by_channel.items():
        newest = max(o.record.observed_at for o in items)
        latest[channel] = [o for o in items if o.record.observed_at == newest]
    return latest


def _refute_with_scopes(latest, scopes, collections, system_id, audit_at, resource_id):
    """A later full snapshot of the same table/collection and store that lacks the resource
    replaces an older presence in that channel (the samples cite such snapshots as proof of absence)."""
    for channel, items in latest.items():
        if not any(o.state in PRESENCE_STATES for o in items):
            continue
        newest = items[0].record.observed_at
        candidates = [
            s for s in scopes
            if s.visible_at(audit_at) and s.coverage_confirmed and s.scope_kind in ("table_snapshot", "collection_snapshot")
            and s.system_id == system_id and s.collection in collections
            and (s.store_role if s.store_role and s.store_role != "primary" else "primary") == channel
            and s.captured_at > newest and resource_id not in s.resource_ids
        ]
        if candidates:
            scope = max(candidates, key=lambda s: (s.captured_at, s.evidence_id))
            latest[channel] = [Observation(items[0].record, channel, ABSENT, frozenset(), False, via_scope=scope)]


def assess_resource(
    system_id: str | None,
    resource_id: str,
    matches: list[MatchedRecord],
    rules: dict[str, Rule],
    prefix_categories: dict[str, str],
    scopes: list[SnapshotScope],
    audit_at: datetime,
) -> ResourceAssessment:
    records = [m.record for m in matches]
    evidence = [r for r in records if not r.is_claim]
    category, category_basis, notes = resolve_category(evidence or records, prefix_categories)
    rule = rules.get(category) if category else None
    if category and category != DATA_FLOW and rule is None:
        notes.append(f"no rule in deletion_rules.csv for category {category}")
    condition_status, condition_note = (evaluate_condition(rule, evidence, audit_at) if rule else ("n/a", ""))
    required = "DISABLE_FLOW" if category == DATA_FLOW else (rule.required_action if rule else None)

    observations = [_observation(r) for r in evidence if r.state is not None and r.observed_at is not None]
    latest = _latest_by_channel(observations)
    collections = {r.collection for r in evidence if r.collection}
    _refute_with_scopes(latest, scopes, collections, system_id, audit_at, resource_id)
    channels = {ch: ",".join(sorted({o.state for o in obs})) for ch, obs in sorted(latest.items())}
    assessment = ResourceAssessment(system_id, resource_id, category, category_basis, rule, condition_status,
                                    condition_note, required, UNKNOWN, None, None, "", channels=channels, notes=notes)
    if not evidence:
        assessment.basis = "only workflow claims mention this resource; its state cannot be established"
        return assessment
    if not observations:
        assessment.basis = "no observation states this resource's condition"
        return assessment
    if required is None:
        assessment.basis = "category could not be resolved to a rule, so no treatment can be checked"
        assessment.state_at_audit = channels.get("primary") or next(iter(channels.values()))
        return assessment
    if condition_status == "not_met":
        assessment.basis = f"rule {rule.rule_id} condition not met ({condition_note}); no other rule covers the record"
        notes.append("unresolved: rule condition not met")
        return assessment
    _judge(assessment, latest, required)
    return assessment


def _judge(a: ResourceAssessment, latest: dict[str, list[Observation]], required: str) -> None:
    flat = [o for items in latest.values() for o in items]
    present = [o for o in flat if o.state in PRESENCE_STATES]
    absent = [o for o in flat if o.state == ABSENT]

    def decide(outcome, finding, state, basis, deciding):
        a.outcome, a.finding, a.state_at_audit, a.basis = outcome, finding, state, basis
        a.deciding_evidence = [o.record for o in deciding if o.via_scope is None]
        scoped = [o.via_scope for o in deciding if o.via_scope is not None]
        a.deciding_scope = scoped[0] if scoped else None

    if required == "DELETE":
        if present:
            decide(VIOLATION, REMAINING_RECORD, "PRESENT", "the rule requires deletion and a copy is still observed", present)
        elif absent:
            decide(COMPLIANT, None, ABSENT, "every observed copy is absent", absent)
        return
    if required in ("REDACT", "ANONYMIZE"):
        key = "exposed" if required == "REDACT" else "attributable"
        content = [o for o in present if o.state != MENTIONED or o.exposed]
        bad = [o for o in content if (o.exposed if key == "exposed" else o.attributable)]
        known_clean = [o for o in content if o.state in (ANONYMIZED, REDACTED) or (o.record.extraction == "structured" and _has_content(o.record))]
        if bad:
            treatment = "redaction" if required == "REDACT" else "anonymization"
            decide(VIOLATION, EXPOSED_PII, IDENTIFIABLE, f"the rule requires {treatment} and identifying data remains", bad)
        elif known_clean:
            state = ANONYMIZED if required == "ANONYMIZE" else REDACTED
            decide(COMPLIANT, None, state, "the record remains without prohibited identifiers", known_clean)
        elif absent and not present:
            decide(COMPLIANT if required == "ANONYMIZE" else UNKNOWN, None, ABSENT,
                   "no attributable copy remains" if required == "ANONYMIZE" else "the record should be retained redacted but is absent",
                   absent)
            if required == "REDACT":
                a.notes.append("unresolved: a record to keep in redacted form is observed absent")
        else:
            a.state_at_audit = PRESENT if present else None
            a.basis = "presence is observed but not the record's content, so exposure cannot be established"
        return
    if required == "RETAIN":
        if a.rule and a.rule.condition.strip() == "Restore access can be blocked":
            active = [o for o in flat if o.state == ACTIVE]
            blocked = [o for o in flat if o.state == BLOCKED]
            if active:
                decide(VIOLATION, RESTORE_RISK, ACTIVE, "the archive is retained but restore access is still allowed", active)
            elif blocked:
                decide(COMPLIANT, None, BLOCKED, "the archive is retained with restore blocked", blocked)
            elif absent:
                decide(COMPLIANT, None, ABSENT, "no archive is observed", absent)
                a.notes.append("archive observed absent; nothing left to restore")
            else:
                a.state_at_audit = PRESENT
                a.basis = "the archive is observed but its restore setting is not"
            return
        if present:
            decide(COMPLIANT, None, PRESENT, "the rule requires retention and the record is present", present)
        elif absent:
            decide(VIOLATION, RETAINED_RECORD_MISSING, ABSENT, "the rule requires retention but the record is absent", absent)
        return
    if required == "DISABLE_FLOW":
        active = [o for o in flat if o.state == ACTIVE]
        disabled = [o for o in flat if o.state == DISABLED]
        if active:
            decide(VIOLATION, ACTIVE_DATA_FLOW, ACTIVE, "the connector is enabled and can copy or recreate the data", active)
        elif disabled:
            decide(COMPLIANT, None, DISABLED, "the connector is disabled", disabled)
        else:
            a.state_at_audit = PRESENT if present else None
            a.basis = "the connector is observed but its enabled state is not"
        return
    a.basis = f"no assessment for required action {required}"


def _has_content(record: EvidenceRecord) -> bool:
    """True for observations that list the record's fields (rows, listings, read results)."""
    return bool(record.state_basis) and (
        record.state_basis in ("row_present", "listed_in_snapshot", "listed_in_export", "listed_in_records", "search_hit")
        or record.state_basis.startswith("event=")
    )


def assessment_dict(a: ResourceAssessment) -> dict[str, Any]:
    return {
        "system_id": a.system_id,
        "resource_id": a.resource_id,
        "category": a.category,
        "category_basis": a.category_basis,
        "rule_id": a.rule.rule_id if a.rule else None,
        "rule_condition": a.rule.condition if a.rule else None,
        "condition_status": a.condition_status,
        "condition_note": a.condition_note,
        "required_action": a.required_action,
        "outcome": a.outcome,
        "finding": a.finding,
        "state_at_audit": a.state_at_audit,
        "basis": a.basis,
        "channels": a.channels,
        "deciding_evidence": [
            {"evidence_id": r.evidence_id, "file_path": r.file_path, "location": r.location,
             "observed_at": r.observed_at.isoformat() if r.observed_at else None,
             "state": r.state, "state_basis": r.state_basis}
            for r in a.deciding_evidence
        ],
        "deciding_scope": (
            {"evidence_id": a.deciding_scope.evidence_id, "location": a.deciding_scope.location,
             "captured_at": a.deciding_scope.captured_at.isoformat(), "note": "resource not listed in this later snapshot"}
            if a.deciding_scope else None
        ),
        "notes": a.notes,
    }
