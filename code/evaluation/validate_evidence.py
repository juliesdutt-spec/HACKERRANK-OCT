"""Check the evidence index against the 32 public sample answers (evaluation only).

For each sample case: resolve the person at audit_at, match evidence, then compare
* the resources the index links to the person with the resources named in sample_output.csv;
* the evidence IDs the sample cites with the evidence the index found for that resource;
* every cited evidence item against the audit_at cutoff.
sample_output.csv is read here only; production code never sees it.

    python3 code/evaluation/validate_evidence.py   # writes artifacts/sample_evidence_validation.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghostaudit.data import REPO_ROOT, parse_time, read_csv  # noqa: E402
from ghostaudit.evidence import EvidenceIndex, identifier_owners  # noqa: E402
from ghostaudit.identity import IdentityIndex  # noqa: E402

OUTPUT = REPO_ROOT / "artifacts" / "sample_evidence_validation.json"


def explain_missing(index: EvidenceIndex, keys, request: dict[str, str], resource_id: str) -> str:
    """Why an expected resource is not linked; diagnostic only, never fed back into matching."""
    audit_at, requested_at = parse_time(request["audit_at"]), parse_time(request["requested_at"])
    records = [r for r in index.records if r.resource_id == resource_id]
    if not records:
        return "no_participant_reference"
    if any(r.identifiers & keys and not r.visible_at(audit_at) for r in records):
        return "identifier_link_only_in_evidence_after_audit_at"
    if any(r.identifiers & keys for r in records):
        return "identifier_link_visible_but_not_matched"
    in_window = any(r.is_claim and r.observed_at and requested_at <= r.observed_at <= audit_at for r in records)
    if in_window:
        return "only_unlinked_records_plus_workflow_activity_in_request_window"
    return "only_records_without_identifiers"


def validate(index: EvidenceIndex, identity: IdentityIndex) -> dict:
    from ghostaudit.evidence import identity_keys

    owners = identifier_owners(identity)
    requests = {row["case_id"]: row for row in read_csv("sample_deletion_requests.csv")}
    manifest = {row["file_id"]: row for row in read_csv("evidence_files.csv")}
    cases = []
    for answer in read_csv("sample_output.csv"):
        request = requests[answer["case_id"]]
        audit_at = parse_time(request["audit_at"])
        user_id = identity.resolve_email(request["email"], audit_at)
        steps = json.loads(answer["steps_json"])
        expected = sorted({(s["system_id"], s["resource_id"]) for s in steps})
        cited = sorted({(s["system_id"], s["resource_id"], s["evidence_id"]) for s in steps if s["evidence_id"]})
        case = index.match(identity.identifiers(user_id, audit_at), audit_at, owners) if user_id else None
        found = case.resources() if case else {}
        evidence_by_resource = {key: {m.record.evidence_id for m in matches} for key, matches in found.items()}
        found_keys = set(found)
        cited_checks = []
        for system, resource, evidence_id in cited:
            item = manifest.get(evidence_id)
            cited_checks.append({
                "resource": f"{system}/{resource}",
                "evidence_id": evidence_id,
                "captured_at_or_before_audit": bool(item) and parse_time(item["captured_at"]) <= audit_at,
                "index_has_it_for_resource": evidence_id in evidence_by_resource.get((system, resource), set()),
            })
        keys = identity_keys(identity.identifiers(user_id, audit_at)) if user_id else frozenset()
        cases.append({
            "case_id": answer["case_id"],
            "deletion_status": answer["deletion_status"],
            "user_id": user_id,
            "expected_resources": [f"{s}/{r}" for s, r in expected],
            "linked_expected": [f"{s}/{r}" for s, r in expected if (s, r) in found_keys],
            "missing_expected": [f"{s}/{r}" for s, r in expected if (s, r) not in found_keys],
            "missing_reasons": {
                f"{s}/{r}": explain_missing(index, keys, request, r) for s, r in expected if (s, r) not in found_keys
            },
            "extra_linked": sorted(f"{s}/{r}" for s, r in found_keys - set(expected)),
            "cited_evidence": cited_checks,
            "link_bases": sorted({m.basis for m in case.matches}) if case else [],
        })
    expected_total = sum(len(c["expected_resources"]) for c in cases)
    linked_total = sum(len(c["linked_expected"]) for c in cases)
    cited_total = sum(len(c["cited_evidence"]) for c in cases)
    cited_found = sum(ch["index_has_it_for_resource"] for c in cases for ch in c["cited_evidence"])
    reasons: dict[str, int] = {}
    for c in cases:
        for reason in c["missing_reasons"].values():
            reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "missing_resource_reasons": dict(sorted(reasons.items())),
        "cases": len(cases),
        "expected_resources": expected_total,
        "expected_resources_linked": linked_total,
        "cases_with_all_resources_linked": sum(not c["missing_expected"] for c in cases),
        "cited_evidence": cited_total,
        "cited_evidence_found_for_resource": cited_found,
        "cited_evidence_after_audit_at": sum(not ch["captured_at_or_before_audit"] for c in cases for ch in c["cited_evidence"]),
        "extra_linked_resources": sum(len(c["extra_linked"]) for c in cases),
        "case_details": cases,
    }


def main() -> int:
    index = EvidenceIndex.build()
    identity = IdentityIndex(read_csv("user_identities.csv"), read_csv("user_audit_log.csv"))
    result = validate(index, identity)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"cases {result['cases']}: resources linked {result['expected_resources_linked']}/{result['expected_resources']}"
          f" (all linked in {result['cases_with_all_resources_linked']} cases);"
          f" cited evidence found {result['cited_evidence_found_for_resource']}/{result['cited_evidence']};"
          f" cited after audit_at {result['cited_evidence_after_audit_at']}; extra linked {result['extra_linked_resources']}")
    print(f"why expected resources are not linked: {result['missing_resource_reasons']}")
    for case in result["case_details"]:
        misses = [c for c in case["cited_evidence"] if not c["index_has_it_for_resource"]]
        if case["missing_expected"] or misses:
            print(f"  {case['case_id']} {case['user_id']}: missing {case['missing_expected']}"
                  f" cited-not-found {[(m['resource'], m['evidence_id']) for m in misses]}")
    print(f"written to {OUTPUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
