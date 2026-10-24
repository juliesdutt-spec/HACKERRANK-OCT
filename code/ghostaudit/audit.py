"""Per-case deletion audit: request intent, identity, linked evidence, rule treatment, status.

Status semantics (problem statement and AGENTS.md section 6.5):
* NOT_APPLICABLE when the request text explicitly declines deletion (requests.classify_request);
* INCOMPLETE when at least one linked resource is in a confirmed violation at audit_at: a copy
  that must be deleted is observed, identifiers that must be redacted or anonymized remain, a
  connector is enabled, a restore is still allowed, or a record that must be retained is gone;
* COMPLETE otherwise. A case with no violation but with resources whose state cannot be
  established is still COMPLETE, because INCOMPLETE needs evidence of an unmet requirement;
  its confidence is "unverified" and the unknown resources are listed.
Reported workflow statuses and activity results are kept as claims and never decide anything.
A linked resource whose evidence all lies in the evidence window (requested_at..audit_at) of the
same person's earlier request, before this request was made, is listed under prior_resources and
does not take part: that evidence was the subject of the earlier request.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .data import DATASET_DIR, parse_time, read_csv
from .evidence import EvidenceIndex, identifier_owners, identity_keys
from .evidence.index import CaseEvidence, MatchedRecord
from .identity import IdentityIndex
from .policy import (
    COMPLIANT,
    UNKNOWN,
    VIOLATION,
    assess_resource,
    assessment_dict,
    content_identifiers,
    learn_prefix_categories,
    load_rules,
)
from .requests import AMBIGUOUS, NON_DELETION, classify_request

COMPLETE, INCOMPLETE, NOT_APPLICABLE = "COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"
LINKING_KINDS = ("email", "email_hash", "phone")  # direct identifiers that can contradict a claimed owner


def _group_by_resource(matches: list[MatchedRecord]) -> list[tuple[str, list[MatchedRecord]]]:
    """Resource IDs are unique across systems (each prefix maps to one system), so records of one
    resource are grouped by ID even when a screenshot's system could not be resolved."""
    grouped: dict[str, list[MatchedRecord]] = defaultdict(list)
    for match in matches:
        if match.record.resource_id:
            grouped[match.record.resource_id].append(match)
    return sorted(grouped.items())


def _contradicting_content(matches: list[MatchedRecord], keys) -> list[str]:
    """Direct identifiers in the resource's own evidence content when none of them is the person's."""
    found = set()
    for match in matches:
        if match.record.is_claim:
            continue
        found |= {k for k in content_identifiers(match.record) if k[0] in LINKING_KINDS}
    if found and not found & keys:
        return sorted(f"{k}={v}" for k, v in found)
    return []


REQUEST_FILES = ("sample_deletion_requests.csv", "deletion_requests.csv")


class AuditEngine:
    def __init__(self, index: EvidenceIndex, identity: IdentityIndex, rules, requests: list[dict[str, str]] = ()):
        self.index = index
        self.identity = identity
        self.rules = rules
        self.owners = identifier_owners(identity)
        # every request's evidence window, per person, to recognise evidence of an earlier request
        self.windows: dict[str, list[tuple[str, datetime, datetime]]] = defaultdict(list)
        for request in requests:
            audit_at = parse_time(request["audit_at"])
            user_id = identity.resolve_email(request["email"], audit_at)
            if user_id:
                self.windows[user_id].append((request["case_id"], parse_time(request["requested_at"]), audit_at))
        self.prefix_categories = learn_prefix_categories([r for r in index.records if r.source == "file"])
        self._by_resource: dict[str, list] = defaultdict(list)
        for record in index.records:
            if record.resource_id:
                self._by_resource[record.resource_id].append(record)

    @classmethod
    def build(cls, dataset_dir: Path = DATASET_DIR, **index_options) -> "AuditEngine":
        index = EvidenceIndex.build(dataset_dir, **index_options)
        identity = IdentityIndex(read_csv("user_identities.csv", dataset_dir), read_csv("user_audit_log.csv", dataset_dir))
        requests = [row for name in REQUEST_FILES if (Path(dataset_dir) / name).is_file() for row in read_csv(name, dataset_dir)]
        return cls(index, identity, load_rules(dataset_dir), requests)

    def _earlier_request_covering(self, user_id: str, case_id: str, requested_at: datetime, times: list[datetime]) -> str | None:
        """The same person's earlier request whose evidence window holds all of these times."""
        for other_case, other_requested, other_audit in sorted(self.windows.get(user_id, []), key=lambda w: (w[1], w[0])):
            if other_case != case_id and other_audit <= requested_at and all(other_requested <= t <= other_audit for t in times):
                return other_case
        return None

    def audit(self, request: dict[str, str]) -> dict[str, Any]:
        audit_at = parse_time(request["audit_at"])
        intent = classify_request(request["request_text"])
        result: dict[str, Any] = {
            "case_id": request["case_id"],
            "request": {k: request[k] for k in ("email", "requested_at", "reported_status", "reported_at", "audit_at")},
            "request_intent": {"intent": intent.intent, "cues": list(intent.cues), "reason": intent.reason},
            "user_id": None,
            "identifiers_used": None,
            "resources": [],
            "excluded_resources": [],
            "prior_resources": [],
            "unattached_mentions": [],
            "intake_claims": [],
            "ambiguities": [],
        }
        if intent.intent == AMBIGUOUS:
            result["ambiguities"].append("request text has no deletion term and no non-deletion statement; audited as a deletion request")

        user_id = self.identity.resolve_email(request["email"], audit_at)
        if user_id is None:
            result["ambiguities"].append("request email does not resolve to a user at audit_at")
            if intent.intent == NON_DELETION:
                return self._finish(result, NOT_APPLICABLE, intent.reason, "high")
            return self._finish(result, None, "identity unresolved; no evidence can be linked", "none")

        ids = self.identity.identifiers(user_id, audit_at)
        result["user_id"] = user_id
        result["identifiers_used"] = {
            "user_id": user_id, "emails": sorted(ids.emails), "email_hashes": sorted(ids.email_hashes), "phones": sorted(ids.phones),
        }
        case = self.index.match(ids, audit_at, self.owners)
        result["intake_claims"] = [
            {"evidence_id": m.record.evidence_id, "resource_id": m.record.resource_id, "details": m.record.fields.get("details")}
            for m in case.matches if m.record.is_claim and m.record.fields.get("event_type") == "CLASSIFY_REQUEST"
        ]
        if intent.intent == NON_DELETION:
            return self._finish(result, NOT_APPLICABLE, intent.reason, "high")

        assessments = self._assess(case, audit_at, result)
        violations = [a for a in assessments if a.outcome == VIOLATION]
        unknown = [a for a in assessments if a.outcome == UNKNOWN]
        if violations:
            names = ", ".join(f"{a.finding} {a.system_id}/{a.resource_id}" for a in violations)
            if all(a.claim_only_link for a in violations):
                return self._finish(result, INCOMPLETE, f"unmet requirement(s) on resources linked only by a workflow claim: {names}", "medium")
            return self._finish(result, INCOMPLETE, f"confirmed unmet requirement(s): {names}", "high")
        account_ok = any(a.category == "USER_PROFILE" and a.outcome == COMPLIANT for a in assessments)
        if not assessments:
            reason, confidence = "no evidence visible at audit_at links any resource to this person; no violation found", "unverified"
        elif unknown or not account_ok:
            gaps = [f"{a.system_id}/{a.resource_id}" for a in unknown]
            reason = "no violation found" + (f"; state not established for {', '.join(gaps)}" if gaps else "")
            if not account_ok:
                reason += "; the account record itself is not confirmed absent"
            confidence = "unverified"
        else:
            reason, confidence = "every linked resource meets its rule at audit_at", "verified"
        return self._finish(result, COMPLETE, reason, confidence)

    def _assess(self, case: CaseEvidence, audit_at: datetime, result: dict[str, Any]):
        keys = identity_keys(self.identity.identifiers(case.user_id, audit_at))
        requested_at = parse_time(result["request"]["requested_at"])
        assessments = []
        for resource_id, matches in _group_by_resource(case.matches):
            systems = [m.record.system_id for m in matches if m.record.system_id and not m.record.is_claim] or \
                      [m.record.system_id for m in matches if m.record.system_id]
            system_id = Counter(systems).most_common(1)[0][0] if systems else None
            observed = [m.record.observed_at for m in matches if not m.record.is_claim and m.record.observed_at]
            earlier = (self._earlier_request_covering(case.user_id, result["case_id"], requested_at, observed)
                       if observed and max(observed) < requested_at else None)
            if earlier:
                # All of this resource's evidence was captured for the same person's earlier request,
                # before this request was made; every evidence item the sample answers cite was
                # captured at or after its own case's requested_at.
                result["prior_resources"].append({
                    "system_id": system_id, "resource_id": resource_id,
                    "latest_evidence_at": max(observed).isoformat(), "earlier_request": earlier,
                    "evidence": sorted({m.record.evidence_id for m in matches if not m.record.is_claim}),
                    "reason": f"all evidence lies in the evidence window of the person's earlier request {earlier}",
                })
                continue
            if len(set(systems)) > 1:
                result["ambiguities"].append(f"{resource_id}: records name several systems {sorted(set(systems))}; using {system_id}")
            claim_only = all(m.claim_only for m in matches)
            contradiction = _contradicting_content(matches, keys) if claim_only else []
            if contradiction:
                result["ambiguities"].append(
                    f"{system_id}/{resource_id}: linked only by a workflow claim, and the record's own content names "
                    f"other identifiers ({', '.join(contradiction)}); excluded from the assessment"
                )
                result["excluded_resources"].append({
                    "system_id": system_id, "resource_id": resource_id, "reason": "claim link contradicted by evidence content",
                    "claims": sorted({m.record.evidence_id for m in matches if m.record.is_claim}),
                    "contradicting_evidence": sorted({m.record.evidence_id for m in matches if not m.record.is_claim}),
                })
                continue
            a = assess_resource(system_id, resource_id, matches, self.rules, self.prefix_categories,
                                case.scopes, audit_at)
            a.claim_only_link = claim_only
            assessments.append(a)
            entry = assessment_dict(a)
            entry["link_strength"] = "claim_only" if claim_only else "evidence_content"
            if claim_only:
                result["ambiguities"].append(f"{system_id}/{resource_id}: linked to the person only by a workflow claim")
            entry["linked_by"] = sorted({m.basis for m in matches})
            entry["evidence"] = [
                {
                    "evidence_id": m.record.evidence_id,
                    "file_path": m.record.file_path,
                    "location": m.record.location,
                    "observed_at": m.record.observed_at.isoformat() if m.record.observed_at else None,
                    "within_audit_cutoff": m.record.visible_at(audit_at),
                    "record_type": m.record.record_type,
                    "collection": m.record.collection,
                    "state": m.record.state,
                    "claim": m.record.is_claim,
                    "reported_result": m.record.fields.get("reported_result") if m.record.is_claim else None,
                    "link_basis": m.basis,
                    "matched_identifiers": sorted(f"{k}={v}" for k, v in m.matched_identifiers),
                }
                for m in matches
            ]
            entry["ignored_after_audit_at"] = sorted(
                {(r.evidence_id, r.observed_at.isoformat() if r.observed_at else None)
                 for r in self._by_resource.get(resource_id, []) if not r.visible_at(audit_at)},
                key=lambda item: (str(item[1]), item[0]),
            )
            for note in a.notes:
                if note.startswith("unresolved") or "conflicting" in note or "several categories" in note:
                    result["ambiguities"].append(f"{system_id}/{resource_id}: {note}")
            if a.outcome == UNKNOWN:
                result["ambiguities"].append(f"{system_id}/{resource_id}: state not established ({a.basis})")
            result["resources"].append(entry)
        result["unattached_mentions"] = [
            {"evidence_id": m.record.evidence_id, "location": m.record.location,
             "matched_identifiers": sorted(f"{k}={v}" for k, v in m.matched_identifiers)}
            for m in case.matches if not m.record.resource_id
        ]
        return assessments

    @staticmethod
    def _finish(result: dict[str, Any], status: str | None, reason: str, confidence: str) -> dict[str, Any]:
        result["ambiguities"] = sorted(set(result["ambiguities"]))
        result["status"] = status
        result["status_reason"] = reason
        result["confidence"] = confidence
        return result
