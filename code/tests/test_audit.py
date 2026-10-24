"""End-to-end tests for the audit engine.

SyntheticEngineTests build a throwaway dataset (every value marked SYNTHETIC or @example.test)
in a temporary directory, with a read-only copy of the real deletion_rules.csv. RealSampleTests
run the real dataset and read expected statuses from dataset/sample_output.csv at test time.
"""
import csv
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghostaudit.audit import AuditEngine  # noqa: E402
from ghostaudit.data import DATASET_DIR, read_csv  # noqa: E402
from ghostaudit.identity import email_hash  # noqa: E402

DELETE_TEXT = "Please permanently delete the account associated with this email address."
AUDIT_AT = "2026-09-15T12:00:00Z"


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def build_synthetic_dataset(root: Path) -> None:
    write_csv(root / "user_identities.csv", ["user_id", "name", "email", "phone", "email_hash", "updated_at"], [
        ["user_9101", "SYNTHETIC Ada", "ada.new@example.test", "+1 555 0101", email_hash("ada.new@example.test"), "2026-05-01T00:00:00Z"],
        ["user_9102", "SYNTHETIC Ada", "ada.new2@example.test", "+1 555 0102", email_hash("ada.new2@example.test"), "2026-01-01T00:00:00Z"],
        ["user_9103", "SYNTHETIC Bo", "bo@example.test", "+1 555 0103", email_hash("bo@example.test"), "2026-01-01T00:00:00Z"],
        ["user_9104", "SYNTHETIC Cy", "cy@example.test", "+1 555 0104", email_hash("cy@example.test"), "2026-01-01T00:00:00Z"],
    ])
    write_csv(root / "user_audit_log.csv", ["audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"], [
        ["audit_s1", "user_9101", "2026-05-01T00:00:00Z", "email", "ada.old@example.test", "ada.new@example.test"],
    ])
    shutil.copy(DATASET_DIR / "deletion_rules.csv", root / "deletion_rules.csv")
    write_csv(root / "system_activity.csv",
              ["activity_id", "event_time", "event_type", "source_system", "target_system", "resource_id", "reported_result", "details"], [
        ["SYNTHETIC_act_1", "2026-09-01T00:00:00Z", "DELETE_RECORD", "deletion_worker", "account_service", "account_1", "SUCCESS", "SYNTHETIC deletion claim"],
        ["SYNTHETIC_act_2", "2026-09-02T00:00:00Z", "LINK_RESOURCE_IDENTITY", "data_catalog", "marketing_platform", "marketing_7", "SUCCESS",
         "Data catalog linked marketing_7 (marketing_7) to ada.new@example.test and user_9101."],
        ["SYNTHETIC_act_3", "2026-09-02T00:00:00Z", "LINK_RESOURCE_IDENTITY", "data_catalog", "export_service", "export_4", "SUCCESS",
         "Data catalog linked export_4 (export_4) to cy@example.test and user_9104."],
    ])
    evidence = root / "evidence" / "records"
    evidence.mkdir(parents=True)
    (evidence / "bundle.json").write_text(json.dumps({"exported_at": "2026-09-01T00:00:00Z", "snapshots": [
        {"captured_at": "2026-08-01T00:00:00Z", "data_store": "account_service", "collection": "customer_profiles",
         "records": [{"resource_id": "account_1", "customer_reference": "user_9101", "email": "ada.old@example.test"}]},
    ]}), encoding="utf-8")
    (evidence / "export.json").write_text(json.dumps({"exported_at": "2026-09-10T00:00:00Z", "service_exports": {
        "account_service": {"profile_lookups": [
            {"resource_id": "account_1", "status_code": 404, "profile": None, "observed_at": "2026-09-10T00:00:00Z"}]},
        "analytics_platform": {"profiles": [
            {"resource_id": "analytics_1", "email": "ADA.NEW@EXAMPLE.TEST", "observed_at": "2026-09-10T00:00:00Z"}]},
        "search_service": {"index_queries": [
            {"resource_id": "search_9", "query": "ada.newer@example.test", "observed_at": "2026-09-10T00:00:00Z",
             "hits": {"total": 1, "documents": [{"resource_id": "search_9", "email": "ada.newer@example.test"}]}}]},
        "security_service": {"audit_records": [
            {"resource_id": "audit_record_1", "subject_reference": "user_9101", "retention_until": "2033-01-01T00:00:00Z",
             "observed_at": "2026-09-10T00:00:00Z"},
            {"resource_id": "audit_record_3", "subject_reference": "user_9103", "retention_until": "2033-01-01T00:00:00Z",
             "observed_at": "2026-09-10T00:00:00Z"}]},
        "integration_service": {"connectors": [
            {"resource_id": "data_flow_1", "customer_reference": "user_9101", "enabled": True, "observed_at": "2026-09-10T00:00:00Z"}]},
        "backup_service": {"restore_entries": [
            {"resource_id": "backup_1", "customer_reference": "user_9101", "restore_allowed": False, "observed_at": "2026-09-10T00:00:00Z"}]},
        "marketing_platform": {"profiles": [
            {"resource_id": "marketing_7", "email": "someone.else@example.test", "observed_at": "2026-09-10T00:00:00Z"}]},
    }}), encoding="utf-8")
    # Ledger rows with explicit record types; like the real evidence, they teach prefix -> category.
    unrelated = [("analytics_platform", "analytics_8", "ANALYTICS_PROFILE", "ANONYMIZED"),
                 ("integration_service", "data_flow_8", "DATA_FLOW", "DISABLED"),
                 ("backup_service", "backup_8", "BACKUP_ARCHIVE", "BLOCKED"),
                 ("security_service", "audit_record_8", "SECURITY_AUDIT_LOG", "FOUND")]
    (evidence / "ledger.json").write_text(json.dumps({"exported_at": "2026-09-12T00:00:00Z", "records": [
        {"recorded_at": "2026-09-12T00:00:00Z", "system": "account_service", "resource_id": "account_3",
         "record_type": "USER_PROFILE", "result": "NOT_FOUND", "subject_reference": "bo@example.test"},
    ] + [{"recorded_at": "2026-09-12T00:00:00Z", "system": s, "resource_id": r, "record_type": t, "result": res,
          "subject_reference": "no direct identifier"} for s, r, t, res in unrelated]}), encoding="utf-8")
    (evidence / "later.json").write_text(json.dumps({"exported_at": "2026-10-01T00:00:00Z", "records": [
        {"resource_id": "search_1", "email": "ada.new@example.test"}]}), encoding="utf-8")
    (evidence / "untimed.json").write_text(json.dumps({"records": [{"resource_id": "search_2", "email": "ada.new@example.test"}]}),
                                           encoding="utf-8")
    (evidence / "old.json").write_text(json.dumps({"exported_at": "2026-07-01T00:00:00Z", "records": [
        {"resource_id": "search_7", "email": "ada.old@example.test"},
        {"resource_id": "audit_record_9", "subject_reference": "user_9103", "retention_until": "2033-01-01T00:00:00Z"}]}),
        encoding="utf-8")
    # user_9101 made an earlier request whose evidence window holds old.json's capture time
    write_csv(root / "deletion_requests.csv",
              ["case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at", "request_text"],
              [["SYNTHETIC_c0", "ada.old@example.test", "2026-06-25T00:00:00Z", "COMPLETE", "2026-06-26T00:00:00Z",
                "2026-07-02T00:00:00Z", DELETE_TEXT]])
    write_csv(root / "evidence_files.csv", ["file_id", "file_path", "origin", "captured_at"], [
        ["SYNTHETIC_bundle", "evidence/records/bundle.json", "SYNTHETIC", "2026-09-01T00:00:00Z"],
        ["SYNTHETIC_export", "evidence/records/export.json", "SYNTHETIC", "2026-09-10T00:00:00Z"],
        ["SYNTHETIC_ledger", "evidence/records/ledger.json", "SYNTHETIC", "2026-09-12T00:00:00Z"],
        ["SYNTHETIC_later", "evidence/records/later.json", "SYNTHETIC", "2026-10-01T00:00:00Z"],
        ["SYNTHETIC_untimed", "evidence/records/untimed.json", "SYNTHETIC", ""],
        ["SYNTHETIC_old", "evidence/records/old.json", "SYNTHETIC", "2026-07-01T00:00:00Z"],
    ])
    # the request file is also read by the engine; the test requests themselves are built in memory


def request(case_id, email, text=DELETE_TEXT, audit_at=AUDIT_AT):
    return {"case_id": case_id, "email": email, "requested_at": "2026-08-30T00:00:00Z", "reported_status": "COMPLETE",
            "reported_at": "2026-09-01T00:00:00Z", "audit_at": audit_at, "request_text": text}


def by_resource(result):
    return {r["resource_id"]: r for r in result["resources"]}


class SyntheticEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="SYNTHETIC_audit_"))
        build_synthetic_dataset(cls.root)
        cls.engine = AuditEngine.build(cls.root, ocr_cache=None)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_historical_case_variant_email_links_every_record_of_the_person(self):
        result = self.engine.audit(request("SYNTHETIC_c1", "  ADA.OLD@EXAMPLE.TEST "))
        self.assertEqual(result["user_id"], "user_9101")
        self.assertEqual(result["identifiers_used"]["emails"], ["ada.new@example.test", "ada.old@example.test"])
        self.assertEqual(set(by_resource(result)), {"account_1", "analytics_1", "audit_record_1", "data_flow_1", "backup_1"})

    def test_status_and_findings_follow_the_rules(self):
        result = self.engine.audit(request("SYNTHETIC_c1", "ada.old@example.test"))
        self.assertEqual((result["status"], result["confidence"]), ("INCOMPLETE", "high"))
        res = by_resource(result)
        self.assertEqual((res["account_1"]["outcome"], res["account_1"]["state_at_audit"], res["account_1"]["rule_id"]),
                         ("COMPLIANT", "ABSENT", "rule_01"))
        self.assertEqual((res["analytics_1"]["finding"], res["analytics_1"]["rule_id"], res["analytics_1"]["required_action"]),
                         ("EXPOSED_PII", "rule_07", "ANONYMIZE"))
        self.assertEqual((res["audit_record_1"]["outcome"], res["audit_record_1"]["required_action"]), ("COMPLIANT", "RETAIN"))
        self.assertEqual((res["data_flow_1"]["finding"], res["data_flow_1"]["required_action"]), ("ACTIVE_DATA_FLOW", "DISABLE_FLOW"))
        self.assertEqual((res["backup_1"]["outcome"], res["backup_1"]["state_at_audit"], res["backup_1"]["rule_id"]),
                         ("COMPLIANT", "BLOCKED", "rule_14"))

    def test_provenance_names_the_deciding_evidence(self):
        res = by_resource(self.engine.audit(request("SYNTHETIC_c1", "ada.old@example.test")))
        deciding = res["account_1"]["deciding_evidence"]
        self.assertEqual([(d["evidence_id"], d["state_basis"]) for d in deciding], [("SYNTHETIC_export", "status_code=404")])
        self.assertTrue(all(e["within_audit_cutoff"] for e in res["account_1"]["evidence"]))
        self.assertIn("SYNTHETIC_act_1", {e["evidence_id"] for e in res["account_1"]["evidence"] if e["claim"]})

    def test_decoy_later_and_untimed_evidence_are_not_used(self):
        result = self.engine.audit(request("SYNTHETIC_c1", "ada.old@example.test"))
        linked = set(by_resource(result))
        self.assertNotIn("search_9", linked)  # ada.newer@ is not ada.new@
        self.assertNotIn("search_1", linked)  # captured after audit_at
        self.assertNotIn("search_2", linked)  # no usable time
        later = self.engine.audit(request("SYNTHETIC_c1b", "ada.old@example.test", audit_at="2026-10-02T00:00:00Z"))
        self.assertIn("search_1", by_resource(later))

    def test_evidence_of_an_earlier_request_is_set_aside(self):
        # search_7 is only observed on 2026-07-01, inside the window of user_9101's earlier request SYNTHETIC_c0
        result = self.engine.audit(request("SYNTHETIC_c1", "ada.old@example.test"))
        self.assertNotIn("search_7", by_resource(result))
        self.assertEqual([(p["resource_id"], p["earlier_request"]) for p in result["prior_resources"]], [("search_7", "SYNTHETIC_c0")])

    def test_old_evidence_without_an_earlier_request_still_counts(self):
        # audit_record_9 (user_9103) is also observed on 2026-07-01, but user_9103 made no earlier request
        result = self.engine.audit(request("SYNTHETIC_c3", "bo@example.test"))
        self.assertEqual(by_resource(result)["audit_record_9"]["outcome"], "COMPLIANT")
        self.assertEqual(result["prior_resources"], [])

    def test_claim_link_contradicted_by_content_is_excluded(self):
        result = self.engine.audit(request("SYNTHETIC_c1", "ada.old@example.test"))
        self.assertNotIn("marketing_7", by_resource(result))
        self.assertEqual([x["resource_id"] for x in result["excluded_resources"]], ["marketing_7"])

    def test_non_deletion_request_is_not_applicable(self):
        result = self.engine.audit(request("SYNTHETIC_c2", "ada.new@example.test", "Please keep the account as it is."))
        self.assertEqual((result["status"], result["resources"]), ("NOT_APPLICABLE", []))

    def test_fully_compliant_person_is_verified_complete(self):
        result = self.engine.audit(request("SYNTHETIC_c3", "bo@example.test"))
        self.assertEqual((result["status"], result["confidence"]), ("COMPLETE", "verified"))

    def test_ambiguous_request_is_audited_and_flagged(self):
        result = self.engine.audit(request("SYNTHETIC_c4", "bo@example.test", "SYNTHETIC: please look at my data."))
        self.assertEqual(result["status"], "COMPLETE")
        self.assertTrue(any("audited as a deletion request" in a for a in result["ambiguities"]))

    def test_claim_only_resource_without_state_does_not_make_a_case_incomplete(self):
        result = self.engine.audit(request("SYNTHETIC_c5", "cy@example.test"))
        self.assertEqual((result["status"], result["confidence"]), ("COMPLETE", "unverified"))
        self.assertEqual(by_resource(result)["export_4"]["outcome"], "UNKNOWN")
        self.assertTrue(any("only by a workflow claim" in a for a in result["ambiguities"]))

    def test_unknown_email_is_unresolved_not_guessed(self):
        result = self.engine.audit(request("SYNTHETIC_c6", "nobody@example.test"))
        self.assertIsNone(result["status"])
        self.assertIsNone(result["user_id"])


class RealSampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = AuditEngine.build()
        cls.samples = {r["case_id"]: r for r in read_csv("sample_deletion_requests.csv")}
        cls.expected = {r["case_id"]: r for r in read_csv("sample_output.csv")}
        cls.main = {r["case_id"]: r for r in read_csv("deletion_requests.csv")}

    def test_sample_statuses_agree_with_the_published_answers(self):
        wrong = [c for c in self.samples if self.engine.audit(self.samples[c])["status"] != self.expected[c]["deletion_status"]]
        self.assertEqual(wrong, [])

    def test_decoys_are_not_linked(self):  # case_06: evidence_57 holds jonas.berglund and joana.berg decoys
        res = by_resource(self.engine.audit(self.samples["case_06"]))
        self.assertFalse({"search_8814", "search_4203"} & set(res))
        self.assertEqual(res["search_001"]["finding"], "REMAINING_RECORD")
        self.assertIn("evidence_57", {d["evidence_id"] for d in res["search_001"]["deciding_evidence"]})

    def test_historical_hash_and_marketing_rule(self):  # case_12: evidence_01 line 92 holds the old email's hash
        res = by_resource(self.engine.audit(self.samples["case_12"]))["analytics_9914"]
        self.assertEqual((res["category"], res["rule_id"], res["finding"]), ("MARKETING_PROFILE", "rule_10", "REMAINING_RECORD"))
        self.assertTrue(any(i.startswith("email_hash=") for e in res["evidence"] for i in e["matched_identifiers"]))

    def test_request_email_can_be_historical(self):  # case_29 uses an email the user later changed
        result = self.engine.audit(self.samples["case_29"])
        self.assertIn(self.samples["case_29"]["email"].lower(), result["identifiers_used"]["emails"])
        self.assertGreater(len(result["identifiers_used"]["emails"]), 1)

    def test_main_non_deletion_and_contradicted_claim(self):
        self.assertEqual(self.engine.audit(self.main["case_539"])["status"], "NOT_APPLICABLE")
        excluded = self.engine.audit(self.main["case_63"])["excluded_resources"]
        self.assertEqual([x["resource_id"] for x in excluded], ["marketing_001"])
        self.assertEqual(excluded[0]["contradicting_evidence"], ["evidence_60"])


if __name__ == "__main__":
    unittest.main()
