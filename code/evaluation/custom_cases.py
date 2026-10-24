"""Team-authored evaluation cases on a synthetic fixture (not part of the challenge dataset).

Every value below is synthetic (IDs contain 9201-9206 and SYNTHETIC, emails use example.test).
The fixture is written to a temporary directory together with a read-only copy of the real
dataset/deletion_rules.csv; nothing is written under dataset/. Each case states the expected
status and steps derived from the challenge rules, runs the real engine and step generator, and
compares the full result.

    python3 code/evaluation/custom_cases.py      # prints each case and writes artifacts/custom_cases.json
"""
from __future__ import annotations

import csv
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))

from ghostaudit.audit import AuditEngine  # noqa: E402
from ghostaudit.data import DATASET_DIR  # noqa: E402
from ghostaudit.identity import email_hash  # noqa: E402
from ghostaudit.steps import build_steps  # noqa: E402

AUDIT_AT = "2026-09-15T12:00:00Z"
DELETE_TEXT = "Please permanently delete the account associated with this email address."


def _csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def ledger(resource_id, system, record_type, result, subject, recorded_at="2026-09-12T00:00:00Z"):
    return {"recorded_at": recorded_at, "system": system, "resource_id": resource_id, "record_type": record_type,
            "result": result, "subject_reference": subject}


def build_fixture(root: Path) -> None:
    people = [("user_9201", "SYNTHETIC Rita", "rita.new@example.test"), ("user_9202", "SYNTHETIC Sam", "sam@example.test"),
              ("user_9203", "SYNTHETIC Tess", "tess@example.test"), ("user_9204", "SYNTHETIC Uma", "uma@example.test"),
              ("user_9205", "SYNTHETIC Vic", "vic@example.test"), ("user_9206", "SYNTHETIC Wen", "wen@example.test")]
    _csv(root / "user_identities.csv", ["user_id", "name", "email", "phone", "email_hash", "updated_at"],
         [[u, n, e, f"+1 555 01{u[-2:]}", email_hash(e), "2026-05-01T00:00:00Z"] for u, n, e in people])
    _csv(root / "user_audit_log.csv", ["audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"],
         [["audit_s9201", "user_9201", "2026-05-01T00:00:00Z", "email", "rita.old@example.test", "rita.new@example.test"]])
    shutil.copy(DATASET_DIR / "deletion_rules.csv", root / "deletion_rules.csv")
    _csv(root / "system_activity.csv",
         ["activity_id", "event_time", "event_type", "source_system", "target_system", "resource_id", "reported_result", "details"],
         [["SYNTHETIC_act_1", "2026-09-05T00:00:00Z", "DISABLE_DATA_FLOW", "deletion_worker", "integration_service",
           "data_flow_9203", "SUCCESS", "Policy action completed for data_flow_9203."]])
    records = root / "evidence" / "records"
    records.mkdir(parents=True)
    (records / "checks.json").write_text(json.dumps({"exported_at": "2026-09-12T00:00:00Z", "records": [
        ledger("account_9201", "account_service", "USER_PROFILE", "NOT_FOUND", "rita.old@example.test"),
        ledger("account_9202", "account_service", "USER_PROFILE", "NOT_FOUND", "sam@example.test"),
        ledger("account_9203", "account_service", "USER_PROFILE", "NOT_FOUND", "tess@example.test"),
        ledger("data_flow_9203", "integration_service", "DATA_FLOW", "ACTIVE", "tess@example.test"),
        ledger("backup_9203", "backup_service", "BACKUP_ARCHIVE", "ACTIVE", "user_9203"),
        ledger("account_9205", "account_service", "USER_PROFILE", "NOT_FOUND", "vic@example.test"),
        ledger("account_9206", "account_service", "USER_PROFILE", "NOT_FOUND", "wen@example.test"),
    ]}), encoding="utf-8")
    (records / "export.json").write_text(json.dumps({"exported_at": "2026-09-10T00:00:00Z", "service_exports": {
        "security_service": {"audit_records": [
            {"resource_id": "audit_record_9202", "subject_reference": "user_9202", "event_code": "account_deletion_reviewed",
             "retention_until": "2033-01-01T00:00:00Z", "observed_at": "2026-09-10T00:00:00Z"}]},
        "search_service": {"index_queries": [
            {"resource_id": "search_9201", "query": "rita.old@example.test", "observed_at": "2026-09-10T00:00:00Z",
             "hits": {"total": 1, "documents": [{"resource_id": "search_9201", "email": "RITA.OLD@EXAMPLE.TEST",
                                                 "display_name": "SYNTHETIC Rita"}]}},
            {"resource_id": "search_9299", "query": "rita.olde@example.test", "observed_at": "2026-09-10T00:00:00Z",
             "hits": {"total": 1, "documents": [{"resource_id": "search_9299", "email": "rita.olde@example.test",
                                                 "display_name": "SYNTHETIC Rita"}]}},
            {"resource_id": "search_9204", "query": "uma@example.test", "observed_at": "2026-09-10T00:00:00Z",
             "hits": {"total": 1, "documents": [{"resource_id": "search_9204", "email": "uma@example.test"}]}}]},
    }}), encoding="utf-8")
    (records / "bundle.json").write_text(json.dumps({"exported_at": "2026-09-11T00:00:00Z", "snapshots": [
        {"captured_at": "2026-09-11T00:00:00Z", "data_store": "identity_service", "collection": "deleted_customer_references",
         "records": [{"resource_id": "tombstone_9206", "subject_reference": "user_9206", "name": "SYNTHETIC Wen",
                      "email": "wen@example.test", "phone": None, "email_hash": None, "account_status": "DELETED"}]}]}),
        encoding="utf-8")
    (records / "late.json").write_text(json.dumps({"exported_at": "2026-10-05T00:00:00Z", "collection": "customer_search_documents",
                                                   "records": [{"resource_id": "search_9205", "email": "vic@example.test"}]}),
                                       encoding="utf-8")
    _csv(root / "evidence_files.csv", ["file_id", "file_path", "origin", "captured_at"], [
        ["SYNTHETIC_checks", "evidence/records/checks.json", "SYNTHETIC", "2026-09-12T00:00:00Z"],
        ["SYNTHETIC_export", "evidence/records/export.json", "SYNTHETIC", "2026-09-10T00:00:00Z"],
        ["SYNTHETIC_bundle", "evidence/records/bundle.json", "SYNTHETIC", "2026-09-11T00:00:00Z"],
        ["SYNTHETIC_late", "evidence/records/late.json", "SYNTHETIC", "2026-10-05T00:00:00Z"],
    ])


def step(n, phase, system, resource, operation, state, evidence=""):
    return {"step_no": n, "phase": phase, "system_id": system, "resource_id": resource, "operation": operation,
            "state": state, "evidence_id": evidence}


CASES = [
    {
        "id": "CUSTOM_01_retained_security_record",
        "scenario": "The account is gone and a security audit record within its retention period remains.",
        "rule_basis": "rule_01 deletes USER_PROFILE; rule_12 retains SECURITY_AUDIT_LOG within the retention period "
                      "(retention_until 2033 > audit_at). COMPLETE cases contain only evidence-backed verification steps; "
                      "the retained record is verified PRESENT and never deleted.",
        "request": {"email": "sam@example.test"},
        "expected_status": "COMPLETE",
        "expected_steps": [
            step(1, "VERIFICATION", "account_service", "account_9202", "VERIFY_RECORD_ABSENT", "ABSENT", "SYNTHETIC_checks"),
            step(2, "VERIFICATION", "security_service", "audit_record_9202", "VERIFY_RECORD_PRESENT", "PRESENT", "SYNTHETIC_export"),
        ],
    },
    {
        "id": "CUSTOM_02_historical_email_and_near_match_decoy",
        "scenario": "The request uses the person's former email in different case; a search document still holds it, and "
                    "a decoy document with the same name and a one-letter-different email (rita.olde@) sits beside it.",
        "rule_basis": "Identifiers include historical emails up to audit_at and match case-insensitively; names and "
                      "similar emails never link. rule_03 deletes SEARCH_INDEX_ENTRY, so the remaining document is a finding.",
        "request": {"email": "  Rita.Old@Example.Test "},
        "expected_status": "INCOMPLETE",
        "expected_steps": [
            step(1, "FINDING", "search_service", "search_9201", "REPORT_REMAINING_RECORD", "PRESENT", "SYNTHETIC_export"),
            step(2, "REMEDIATION", "search_service", "search_9201", "DELETE_RECORD", "ABSENT"),
            step(3, "VERIFICATION", "account_service", "account_9201", "VERIFY_RECORD_ABSENT", "ABSENT"),
            step(4, "VERIFICATION", "search_service", "search_9201", "VERIFY_RECORD_ABSENT", "ABSENT"),
        ],
    },
    {
        "id": "CUSTOM_03_active_flow_and_restore_risk",
        "scenario": "The account is gone, but a connector for the person is still ACTIVE and a backup still allows restore; "
                    "a workflow claim says the connector was disabled.",
        "rule_basis": "No active sync or restore path may remain (problem statement). rule_14 retains BACKUP_ARCHIVE only "
                      "with restore blocked. The claim SYNTHETIC_act_1 is not proof. Remediation disables the flow and "
                      "blocks the restore first, in the operation order of the specification.",
        "request": {"email": "tess@example.test"},
        "expected_status": "INCOMPLETE",
        "expected_steps": [
            step(1, "FINDING", "integration_service", "data_flow_9203", "REPORT_ACTIVE_DATA_FLOW", "ACTIVE", "SYNTHETIC_checks"),
            step(2, "FINDING", "backup_service", "backup_9203", "REPORT_RESTORE_RISK", "ACTIVE", "SYNTHETIC_checks"),
            step(3, "REMEDIATION", "integration_service", "data_flow_9203", "DISABLE_DATA_FLOW", "DISABLED"),
            step(4, "REMEDIATION", "backup_service", "backup_9203", "BLOCK_RESTORE", "BLOCKED"),
            step(5, "VERIFICATION", "account_service", "account_9203", "VERIFY_RECORD_ABSENT", "ABSENT"),
            step(6, "VERIFICATION", "integration_service", "data_flow_9203", "VERIFY_DATA_FLOW_DISABLED", "DISABLED"),
            step(7, "VERIFICATION", "backup_service", "backup_9203", "VERIFY_RESTORE_BLOCKED", "BLOCKED"),
        ],
    },
    {
        "id": "CUSTOM_04_non_deletion_request",
        "scenario": "The person asks for a copy of their data and explicitly declines deletion, although a search "
                    "document for them exists.",
        "rule_basis": "A request for access or export is NOT_APPLICABLE with steps_json [] (AGENTS.md 6.5), whatever "
                      "data exists.",
        "request": {"email": "uma@example.test",
                    "request_text": "Could you send me a copy of my data? Please don't delete or change anything."},
        "expected_status": "NOT_APPLICABLE",
        "expected_steps": [],
    },
    {
        "id": "CUSTOM_05_evidence_after_audit_at_is_ignored",
        "scenario": "The account was confirmed absent before audit_at; a file captured three weeks after audit_at "
                    "shows a search document for the person.",
        "rule_basis": "Only information available at or before audit_at decides the case, and later evidence is never "
                      "cited (AGENTS.md 6.2, 6.6).",
        "request": {"email": "vic@example.test"},
        "expected_status": "COMPLETE",
        "expected_steps": [
            step(1, "VERIFICATION", "account_service", "account_9205", "VERIFY_RECORD_ABSENT", "ABSENT", "SYNTHETIC_checks"),
        ],
    },
    {
        "id": "CUSTOM_06_tombstone_still_exposes_identifiers",
        "scenario": "The account is gone, but its deleted-account reference still carries the person's name and email.",
        "rule_basis": "rule_15: an ACCOUNT_TOMBSTONE keeps only the stable user reference; name and email must be "
                      "redacted, so the exposed identifiers are a finding and the fix is REDACT_RECORD.",
        "request": {"email": "wen@example.test"},
        "expected_status": "INCOMPLETE",
        "expected_steps": [
            step(1, "FINDING", "identity_service", "tombstone_9206", "REPORT_EXPOSED_PII", "PRESENT", "SYNTHETIC_bundle"),
            step(2, "REMEDIATION", "identity_service", "tombstone_9206", "REDACT_RECORD", "REDACTED"),
            step(3, "VERIFICATION", "account_service", "account_9206", "VERIFY_RECORD_ABSENT", "ABSENT"),
            step(4, "VERIFICATION", "identity_service", "tombstone_9206", "VERIFY_RECORD_REDACTED", "REDACTED"),
        ],
    },
]


def run_cases() -> list[dict]:
    root = Path(tempfile.mkdtemp(prefix="SYNTHETIC_custom_cases_"))
    try:
        build_fixture(root)
        engine = AuditEngine.build(root, ocr_cache=None)
        results = []
        for case in CASES:
            request = {"case_id": case["id"], "requested_at": "2026-09-01T00:00:00Z", "reported_status": "COMPLETE",
                       "reported_at": "2026-09-02T00:00:00Z", "audit_at": AUDIT_AT, "request_text": DELETE_TEXT, **case["request"]}
            audit = engine.audit(request)
            steps, notes = build_steps(audit)
            passed = audit["status"] == case["expected_status"] and steps == case["expected_steps"]
            results.append({**case, "actual_status": audit["status"], "actual_steps": steps, "step_notes": notes,
                             "status_reason": audit["status_reason"], "passed": passed})
        return results
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    results = run_cases()
    out = ROOT / "artifacts" / "custom_cases.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    for r in results:
        print(f"{'PASS' if r['passed'] else 'FAIL'} {r['id']}: expected {r['expected_status']} / {len(r['expected_steps'])} steps;"
              f" actual {r['actual_status']} / {len(r['actual_steps'])} steps")
        if not r["passed"]:
            print("   actual steps:", json.dumps(r["actual_steps"]))
    print(f"{sum(r['passed'] for r in results)}/{len(results)} custom cases passed; written to {out.relative_to(ROOT)}")
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
