"""Build the evidence index, write the parse summary, and optionally trace cases.

    python3 code/index_evidence.py                       # writes artifacts/evidence_parse_summary.json
    python3 code/index_evidence.py --trace case_06       # request -> identity -> matched evidence
    python3 code/index_evidence.py --trace case_101 --requests deletion_requests.csv
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ghostaudit.data import REPO_ROOT, parse_time, read_csv  # noqa: E402
from ghostaudit.evidence import EvidenceIndex, identifier_owners  # noqa: E402
from ghostaudit.identity import IdentityIndex  # noqa: E402

DEFAULT_SUMMARY = REPO_ROOT / "artifacts" / "evidence_parse_summary.json"


def trace_case(index: EvidenceIndex, identity: IdentityIndex, owners, request: dict[str, str]) -> dict:
    audit_at = parse_time(request["audit_at"])
    user_id = identity.resolve_email(request["email"], audit_at)
    if user_id is None:
        return {"case_id": request["case_id"], "user_id": None, "note": "request email does not resolve"}
    ids = identity.identifiers(user_id, audit_at)
    case = index.match(ids, audit_at, owners)
    return {
        "case_id": request["case_id"],
        "audit_at": request["audit_at"],
        "user_id": user_id,
        "emails": sorted(ids.emails),
        "resources": {
            f"{system}/{resource}": [
                {
                    "evidence_id": m.record.evidence_id,
                    "location": m.record.location,
                    "observed_at": m.record.observed_at.isoformat() if m.record.observed_at else None,
                    "state": m.record.state,
                    "state_basis": m.record.state_basis,
                    "basis": m.basis,
                    "matched": sorted(f"{k}={v}" for k, v in m.matched_identifiers),
                    "foreign": sorted(f"{k}={v}" for k, v in m.foreign_identifiers),
                    "extraction": m.record.extraction,
                    "claim": m.record.is_claim,
                }
                for m in matches
            ]
            for (system, resource), matches in case.resources().items()
        },
        "unattached_identifier_mentions": [
            {"evidence_id": m.record.evidence_id, "location": m.record.location}
            for m in case.matches if not m.record.resource_id
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY, help="where to write the parse summary")
    parser.add_argument("--trace", nargs="*", default=[], help="case IDs to trace")
    parser.add_argument("--requests", default="sample_deletion_requests.csv", help="request file for --trace")
    parser.add_argument("--no-ocr", action="store_true", help="skip screenshots (they are reported as failed)")
    args = parser.parse_args(argv)

    index = EvidenceIndex.build(ocr=not args.no_ocr)
    summary = index.summary()
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=False, default=str) + "\n", encoding="utf-8")
    print(f"manifest entries: {summary['manifest_entries']}  status: {summary['status_counts']}")
    print(f"records: {summary['records']['from_files']} from files, {summary['records']['from_activity']} from activity;"
          f" scopes: {summary['scopes']}")
    for item in summary["failed_or_partial"]:
        print(f"  {item['status']:8s} {item['file_id']} {item['file_path']}: {item['reasons'][:3]}")
    print(f"summary written to {args.summary.relative_to(REPO_ROOT) if args.summary.is_relative_to(REPO_ROOT) else args.summary}")

    if args.trace:
        identity = IdentityIndex(read_csv("user_identities.csv"), read_csv("user_audit_log.csv"))
        owners = identifier_owners(identity)
        requests = {row["case_id"]: row for row in read_csv(args.requests)}
        for case_id in args.trace:
            if case_id not in requests:
                print(f"{case_id}: not in {args.requests}")
                continue
            print(json.dumps(trace_case(index, identity, owners, requests[case_id]), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
