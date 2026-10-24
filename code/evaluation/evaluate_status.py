"""Compare the audit engine with the 32 public sample answers (evaluation only).

Status is the headline metric. Two deeper checks guard against right answers for wrong reasons:
* findings: each sample FINDING step (system, resource, kind) against the engine's violations;
* treatment: for every resource both sides know, the sample's final verification operation
  against the engine's required action (ABSENT=DELETE, REDACTED=REDACT, ANONYMIZED=ANONYMIZE,
  PRESENT=RETAIN, RESTORE_BLOCKED=RETAIN with restore blocked, DATA_FLOW_DISABLED=DISABLE_FLOW).

    python3 code/evaluation/evaluate_status.py   # writes artifacts/sample_status_evaluation.json
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghostaudit.audit import AuditEngine  # noqa: E402
from ghostaudit.data import REPO_ROOT, read_csv  # noqa: E402

OUTPUT = REPO_ROOT / "artifacts" / "sample_status_evaluation.json"
FINDING_KIND = {
    "REPORT_REMAINING_RECORD": "REMAINING_RECORD",
    "REPORT_EXPOSED_PII": "EXPOSED_PII",
    "REPORT_ACTIVE_DATA_FLOW": "ACTIVE_DATA_FLOW",
    "REPORT_RESTORE_RISK": "RESTORE_RISK",
}
VERIFY_TREATMENT = {
    "VERIFY_RECORD_ABSENT": "DELETE",
    "VERIFY_RECORD_REDACTED": "REDACT",
    "VERIFY_RECORD_ANONYMIZED": "ANONYMIZE",
    "VERIFY_RECORD_PRESENT": "RETAIN",
    "VERIFY_RESTORE_BLOCKED": "RETAIN",
    "VERIFY_DATA_FLOW_DISABLED": "DISABLE_FLOW",
}


def evaluate(engine: AuditEngine) -> dict:
    requests = {r["case_id"]: r for r in read_csv("sample_deletion_requests.csv")}
    cases, confusion = [], Counter()
    finding_tp = finding_fn = finding_fp = 0
    treatment_agree = treatment_total = 0
    treatment_misses = []
    for answer in read_csv("sample_output.csv"):
        result = engine.audit(requests[answer["case_id"]])
        steps = json.loads(answer["steps_json"])
        expected_findings = {(s["system_id"], s["resource_id"], FINDING_KIND[s["operation"]])
                             for s in steps if s["phase"] == "FINDING"}
        predicted_findings = {(r["system_id"], r["resource_id"], r["finding"])
                              for r in result["resources"] if r["outcome"] == "VIOLATION"}
        finding_tp += len(expected_findings & predicted_findings)
        finding_fn += len(expected_findings - predicted_findings)
        finding_fp += len(predicted_findings - expected_findings)
        expected_treatment = {(s["system_id"], s["resource_id"]): VERIFY_TREATMENT[s["operation"]]
                              for s in steps if s["phase"] == "VERIFICATION"}
        for r in result["resources"]:
            key = (r["system_id"], r["resource_id"])
            if key in expected_treatment:
                treatment_total += 1
                if r["required_action"] == expected_treatment[key]:
                    treatment_agree += 1
                else:
                    treatment_misses.append({"case_id": answer["case_id"], "resource": "/".join(key),
                                             "expected": expected_treatment[key], "predicted": r["required_action"],
                                             "category": r["category"], "category_basis": r["category_basis"]})
        confusion[(answer["deletion_status"], result["status"])] += 1
        cases.append({
            "case_id": answer["case_id"],
            "expected_status": answer["deletion_status"],
            "predicted_status": result["status"],
            "match": answer["deletion_status"] == result["status"],
            "confidence": result["confidence"],
            "status_reason": result["status_reason"],
            "missed_findings": sorted("/".join(f) for f in expected_findings - predicted_findings),
            "extra_findings": sorted("/".join(f) for f in predicted_findings - expected_findings),
            "ambiguities": result["ambiguities"],
        })
    matched = sum(c["match"] for c in cases)
    return {
        "cases": len(cases),
        "status_matches": matched,
        "status_accuracy": round(matched / len(cases), 4),
        "confusion_expected_vs_predicted": {f"{e}->{p}": n for (e, p), n in sorted(confusion.items(), key=str)},
        "findings": {"expected_and_found": finding_tp, "expected_not_found": finding_fn, "found_not_expected": finding_fp},
        "treatment": {"agree": treatment_agree, "compared": treatment_total, "disagreements": treatment_misses},
        "case_details": cases,
    }


def main() -> int:
    result = evaluate(AuditEngine.build())
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"status: {result['status_matches']}/{result['cases']} ({result['status_accuracy']:.1%});"
          f" confusion {result['confusion_expected_vs_predicted']}")
    print(f"findings: {result['findings']}")
    print(f"treatment agreement: {result['treatment']['agree']}/{result['treatment']['compared']}")
    for miss in result["treatment"]["disagreements"]:
        print(f"  treatment {miss}")
    for case in result["case_details"]:
        if not case["match"] or case["missed_findings"] or case["extra_findings"]:
            print(f"  {case['case_id']}: expected {case['expected_status']} predicted {case['predicted_status']}"
                  f" missed {case['missed_findings']} extra {case['extra_findings']}")
    print(f"written to {OUTPUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
