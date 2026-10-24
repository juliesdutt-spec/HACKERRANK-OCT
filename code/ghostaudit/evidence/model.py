"""Internal schema shared by every evidence parser, plus the identifier and state rules.

The rules below come from formats observed in dataset/evidence (docs/dataset_findings.md).
Nothing is inferred from file names, and raw source values are always kept in `fields`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..data import parse_time
from ..identity import normalize_email

# Normalized observed states. A parser only sets one when the source states it explicitly
# (see derive_state); MENTIONED marks a document or screenshot that shows a record without a status.
PRESENT, ABSENT = "PRESENT", "ABSENT"
IDENTIFIABLE, ANONYMIZED, REDACTED = "IDENTIFIABLE", "ANONYMIZED", "REDACTED"
ACTIVE, DISABLED, BLOCKED = "ACTIVE", "DISABLED", "BLOCKED"
MENTIONED = "MENTIONED"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
HASH_RE = re.compile(r"[0-9a-fA-F]{64}")
USER_REF_RE = re.compile(r"\buser_\d+\b")
PHONE_RE = re.compile(r"\+\d[\d ()\-]{5,}\d")
# Resource-ID prefixes observed in the evidence (structured records, PDFs and OCR text).
RESOURCE_RE = re.compile(
    r"\b(?:account|analytics|audit_record|backup|data_flow|export|legal_hold|marketing"
    r"|search|support|tombstone)_\d+\b"
)

NAME_FIELDS = {"full_name", "display_name", "name", "requester_name"}
FREE_TEXT_FIELDS = {"message_text", "message_excerpt", "details", "text"}
NULL_STRINGS = {"", "null", "none", "nil"}
# Keys whose value is when the record was observed; created_at, updated_at, expires_at and
# retention dates are record attributes and never decide visibility.
OBSERVATION_TIME_KEYS = ("recorded_at", "observed_at")

RESULT_STATES = {
    "FOUND": PRESENT,
    "NOT_FOUND": ABSENT,
    "hit": PRESENT,
    "IDENTIFIABLE": IDENTIFIABLE,
    "ANONYMIZED": ANONYMIZED,
    "REDACTED": REDACTED,
    "ACTIVE": ACTIVE,
    "DISABLED": DISABLED,
    "BLOCKED": BLOCKED,
}


@dataclass(frozen=True)
class EvidenceRecord:
    """One observation of one resource (or of identifiers without a resource) in one source."""

    evidence_id: str  # citable id: the manifest file_id, or the activity_id for activity rows
    source: str  # "file" or "activity"
    file_format: str  # sql, json, log, txt, pdf, png, activity
    file_path: str | None  # dataset-relative path from the manifest
    location: str  # where inside the source the record sits
    system_id: str | None
    system_basis: str  # explicit, collection, resource_prefix, log_service or unresolved
    resource_id: str | None
    collection: str | None  # table, collection, listing or log event the record came from
    record_type: str | None  # only when the source names a category (e.g. USER_PROFILE)
    state: str | None
    state_basis: str | None  # the source value that set the state, e.g. "rows=0"
    observed_at: datetime | None
    time_basis: str
    file_captured_at: datetime | None
    identifiers: frozenset[tuple[str, str]]
    related_resources: frozenset[str]
    extraction: str  # structured, pdf_text or ocr
    is_claim: bool  # activity rows report what a workflow claims, not a verified state
    fields: dict[str, Any] = field(default_factory=dict, compare=False)

    def visible_at(self, audit_at: datetime) -> bool:
        """Conservative cutoff: the observation and the capture of its source must not be later
        than audit_at, and a record without an observation time can never establish state."""
        if self.observed_at is None or self.observed_at > audit_at:
            return False
        if self.source == "file":
            return self.file_captured_at is not None and self.file_captured_at <= audit_at
        return True


@dataclass(frozen=True)
class SnapshotScope:
    """A table or collection captured as a whole: which resource IDs it held at one time.

    The index records scopes neutrally; whether a missing ID proves deletion is decided later.
    """

    evidence_id: str
    file_path: str
    location: str
    scope_kind: str  # table_snapshot, collection_snapshot, service_listing or record_listing
    system_id: str | None
    system_basis: str
    collection: str | None
    store_role: str | None  # primary or the replica name, taken from the table name
    captured_at: datetime | None
    time_basis: str
    file_captured_at: datetime | None
    resource_ids: frozenset[str]
    coverage_confirmed: bool  # False when an inventory's snapshot_scope does not list the system

    def visible_at(self, audit_at: datetime) -> bool:
        return (
            self.captured_at is not None
            and self.captured_at <= audit_at
            and self.file_captured_at is not None
            and self.file_captured_at <= audit_at
        )


@dataclass
class FileReport:
    file_id: str
    file_path: str
    file_format: str
    origin: str
    captured_at: str
    parser: str = ""
    status: str = "failed"  # parsed, partial or failed
    records: int = 0
    scopes: int = 0
    # Every unit (non-blank line, table row, listed item) ends up in exactly one bucket:
    # units_total == units_used + sum(units_accounted.values()).
    units_total: int = 0
    units_used: int = 0  # units that produced at least one record
    units_accounted: dict[str, int] = field(default_factory=dict)  # units that produced none, by reason
    info: dict[str, int] = field(default_factory=dict)  # counts that are not units (blank lines, ...)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def account(self, reason: str, count: int = 1) -> None:
        self.units_accounted[reason] = self.units_accounted.get(reason, 0) + count

    def note(self, reason: str, count: int = 1) -> None:
        self.info[reason] = self.info.get(reason, 0) + count

    def finish(self) -> None:
        self.status = "failed" if self.errors else ("partial" if self.warnings else "parsed")

    def as_dict(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "file_path": self.file_path,
            "file_format": self.file_format,
            "origin": self.origin,
            "captured_at": self.captured_at,
            "parser": self.parser,
            "status": self.status,
            "records": self.records,
            "scopes": self.scopes,
            "units_total": self.units_total,
            "units_used": self.units_used,
            "units_accounted": dict(sorted(self.units_accounted.items())),
            "info": dict(sorted(self.info.items())),
            "warnings": self.warnings,
            "errors": self.errors,
        }


@dataclass
class ParseContext:
    """What a parser needs to know about the file it reads, and where it reports problems."""

    file_id: str
    path: Path
    rel_path: str
    file_format: str
    captured_at: datetime | None
    report: FileReport
    records: list[EvidenceRecord] = field(default_factory=list)
    scopes: list[SnapshotScope] = field(default_factory=list)
    # Inventory snapshots list a capture time per system; resolved once systems are known.
    scope_times: dict[str, datetime] = field(default_factory=dict)

    def add_record(self, **values: Any) -> None:
        values.setdefault("evidence_id", self.file_id)
        values.setdefault("source", "file")
        values.setdefault("file_format", self.file_format)
        values.setdefault("file_path", self.rel_path)
        values.setdefault("file_captured_at", self.captured_at)
        values.setdefault("record_type", None)
        values.setdefault("related_resources", frozenset())
        values.setdefault("extraction", "structured")
        values.setdefault("is_claim", False)
        values.setdefault("system_basis", "explicit" if values.get("system_id") else "unresolved")
        self.records.append(EvidenceRecord(**values))

    def add_scope(self, **values: Any) -> None:
        values.setdefault("evidence_id", self.file_id)
        values.setdefault("file_path", self.rel_path)
        values.setdefault("file_captured_at", self.captured_at)
        values.setdefault("store_role", None)
        values.setdefault("coverage_confirmed", True)
        values.setdefault("system_basis", "explicit" if values.get("system_id") else "unresolved")
        self.scopes.append(SnapshotScope(**values))


def parse_time_or_none(value: Any) -> datetime | None:
    """UTC datetime, or None for missing, malformed or zone-less values (never guessed)."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = parse_time(value)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def normalize_phone(value: str) -> str | None:
    digits = re.sub(r"\D", "", value)
    return "+" + digits if len(digits) >= 7 else None


def flatten(obj: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Nested dicts become dotted keys; lists of dicts are kept as JSON text."""
    flat: dict[str, Any] = {}
    for key, value in obj.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(flatten(value, f"{name}."))
        elif isinstance(value, list) and any(isinstance(item, dict) for item in value):
            flat[name] = json.dumps(value, sort_keys=True)
        else:
            flat[name] = value
    return flat


def _is_null(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in NULL_STRINGS)


def identifiers_from_text(text: str) -> set[tuple[str, str]]:
    """Exact identifier patterns in free text (documents, OCR lines, message bodies)."""
    found: set[tuple[str, str]] = set()
    for email in EMAIL_RE.findall(text):
        found.add(("email", normalize_email(email)))
    for ref in USER_REF_RE.findall(text):
        found.add(("user_ref", ref))
    for phone in PHONE_RE.findall(text):
        if (normalized := normalize_phone(phone)) is not None:
            found.add(("phone", normalized))
    return found


def identifiers_from_fields(fields: dict[str, Any]) -> set[tuple[str, str]]:
    """Classify field values by exact pattern. Names are kept for exposure, never for linking."""
    found: set[tuple[str, str]] = set()
    for key, value in fields.items():
        values = value if isinstance(value, list) else [value]
        base = key.rsplit(".", 1)[-1]
        for item in values:
            if _is_null(item) or not isinstance(item, str):
                continue
            text = item.strip()
            if base in NAME_FIELDS:
                found.add(("name", " ".join(text.split())))
            elif "phone" in base or PHONE_RE.fullmatch(text):
                if (normalized := normalize_phone(text)) is not None:
                    found.add(("phone", normalized))
            elif HASH_RE.fullmatch(text):
                found.add(("email_hash", text.lower()))
            elif base in FREE_TEXT_FIELDS:
                found |= identifiers_from_text(text)
            else:
                found |= {("email", normalize_email(e)) for e in EMAIL_RE.findall(text)}
                found |= {("user_ref", ref) for ref in USER_REF_RE.findall(text)}
    return found


def related_resources_from_fields(fields: dict[str, Any], own_id: str | None) -> frozenset[str]:
    """Other resource IDs a record points at, such as a search document's source_resource_id."""
    related: set[str] = set()
    for key, value in fields.items():
        if key == "resource_id" or not isinstance(value, str):
            continue
        if RESOURCE_RE.fullmatch(value.strip()) or key.rsplit(".", 1)[-1] in FREE_TEXT_FIELDS:
            related.update(RESOURCE_RE.findall(value))
    related.discard(own_id or "")
    return frozenset(related)


def _boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in ("true", "false", "0", "1"):
        return value.strip().lower() in ("true", "1")
    return None


def derive_state(fields: dict[str, Any]) -> tuple[str | None, str | None]:
    """Map an explicit state-bearing source value to a normalized state.

    Returns (None, None) when the record states nothing about its status; callers decide whether
    plain presence in a snapshot means PRESENT. An unmapped value returns (None, basis).
    """

    def get(key: str) -> Any:
        value = fields.get(key)
        return None if _is_null(value) else value

    if (value := get("result")) is not None:
        return RESULT_STATES.get(str(value).strip()), f"result={value}"
    if (value := get("rows")) is not None and str(value).strip().isdigit():
        return (ABSENT if int(str(value)) == 0 else PRESENT), f"rows={value}"
    for key in ("http_status", "status_code"):
        if (value := get(key)) is not None:
            code = str(value).strip()
            if code in ("200", "404"):
                return (PRESENT if code == "200" else ABSENT), f"{key}={code}"
            return None, f"{key}={code}"
    if (flag := _boolish(get("enabled"))) is not None:
        return (ACTIVE if flag else DISABLED), f"enabled={str(flag).lower()}"
    if (flag := _boolish(get("restore_allowed"))) is not None:
        return (ACTIVE if flag else BLOCKED), f"restore_allowed={str(flag).lower()}"
    if (value := get("decision")) is not None:
        decision = str(value).strip().lower()
        mapped = {"deny": BLOCKED, "allow": ACTIVE}.get(decision)
        return mapped, f"decision={decision}"
    eligible, blocked = _boolish(get("eligible")), _boolish(get("deletion_block"))
    if eligible is not None or blocked is not None:
        state = BLOCKED if blocked or eligible is False else ACTIVE
        return state, f"eligible={eligible},deletion_block={blocked}"
    if (value := get("direct_identifiers")) is not None and str(value).strip().isdigit():
        return (IDENTIFIABLE if int(str(value)) > 0 else ANONYMIZED), f"direct_identifiers={value}"
    if get("detector") == "direct_identifier" and get("matched_value") is not None:
        return IDENTIFIABLE, "detector=direct_identifier"
    if _boolish(get("marker_found")) is False:
        return PRESENT, "marker_found=false"
    if _boolish(get("available")) is True:
        return PRESENT, "available=true"
    return None, None


def observation_time(fields: dict[str, Any]) -> tuple[datetime | None, str | None, bool]:
    """Return (time, key, malformed) for a record's own observation timestamp, if it has one."""
    for key in OBSERVATION_TIME_KEYS:
        if key in fields and not _is_null(fields[key]):
            parsed = parse_time_or_none(fields[key])
            return parsed, key, parsed is None
    return None, None, False


def resource_ids_in(text: str) -> list[str]:
    seen: list[str] = []
    for match in RESOURCE_RE.findall(text):
        if match not in seen:
            seen.append(match)
    return seen
