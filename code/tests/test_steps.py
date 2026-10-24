"""Tests for step generation and the output contract. Synthetic audit results use SYNTHETIC IDs;
real-data tests read the expected answers from dataset/sample_output.csv at test time."""
import csv
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghostaudit.audit import AuditEngine  # noqa: E402
from ghostaudit.data import parse_time, read_csv  # noqa: E402
from ghostaudit.output import case_sort_key, citable_evidence_times, validate_output, write_output  # noqa: E402
from ghostaudit.steps import build_steps, cited_evidence  # noqa: E402


def resource(system, rid, outcome, action, finding=None, evidence=None, condition="", link="evidence_content"):
    deciding = [{"evidence_id": e, "observed_at": t} for e, t in (evidence or [])]
    return {"system_id": system, "resource_id": rid, "outcome": outcome, "required_action": action, "finding": finding,
            "rule_condition": condition, "deciding_evidence": deciding, "deciding_scope": None, "link_strength": link}


INCOMPLETE = {"status": "INCOMPLETE", "resources": [
    resource("support_platform", "support_2", "VIOLATION", "REDACT", "EXPOSED_PII", [("SYNTHETIC_e3", "2026-09-10T00:00:00+00:00")]),
    resource("account_service", "account_1", "COMPLIANT", "DELETE", evidence=[("SYNTHETIC_e1", "2026-09-10T00:00:00+00:00")]),
    resource("integration_service", "data_flow_1", "VIOLATION", "DISABLE_FLOW", "ACTIVE_DATA_FLOW", [("SYNTHETIC_e2", "2026-09-10T00:00:00+00:00")]),
    resource("analytics_platform", "analytics_1", "VIOLATION", "ANONYMIZE", "EXPOSED_PII", [("SYNTHETIC_e2", "2026-09-10T00:00:00+00:00")]),
    resource("backup_service", "backup_1", "VIOLATION", "RETAIN", "RESTORE_RISK", [("SYNTHETIC_e2", "2026-09-10T00:00:00+00:00")],
             condition="Restore access can be blocked"),
    resource("search_service", "search_1", "VIOLATION", "DELETE", "REMAINING_RECORD", [("SYNTHETIC_e4", "2026-09-10T00:00:00+00:00")]),
    resource("security_service", "audit_record_1", "COMPLIANT", "RETAIN", evidence=[("SYNTHETIC_e2", "2026-09-10T00:00:00+00:00")]),
    resource("account_service", "account_2", "UNKNOWN", "DELETE"),
    resource("identity_service", "tombstone_1", "UNKNOWN", "REDACT", link="claim_only"),
    resource("object_storage", "object_1", "UNKNOWN", None),
]}


def ops(steps):
    return [(s["phase"], s["operation"], s["resource_id"], s["evidence_id"]) for s in steps]


class StepGenerationTests(unittest.TestCase):
    def test_incomplete_plan_order_and_evidence(self):
        steps, notes = build_steps(INCOMPLETE)
        self.assertEqual([s["step_no"] for s in steps], list(range(1, len(steps) + 1)))
        self.assertEqual(ops(steps), [
            ("FINDING", "REPORT_REMAINING_RECORD", "search_1", "SYNTHETIC_e4"),
            ("FINDING", "REPORT_EXPOSED_PII", "analytics_1", "SYNTHETIC_e2"),
            ("FINDING", "REPORT_EXPOSED_PII", "support_2", "SYNTHETIC_e3"),
            ("FINDING", "REPORT_ACTIVE_DATA_FLOW", "data_flow_1", "SYNTHETIC_e2"),
            ("FINDING", "REPORT_RESTORE_RISK", "backup_1", "SYNTHETIC_e2"),
            ("REMEDIATION", "DISABLE_DATA_FLOW", "data_flow_1", ""),
            ("REMEDIATION", "BLOCK_RESTORE", "backup_1", ""),
            ("REMEDIATION", "DELETE_RECORD", "search_1", ""),
            ("REMEDIATION", "REDACT_RECORD", "support_2", ""),
            ("REMEDIATION", "ANONYMIZE_RECORD", "analytics_1", ""),
            ("VERIFICATION", "VERIFY_RECORD_ABSENT", "account_1", ""),
            ("VERIFICATION", "VERIFY_RECORD_ABSENT", "account_2", ""),
            ("VERIFICATION", "VERIFY_RECORD_ABSENT", "search_1", ""),
            ("VERIFICATION", "VERIFY_RECORD_PRESENT", "audit_record_1", ""),
            ("VERIFICATION", "VERIFY_RECORD_REDACTED", "support_2", ""),
            ("VERIFICATION", "VERIFY_RECORD_ANONYMIZED", "analytics_1", ""),
            ("VERIFICATION", "VERIFY_DATA_FLOW_DISABLED", "data_flow_1", ""),
            ("VERIFICATION", "VERIFY_RESTORE_BLOCKED", "backup_1", ""),
        ])
        self.assertTrue(any("tombstone_1" in n and "claim" in n for n in notes))
        self.assertTrue(any("object_1" in n for n in notes))

    def test_complete_cites_proof_and_skips_what_it_cannot_prove(self):
        audit = {"status": "COMPLETE", "resources": [
            resource("account_service", "account_1", "COMPLIANT", "DELETE", evidence=[("SYNTHETIC_old", "2026-09-01T00:00:00+00:00"),
                                                                                    ("SYNTHETIC_new", "2026-09-10T00:00:00+00:00")]),
            resource("analytics_platform", "analytics_1", "UNKNOWN", "ANONYMIZE"),
        ]}
        steps, notes = build_steps(audit)
        self.assertEqual(ops(steps), [("VERIFICATION", "VERIFY_RECORD_ABSENT", "account_1", "SYNTHETIC_new")])
        self.assertTrue(any("analytics_1" in n for n in notes))

    def test_not_applicable_and_unresolved_have_no_steps(self):
        self.assertEqual(build_steps({"status": "NOT_APPLICABLE", "resources": INCOMPLETE["resources"]}), ([], []))
        self.assertEqual(build_steps({"status": None, "resources": []})[0], [])

    def test_scope_is_cited_when_absence_comes_from_a_later_snapshot(self):
        r = resource("account_service", "account_1", "COMPLIANT", "DELETE")
        r["deciding_scope"] = {"evidence_id": "SYNTHETIC_scope"}
        self.assertEqual(cited_evidence(r), "SYNTHETIC_scope")


class OutputContractTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="SYNTHETIC_output_"))
        self.requests = [{"case_id": c, "audit_at": "2026-09-15T12:00:00Z"} for c in ("case_9", "case_10", "case_100")]
        self.times = {"SYNTHETIC_e1": parse_time("2026-09-10T00:00:00Z"), "SYNTHETIC_late": parse_time("2026-09-20T00:00:00Z")}

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, rows, name="out.csv"):
        path = self.dir / name
        write_output(rows, path)
        return path

    def good_rows(self):
        incomplete, _ = build_steps({"status": "INCOMPLETE", "resources": [
            resource("search_service", "search_1", "VIOLATION", "DELETE", "REMAINING_RECORD", [("SYNTHETIC_e1", "2026-09-10T00:00:00+00:00")])]})
        complete, _ = build_steps({"status": "COMPLETE", "resources": [
            resource("account_service", "account_1", "COMPLIANT", "DELETE", evidence=[("SYNTHETIC_e1", "2026-09-10T00:00:00+00:00")])]})
        return [("case_100", "NOT_APPLICABLE", []), ("case_9", "INCOMPLETE", incomplete), ("case_10", "COMPLETE", complete)]

    def test_valid_output_round_trips_in_numeric_case_order(self):
        path = self.write(self.good_rows())
        errors, warnings = validate_output(path, self.requests, self.times)
        self.assertEqual((errors, warnings), ([], []))
        with open(path, newline="", encoding="utf-8") as handle:
            self.assertEqual([r["case_id"] for r in csv.DictReader(handle)], ["case_9", "case_10", "case_100"])
        self.assertLess(case_sort_key("case_33"), case_sort_key("case_100"))

    def test_contract_violations_are_reported(self):
        cases = {
            "captured after audit_at": ("case_10", "COMPLETE", [{"step_no": 1, "phase": "VERIFICATION", "system_id": "a", "resource_id": "b",
                                                                  "operation": "VERIFY_RECORD_ABSENT", "state": "ABSENT", "evidence_id": "SYNTHETIC_late"}]),
            "unknown evidence_id": ("case_10", "COMPLETE", [{"step_no": 1, "phase": "VERIFICATION", "system_id": "a", "resource_id": "b",
                                                             "operation": "VERIFY_RECORD_ABSENT", "state": "ABSENT", "evidence_id": "SYNTHETIC_nope"}]),
            "must have state": ("case_10", "COMPLETE", [{"step_no": 1, "phase": "VERIFICATION", "system_id": "a", "resource_id": "b",
                                                         "operation": "VERIFY_RECORD_ABSENT", "state": "PRESENT", "evidence_id": "SYNTHETIC_e1"}]),
            "empty evidence_id": ("case_10", "INCOMPLETE", [
                {"step_no": 1, "phase": "FINDING", "system_id": "a", "resource_id": "b", "operation": "REPORT_REMAINING_RECORD", "state": "PRESENT", "evidence_id": "SYNTHETIC_e1"},
                {"step_no": 2, "phase": "REMEDIATION", "system_id": "a", "resource_id": "b", "operation": "DELETE_RECORD", "state": "ABSENT", "evidence_id": "SYNTHETIC_e1"}]),
            "not ordered": ("case_10", "INCOMPLETE", [
                {"step_no": 1, "phase": "REMEDIATION", "system_id": "a", "resource_id": "b", "operation": "DELETE_RECORD", "state": "ABSENT", "evidence_id": ""},
                {"step_no": 2, "phase": "FINDING", "system_id": "a", "resource_id": "b", "operation": "REPORT_REMAINING_RECORD", "state": "PRESENT", "evidence_id": "SYNTHETIC_e1"}]),
            "has step_no": ("case_10", "COMPLETE", [{"step_no": 3, "phase": "VERIFICATION", "system_id": "a", "resource_id": "b",
                                                     "operation": "VERIFY_RECORD_ABSENT", "state": "ABSENT", "evidence_id": "SYNTHETIC_e1"}]),
            "must have steps_json []": ("case_10", "NOT_APPLICABLE", [{"step_no": 1, "phase": "VERIFICATION", "system_id": "a", "resource_id": "b",
                                                                     "operation": "VERIFY_RECORD_ABSENT", "state": "ABSENT", "evidence_id": "SYNTHETIC_e1"}]),
            "without a finding": ("case_10", "INCOMPLETE", []),
        }
        for expected, bad in cases.items():
            with self.subTest(problem=expected):
                rows = [r for r in self.good_rows() if r[0] != bad[0]] + [bad]
                errors, _ = validate_output(self.write(rows, f"{len(expected)}.csv"), self.requests, self.times)
                self.assertTrue(any(expected in e for e in errors), errors)

    def test_missing_rows_and_bad_header(self):
        errors, _ = validate_output(self.write(self.good_rows()[:2]), self.requests, self.times)
        self.assertTrue(any("data rows" in e for e in errors))
        path = self.dir / "header.csv"
        path.write_text("case,status,steps\n", encoding="utf-8")
        self.assertTrue(validate_output(path, self.requests, self.times)[0][0].startswith("header"))


class MissingOcrTests(unittest.TestCase):
    """main.py must not silently write a different output when Tesseract is unavailable."""

    def test_run_stops_without_tesseract_unless_allowed(self):
        import os
        import subprocess
        scratch = Path(tempfile.mkdtemp(prefix="SYNTHETIC_no_ocr_"))
        try:
            bin_dir = scratch / "bin"
            bin_dir.mkdir()
            (bin_dir / "python3").symlink_to(Path(sys.executable).resolve())
            env = {**os.environ, "PATH": str(bin_dir)}
            main = str(Path(__file__).resolve().parents[1] / "main.py")
            out = scratch / "out.csv"
            strict = subprocess.run([str(bin_dir / "python3"), main, "--output", str(out), "--no-audit-trail"],
                                    capture_output=True, text=True, env=env)
            self.assertEqual(strict.returncode, 3, strict.stderr)
            self.assertIn("tesseract OCR is not installed", strict.stderr)
            self.assertFalse(out.exists())
            allowed = subprocess.run([str(bin_dir / "python3"), main, "--output", str(out), "--no-audit-trail",
                                      "--allow-parse-failures"], capture_output=True, text=True, env=env)
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertTrue(out.exists())
        finally:
            shutil.rmtree(scratch, ignore_errors=True)


class IndependentCheckerTests(unittest.TestCase):
    """code/evaluation/check_output.py must reject a corrupted output (it shares no code with output.py)."""

    def test_checker_flags_a_corrupted_row(self):
        import subprocess
        repo = Path(__file__).resolve().parents[2]
        if not (repo / "output.csv").exists():
            self.skipTest("output.csv has not been generated yet")
        scratch = Path(tempfile.mkdtemp(prefix="SYNTHETIC_checker_"))
        try:
            (scratch / "dataset").symlink_to(repo / "dataset")
            lines = (repo / "output.csv").read_text(encoding="utf-8").splitlines(keepends=True)
            first = next(i for i, line in enumerate(lines) if ",INCOMPLETE," in line)
            lines[first] = lines[first].replace('""evidence_id"":""""', '""evidence_id"":""SYNTHETIC_bad""', 1)
            (scratch / "output.csv").write_text("".join(lines), encoding="utf-8")
            checker = str(repo / "code" / "evaluation" / "check_output.py")
            bad = subprocess.run([sys.executable, checker, str(scratch)], capture_output=True, text=True)
            self.assertEqual(bad.returncode, 1, bad.stdout)
            self.assertIn("future step with evidence", bad.stdout)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)


class RealSampleStepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        engine = AuditEngine.build()
        cls.expected = {r["case_id"]: json.loads(r["steps_json"]) for r in read_csv("sample_output.csv")}
        cls.predicted = {r["case_id"]: build_steps(engine.audit(r))[0] for r in read_csv("sample_deletion_requests.csv")}

    def test_no_sample_step_is_invented(self):
        key = lambda s: tuple(s[k] for k in ("phase", "system_id", "resource_id", "operation", "state", "evidence_id"))
        extra = {c: [key(s) for s in steps if key(s) not in {key(e) for e in self.expected[c]}] for c, steps in self.predicted.items()}
        self.assertEqual({c: e for c, e in extra.items() if e}, {})

    def test_fully_linked_cases_reproduce_the_published_steps(self):
        # every resource of these answers is linked by identifiers visible at audit_at (artifacts/sample_evidence_validation.json)
        for case_id in ("case_05", "case_06", "case_11", "case_13", "case_14", "case_15", "case_16", "case_19"):
            with self.subTest(case=case_id):
                self.assertEqual(self.predicted[case_id], self.expected[case_id])

    def test_prior_resource_of_an_earlier_request_is_left_out(self):
        # case_17: account_6523 is only evidenced by a 2026-03-10 lookup, before this request (2026-08-19)
        self.assertNotIn("account_6523", {s["resource_id"] for s in self.predicted["case_17"]})

    def test_citable_times_cover_all_id_kinds(self):
        times = citable_evidence_times({name: read_csv(f"{name}.csv") for name in ("evidence_files", "system_activity", "user_audit_log")})
        self.assertTrue({"evidence_54", "activity_4000", "audit_01"} <= set(times))


if __name__ == "__main__":
    unittest.main()
