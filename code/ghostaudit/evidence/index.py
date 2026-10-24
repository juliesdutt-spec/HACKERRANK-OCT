"""Build the evidence index from the manifest, resolve systems, and match evidence to a person."""
from __future__ import annotations

import dataclasses
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Mapping

from ..data import DATASET_DIR, REPO_ROOT, read_csv
from ..identity import Identifiers, IdentityIndex
from .activity import activity_records
from .documents import OCR_SCALE, parse_pdf, parse_png, parse_text, tesseract_version
from .jsonfiles import parse_json
from .logs import parse_log
from .model import EvidenceRecord, FileReport, ParseContext, SnapshotScope, normalize_phone, parse_time_or_none
from .sql import parse_sql

PARSERS: dict[str, Callable[..., None]] = {
    ".sql": parse_sql, ".json": parse_json, ".log": parse_log, ".txt": parse_text, ".pdf": parse_pdf, ".png": parse_png,
}
MANIFEST_COLUMNS = ("file_id", "file_path", "origin", "captured_at")
LINK_KINDS = ("email", "email_hash", "user_ref", "phone")  # names are never used to link
DEFAULT_OCR_CACHE = REPO_ROOT / ".cache" / "ocr"


@dataclass(frozen=True)
class MatchedRecord:
    record: EvidenceRecord
    basis: str  # identifier, resource_id, related_resource or references_linked_resource
    matched_identifiers: frozenset[tuple[str, str]]
    via: str | None  # the linked resource that brought the record in, for non-identifier matches
    foreign_identifiers: frozenset[tuple[str, str]]  # identifiers on the record owned only by other users
    claim_only: bool = False  # True when the link rests only on activity claims, not on evidence content


@dataclass
class CaseEvidence:
    user_id: str
    audit_at: datetime
    matches: list[MatchedRecord]
    scopes: list[SnapshotScope]

    def resources(self) -> dict[tuple[str | None, str], list[MatchedRecord]]:
        grouped: dict[tuple[str | None, str], list[MatchedRecord]] = defaultdict(list)
        for match in self.matches:
            if match.record.resource_id:
                grouped[(match.record.system_id, match.record.resource_id)].append(match)
        return dict(sorted(grouped.items(), key=lambda item: (str(item[0][0]), item[0][1])))


def identity_keys(ids: Identifiers) -> frozenset[tuple[str, str]]:
    keys = {("email", e) for e in ids.emails} | {("email_hash", h) for h in ids.email_hashes}
    keys.add(("user_ref", ids.user_id))
    keys |= {("phone", p) for p in (normalize_phone(raw) for raw in ids.phones) if p}
    return frozenset(keys)


def identifier_owners(identity: IdentityIndex) -> dict[tuple[str, str], frozenset[str]]:
    """Every identifier any user ever held, mapped to the users who held it."""
    owners: dict[tuple[str, str], set[str]] = defaultdict(set)
    for user_id in identity.user_ids():
        for key in identity_keys(identity.identifiers(user_id)):
            owners[key].add(user_id)
    return {key: frozenset(users) for key, users in owners.items()}


class EvidenceIndex:
    def __init__(self, records, scopes, reports, learned, notes):
        self.records: list[EvidenceRecord] = records
        self.scopes: list[SnapshotScope] = scopes
        self.reports: list[FileReport] = reports
        self.learned: dict[str, dict[str, str]] = learned
        self.notes: dict[str, object] = notes

    # ------------------------------------------------------------------ build
    @classmethod
    def build(
        cls,
        dataset_dir: Path = DATASET_DIR,
        *,
        include_activity: bool = True,
        ocr: bool = True,
        ocr_cache: Path | None = DEFAULT_OCR_CACHE,
        manifest_name: str = "evidence_files.csv",
    ) -> "EvidenceIndex":
        dataset_dir = Path(dataset_dir)
        contexts, reports, notes = [], [], {}
        manifest = read_csv(manifest_name, dataset_dir)
        missing_columns = [c for c in MANIFEST_COLUMNS if manifest and c not in manifest[0]]
        if missing_columns:
            raise ValueError(f"{manifest_name} is missing columns {missing_columns}")
        seen_ids, seen_paths = set(), set()
        for row in manifest:
            report = FileReport(row["file_id"], row["file_path"], Path(row["file_path"]).suffix.lstrip("."),
                                row.get("origin", ""), row.get("captured_at", ""))
            reports.append(report)
            if row["file_id"] in seen_ids or row["file_path"] in seen_paths:
                report.errors.append("duplicate manifest entry (file_id or file_path already listed); not parsed")
                report.finish()
                continue
            seen_ids.add(row["file_id"])
            seen_paths.add(row["file_path"])
            path = dataset_dir / row["file_path"]
            captured = parse_time_or_none(row.get("captured_at"))
            if captured is None:
                report.warnings.append(f"manifest captured_at {row.get('captured_at')!r} is missing or unparseable")
            ctx = ParseContext(row["file_id"], path, row["file_path"], report.file_format, captured, report)
            parser = PARSERS.get(path.suffix.lower())
            if not path.is_file():
                report.errors.append("file listed in the manifest does not exist")
            elif parser is None:
                report.errors.append(f"unsupported evidence format {path.suffix!r}")
            elif path.suffix.lower() == ".png" and not ocr:
                report.parser = "ocr"
                report.errors.append("OCR disabled for this build; screenshot not parsed")
            else:
                try:
                    if path.suffix.lower() == ".png":
                        parser(ctx, ocr_cache)
                    else:
                        parser(ctx)
                except Exception as exc:  # a parser bug must surface as a failed file, not vanish
                    report.errors.append(f"parser raised {type(exc).__name__}: {exc}")
                    ctx.records.clear()
                    ctx.scopes.clear()
            contexts.append(ctx)

        listed = {(dataset_dir / r["file_path"]).resolve() for r in manifest}
        evidence_root = dataset_dir / "evidence"
        unlisted = sorted(
            str(p.relative_to(dataset_dir)) for p in evidence_root.rglob("*") if p.is_file() and p.resolve() not in listed
        ) if evidence_root.is_dir() else []
        notes["unlisted_files"] = unlisted

        learned = _learn_maps(ctx.records for ctx in contexts)
        learned["collection_to_system_via_prefix"] = _learn_collections_via_prefix(
            (ctx.records for ctx in contexts), learned
        )
        records: list[EvidenceRecord] = []
        scopes: list[SnapshotScope] = []
        for ctx in contexts:
            file_records = [_resolve_record(r, learned, ctx) for r in ctx.records]
            file_scopes = [_resolve_scope(s, learned, ctx) for s in ctx.scopes]
            for record in file_records:
                if record.observed_at and ctx.captured_at and record.observed_at > ctx.captured_at:
                    ctx.report.warnings.append(f"{record.location}: observed_at is later than the file capture time")
            ctx.report.records, ctx.report.scopes = len(file_records), len(file_scopes)
            ctx.report.finish()
            records.extend(file_records)
            scopes.extend(file_scopes)
        for report in reports:
            if not report.parser and not report.errors:
                report.finish()

        if include_activity:
            activity, warnings = activity_records(read_csv("system_activity.csv", dataset_dir))
            activity = [_resolve_activity(r, learned) for r in activity]
            records.extend(activity)
            notes["activity"] = {"rows": len(activity), "warnings": warnings,
                                 "rows_with_identifiers": sum(1 for r in activity if r.identifiers)}
        notes["ocr"] = {"engine": tesseract_version() if ocr else None, "scale": OCR_SCALE}
        return cls(records, scopes, reports, learned, notes)

    # ------------------------------------------------------------- queries
    def visible(self, audit_at: datetime) -> list[EvidenceRecord]:
        return [r for r in self.records if r.visible_at(audit_at)]

    def match(
        self,
        ids: Identifiers,
        audit_at: datetime,
        owners: Mapping[tuple[str, str], frozenset[str]] | None = None,
    ) -> CaseEvidence:
        """Records visible at audit_at that belong to the person, by exact identifier, then one hop
        through the resource IDs those records established. Timestamps and names never link."""
        keys = identity_keys(ids)
        visible = self.visible(audit_at)
        direct = {id(r): r.identifiers & keys for r in visible if r.identifiers & keys}
        linked = {r.resource_id for r in visible if id(r) in direct and r.resource_id}
        related = {x for r in visible if id(r) in direct for x in r.related_resources} - linked
        # the same sets, built from evidence content only (activity rows are claims)
        content_linked = {r.resource_id for r in visible if id(r) in direct and r.resource_id and not r.is_claim}
        content_related = {x for r in visible if id(r) in direct and not r.is_claim for x in r.related_resources}
        matches = []
        for record in visible:
            if id(record) in direct:
                basis, via, matched = "identifier", None, frozenset(direct[id(record)])
                claim_only = record.is_claim
            elif record.resource_id in linked:
                basis, via, matched = "resource_id", record.resource_id, frozenset()
                claim_only = record.resource_id not in content_linked
            elif record.resource_id in related:
                basis, via, matched = "related_resource", record.resource_id, frozenset()
                claim_only = record.resource_id not in content_related
            elif record.related_resources & linked:
                basis, via, matched = "references_linked_resource", min(record.related_resources & linked), frozenset()
                claim_only = record.is_claim  # the relationship is stated by this record itself
            else:
                continue
            foreign = frozenset()
            if owners is not None:
                foreign = frozenset(
                    key for key in record.identifiers
                    if key[0] in LINK_KINDS and key not in keys and owners.get(key) and ids.user_id not in owners[key]
                )
            matches.append(MatchedRecord(record, basis, matched, via, foreign, claim_only))
        matches.sort(key=lambda m: (str(m.record.system_id), str(m.record.resource_id), m.record.observed_at, m.record.evidence_id, m.record.location))
        resource_ids = linked | related
        scopes = [s for s in self.scopes if s.visible_at(audit_at) and (s.resource_ids & resource_ids or s.system_id in {m.record.system_id for m in matches})]
        return CaseEvidence(ids.user_id, audit_at, matches, scopes)

    # ------------------------------------------------------------- summary
    def summary(self) -> dict[str, object]:
        status = defaultdict(int)
        formats = defaultdict(lambda: defaultdict(int))
        for report in self.reports:
            status[report.status] += 1
            formats[report.file_format][report.status] += 1
        file_records = [r for r in self.records if r.source == "file"]
        return {
            "manifest_entries": len(self.reports),
            "status_counts": dict(sorted(status.items())),
            "by_format": {fmt: dict(sorted(c.items())) for fmt, c in sorted(formats.items())},
            "records": {
                "from_files": len(file_records),
                "from_activity": len(self.records) - len(file_records),
                "without_usable_time": sum(1 for r in self.records if r.observed_at is None),
                "without_resource_id": sum(1 for r in file_records if not r.resource_id),
                "unresolved_system": sum(1 for r in file_records if r.resource_id and not r.system_id),
                "by_state": dict(sorted(_count(r.state or "None" for r in file_records).items())),
                "by_extraction": dict(sorted(_count(r.extraction for r in file_records).items())),
            },
            "scopes": len(self.scopes),
            "learned_maps": self.learned,
            "notes": self.notes,
            "failed_or_partial": [
                {"file_id": r.file_id, "file_path": r.file_path, "status": r.status,
                 "reasons": r.errors + r.warnings}
                for r in self.reports if r.status != "parsed"
            ],
            "files": [r.as_dict() for r in self.reports],
        }


def _count(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for value in values:
        counts[value] += 1
    return counts


def _learn_maps(record_groups: Iterable[list[EvidenceRecord]]) -> dict[str, dict[str, str]]:
    """resource-ID prefix -> system and collection -> system, learned only from records whose
    source names the system explicitly; a key seen with two systems is left out."""
    prefixes: dict[str, set[str]] = defaultdict(set)
    collections: dict[str, set[str]] = defaultdict(set)
    for group in record_groups:
        for record in group:
            if record.system_basis != "explicit" or not record.system_id:
                continue
            if record.resource_id and "_" in record.resource_id:
                prefixes[record.resource_id.rsplit("_", 1)[0]].add(record.system_id)
            if record.collection and record.file_format in ("sql", "json"):
                collections[record.collection].add(record.system_id)
    return {
        "prefix_to_system": {k: next(iter(v)) for k, v in sorted(prefixes.items()) if len(v) == 1},
        "collection_to_system": {k: next(iter(v)) for k, v in sorted(collections.items()) if len(v) == 1},
        "ambiguous_prefixes": {k: sorted(v) for k, v in sorted(prefixes.items()) if len(v) > 1},
        "ambiguous_collections": {k: sorted(v) for k, v in sorted(collections.items()) if len(v) > 1},
    }


def _learn_collections_via_prefix(record_groups, learned) -> dict[str, str]:
    """Tables without a system column (inventory tables, typed replicas) get one when every row
    seen anywhere resolves to the same system by its resource-ID prefix."""
    systems: dict[str, set[str | None]] = defaultdict(set)
    for group in record_groups:
        for record in group:
            if record.system_id or not record.collection or record.file_format not in ("sql", "json"):
                continue
            if record.collection in learned["collection_to_system"]:
                continue
            prefix = record.resource_id.rsplit("_", 1)[0] if record.resource_id and "_" in record.resource_id else None
            systems[record.collection].add(learned["prefix_to_system"].get(prefix))
    return {k: next(iter(v)) for k, v in sorted(systems.items()) if len(v) == 1 and None not in v}


def _system_for(resource_id, collection, learned, fallback_service=None) -> tuple[str | None, str]:
    if resource_id and "_" in resource_id:
        system = learned["prefix_to_system"].get(resource_id.rsplit("_", 1)[0])
        if system:
            return system, "resource_prefix"
    if collection and collection in learned["collection_to_system"]:
        return learned["collection_to_system"][collection], "collection"
    if collection and collection in learned.get("collection_to_system_via_prefix", {}):
        return learned["collection_to_system_via_prefix"][collection], "collection_via_prefix"
    if fallback_service:
        return fallback_service, "log_service"
    return None, "unresolved"


def _resolve_record(record: EvidenceRecord, learned, ctx: ParseContext) -> EvidenceRecord:
    changes = {}
    if not record.system_id:
        service = record.fields.get("service") if record.file_format == "log" else None
        changes["system_id"], changes["system_basis"] = _system_for(record.resource_id, record.collection, learned, service)
    system = changes.get("system_id", record.system_id)
    if record.time_basis == "pending:snapshot_scope":
        if system in ctx.scope_times:
            changes["observed_at"], changes["time_basis"] = ctx.scope_times[system], "file:snapshot_scope"
        else:
            changes["time_basis"] = "file:header_capture_time"
            ctx.report.warnings.append(f"{record.location}: system {system} is not listed in snapshot_scope")
    return dataclasses.replace(record, **changes) if changes else record


def _resolve_activity(record: EvidenceRecord, learned) -> EvidenceRecord:
    system, basis = _system_for(record.resource_id, None, learned)
    if system is None and record.fields.get("target_system"):
        system, basis = record.fields["target_system"], "activity_target"
    return dataclasses.replace(record, system_id=system, system_basis=basis)


def _resolve_scope(scope: SnapshotScope, learned, ctx: ParseContext) -> SnapshotScope:
    changes = {}
    if not scope.system_id:
        prefixes = {rid.rsplit("_", 1)[0] for rid in scope.resource_ids}
        systems = {learned["prefix_to_system"].get(p) for p in prefixes}
        if scope.collection in learned["collection_to_system"]:
            changes["system_id"], changes["system_basis"] = learned["collection_to_system"][scope.collection], "collection"
        elif scope.collection in learned.get("collection_to_system_via_prefix", {}):
            changes["system_id"] = learned["collection_to_system_via_prefix"][scope.collection]
            changes["system_basis"] = "collection_via_prefix"
        elif len(systems) == 1 and None not in systems:
            changes["system_id"], changes["system_basis"] = systems.pop(), "resource_prefix"
    system = changes.get("system_id", scope.system_id)
    if scope.time_basis == "pending:snapshot_scope":
        if system in ctx.scope_times:
            changes["captured_at"], changes["time_basis"] = ctx.scope_times[system], "file:snapshot_scope"
        else:
            changes["time_basis"], changes["coverage_confirmed"] = "file:header_capture_time", False
    return dataclasses.replace(scope, **changes) if changes else scope
