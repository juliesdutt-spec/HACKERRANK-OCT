"""Tests for the evidence index. Run from the repo root: python3 -m unittest discover -s code/tests

Integration tests read the real dataset; their expected values are copied from the cited source
lines and rows (file and line noted next to each). Synthetic tests build throwaway datasets in a
temporary directory; every synthetic value is prefixed SYNTHETIC or uses example.test addresses,
and none of it is ever written under dataset/.
"""
import csv
import shutil
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghostaudit.data import DATASET_DIR, parse_time, read_csv  # noqa: E402
from ghostaudit.evidence import EvidenceIndex, identifier_owners  # noqa: E402
from ghostaudit.evidence.documents import tesseract_version  # noqa: E402
from ghostaudit.identity import IdentityIndex, email_hash  # noqa: E402

_INDEX = None


def real_index() -> EvidenceIndex:
    global _INDEX
    if _INDEX is None:
        _INDEX = EvidenceIndex.build(ocr=tesseract_version() is not None)
    return _INDEX


def find(index, evidence_id, resource_id=None, location=None):
    return [
        r for r in index.records
        if r.evidence_id == evidence_id
        and (resource_id is None or r.resource_id == resource_id)
        and (location is None or r.location.startswith(location))
    ]


def one(index, evidence_id, resource_id=None, location=None):
    found = find(index, evidence_id, resource_id, location)
    assert len(found) == 1, f"expected one record for {evidence_id} {resource_id} {location}, got {len(found)}"
    return found[0]


T = parse_time


class ManifestCoverageTests(unittest.TestCase):
    def test_every_manifest_file_has_exactly_one_report(self):
        manifest = [row["file_id"] for row in read_csv("evidence_files.csv")]
        self.assertEqual(sorted(r.file_id for r in real_index().reports), sorted(manifest))

    def test_every_unit_is_used_or_explained(self):
        for report in real_index().reports:
            with self.subTest(file=report.file_id):
                self.assertEqual(report.units_total, report.units_used + sum(report.units_accounted.values()))

    def test_no_file_failed_and_partial_files_say_why(self):
        for report in real_index().reports:
            with self.subTest(file=report.file_id):
                if not tesseract_version() and report.file_format == "png":
                    continue
                self.assertNotEqual(report.status, "failed", report.errors)
                if report.status == "partial":
                    self.assertTrue(report.warnings)

    def test_provenance_matches_the_manifest(self):
        manifest = {row["file_id"]: row for row in read_csv("evidence_files.csv")}
        activity_ids = {row["activity_id"] for row in read_csv("system_activity.csv")}
        for record in real_index().records:
            self.assertTrue(record.location)
            if record.source == "file":
                row = manifest[record.evidence_id]
                self.assertEqual(record.file_path, row["file_path"])
                self.assertEqual(record.file_captured_at, T(row["captured_at"]))
            else:
                self.assertIn(record.evidence_id, activity_ids)
                self.assertTrue(record.is_claim)
                self.assertIsNone(record.state)

    def test_production_index_holds_no_synthetic_data(self):
        self.assertFalse([r for r in real_index().records if "SYNTHETIC" in r.evidence_id or "example.test" in str(r.identifiers)])


class SqlTests(unittest.TestCase):
    def test_failover_replica_row_missing_from_primary(self):
        # customer_accounts_failover_replica_20260902.sql: account_005 is only in the replica table
        index = real_index()
        record = one(index, "evidence_54", "account_005")
        self.assertEqual(record.location, "table=customer_profiles_failover_replica row=1")
        self.assertEqual((record.system_id, record.state, record.observed_at), ("account_service", "PRESENT", T("2026-09-02T23:00:00Z")))
        self.assertTrue({("email", "leila.haddad@yahoo.com"), ("user_ref", "user_05"), ("phone", "+212648271593")} <= record.identifiers)
        self.assertEqual(record.fields["_store_role"], "failover_replica")
        primary = [s for s in index.scopes if s.evidence_id == "evidence_54" and s.store_role == "primary"]
        self.assertEqual(len(primary), 1)
        self.assertEqual(primary[0].resource_ids, {"account_8741", "account_9116", "account_7204"})

    def test_inventory_scope_with_no_rows(self):
        # customer_data_inventory_20260905.sql: snapshot_scope lists only account_service, every table empty
        index = real_index()
        self.assertEqual(find(index, "evidence_90"), [])
        profiles = [s for s in index.scopes if s.evidence_id == "evidence_90" and s.collection == "customer_profiles"]
        self.assertEqual(len(profiles), 1)
        self.assertEqual((profiles[0].system_id, profiles[0].captured_at, profiles[0].resource_ids, profiles[0].coverage_confirmed),
                         ("account_service", T("2026-09-05T07:18:01Z"), frozenset(), True))
        unconfirmed = [s for s in index.scopes if s.evidence_id == "evidence_90" and not s.coverage_confirmed]
        self.assertEqual(len(unconfirmed), 7)

    def test_search_document_points_at_its_source_account(self):
        # customer_search_primary_20260910.sql row 1
        record = one(real_index(), "evidence_57", "search_001")
        self.assertIn(("email", "jonas.berg@outlook.com"), record.identifiers)
        self.assertEqual(record.related_resources, {"account_006"})


class JsonTests(unittest.TestCase):
    def test_service_export_lookup_404_is_absent(self):
        # service_resource_export_20260915_114455.json profile_lookups
        record = one(real_index(), "evidence_92", "account_3136")
        self.assertEqual((record.state, record.state_basis, record.observed_at), ("ABSENT", "status_code=404", T("2026-09-15T09:31:40Z")))

    def test_service_export_connector_disabled(self):
        record = one(real_index(), "evidence_92", "data_flow_2295")
        self.assertEqual((record.state, record.system_id), ("DISABLED", "integration_service"))
        self.assertIn(("user_ref", "user_27"), record.identifiers)

    def test_bundle_ledger_section_uses_record_time_and_system(self):
        # customer_data_snapshot_bundle_20260223.json, data_store backup_restore_service
        record = one(real_index(), "evidence_45", "account_1433")
        self.assertEqual((record.system_id, record.system_basis, record.state), ("account_service", "explicit", "ABSENT"))
        self.assertEqual((record.observed_at, record.time_basis), (T("2026-02-18T09:00:00Z"), "record:recorded_at"))
        self.assertIn(("email", "maja.lund@outlook.com"), record.identifiers)

    def test_flat_listing_with_and_without_identifiers(self):
        # customer_analytics_report_20260907.json
        named = one(real_index(), "evidence_56", "analytics_824")
        self.assertTrue({("user_ref", "user_824"), ("email", "dahl.freja@icloud.com")} <= named.identifiers)
        anonymous = one(real_index(), "evidence_56", "analytics_901")
        self.assertEqual({k for k, _ in anonymous.identifiers}, set())
        self.assertEqual(anonymous.observed_at, T("2026-09-07T09:45:00Z"))


class LogTests(unittest.TestCase):
    def assertLine(self, evidence_id, location, resource_id, state, when, identifier):
        record = one(real_index(), evidence_id, resource_id, location)
        self.assertEqual((record.state, record.observed_at), (state, T(when)))
        self.assertIn(identifier, record.identifiers)
        return record

    def test_keyvalue_lookup_trace(self):  # resource_lookup_trace_20260926_103856.log lines 2-3
        record = self.assertLine("evidence_94", "line 3 ", "account_2064", "ABSENT", "2026-09-26T07:49:50Z",
                                 ("email", "charlotte.wright@outlook.com"))
        self.assertIn("lookup line 2", record.location)
        self.assertLine("evidence_94", "line 5 ", "data_flow_4713", "DISABLED", "2026-09-26T07:51:26Z", ("user_ref", "user_49"))

    def test_syslog(self):  # syslog_archive_us_east_20260824.log lines 1, 3, 6
        self.assertLine("evidence_11", "line 3 ", "account_1785", "PRESENT", "2026-08-22T10:51:17Z", ("email", "nana.b9576@outlook.com"))
        self.assertLine("evidence_11", "line 6", "support_9309", "IDENTIFIABLE", "2026-08-22T10:51:33Z", ("email", "joao-santos@proton.me"))

    def test_json_lines(self):  # structured_service_events_eu_west_20260918.log lines 2, 4
        self.assertLine("evidence_06", "line 4 ", "account_6633", "PRESENT", "2026-09-10T08:56:20Z", ("email", "collins.grace@icloud.com"))

    def test_bracketed(self):  # application_trace_ap_northeast_20260730.log lines 2, 4
        self.assertLine("evidence_16", "line 4 ", "account_7947", "ABSENT", "2026-07-28T05:51:21Z", ("email", "l.nascimento40@gmail.com"))

    def test_pipe_separated(self):  # operations_stream_eu_central_20260705.log line 2
        self.assertLine("evidence_21", "line 2", "audit_record_4413", "PRESENT", "2026-07-03T07:51:32Z", ("user_ref", "user_211"))

    def test_cloud_message(self):  # cloud_runtime_export_af_south_20260930.log line 2
        self.assertLine("evidence_53", "line 2", "account_66777", "PRESENT", "2026-09-30T11:51:03Z", ("email", "a.watanabe06@yahoo.com"))

    def test_record_lookup_line_names_its_system(self):  # notification_delivery_20260315.log line 1
        record = self.assertLine("evidence_41", "line 1", "account_022", "ABSENT", "2026-03-15T07:00:00Z", ("email", "zofia-novak@gmail.com"))
        self.assertEqual((record.system_id, record.system_basis, record.record_type), ("account_service", "explicit", "USER_PROFILE"))

    def test_trace_links_lookup_to_two_state_lines(self):  # support_replica.log lines 4-6
        self.assertLine("evidence_61", "line 5 ", "support_002", "PRESENT", "2026-09-17T22:00:05Z", ("email", "nadia.ionescu@yahoo.com"))
        record = self.assertLine("evidence_61", "line 6 ", "support_002", "PRESENT", "2026-09-17T22:00:06Z", ("email", "nadia.ionescu@yahoo.com"))
        self.assertEqual(record.state_basis, "marker_found=false")


class TextPdfPngTests(unittest.TestCase):
    def test_handover_rows(self):  # customer_account_handover_20261001.txt lines 7, 11, 20
        index = real_index()
        self.assertEqual(one(index, "evidence_03", "account_4248").state, "ABSENT")
        blocked = one(index, "evidence_03", "backup_706")
        self.assertEqual((blocked.state, blocked.observed_at), ("BLOCKED", T("2026-10-01T12:00:00Z")))
        self.assertIn(("user_ref", "user_39"), blocked.identifiers)
        self.assertIn(("email", "choi.jihoon94@gmail.com"), one(index, "evidence_03", "account_1810").identifiers)

    def test_pdf_block_keeps_resource_with_its_details(self):  # delivery_correction_attachment.pdf page 1
        records = [r for r in find(real_index(), "evidence_58", "support_001") if ("email", "priya.raman@gmail.com") in r.identifiers]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual((record.state, record.extraction, record.observed_at), ("MENTIONED", "pdf_text", T("2026-09-12T07:30:00Z")))
        self.assertIn(("phone", "+919876573142"), record.identifiers)

    def test_pdf_register_row_without_resource(self):  # legal_retention_register_20260529.pdf row 1
        rows = [r for r in find(real_index(), "evidence_27") if ("user_ref", "user_08") in r.identifiers]
        self.assertEqual([r.resource_id for r in rows], [None])

    @unittest.skipUnless(tesseract_version(), "tesseract OCR is not installed")
    def test_screenshot_header_line(self):  # account_admin_console_20260926.png header "user_11 · account_5762"
        records = [r for r in find(real_index(), "evidence_04", "account_5762") if ("user_ref", "user_11") in r.identifiers]
        self.assertTrue(records)
        self.assertEqual({(r.extraction, r.state, r.observed_at) for r in records}, {("ocr", "MENTIONED", T("2026-09-26T11:00:00Z"))})

    def test_activity_link_row_is_a_claim_with_identifiers(self):  # system_activity.csv activity_4000
        record = one(real_index(), "activity_4000")
        self.assertEqual((record.resource_id, record.system_id, record.state, record.is_claim),
                         ("account_5762", "account_service", None, True))
        self.assertTrue({("email", "haruka.s1838@zoho.com"), ("user_ref", "user_11")} <= record.identifiers)


class CutoffTests(unittest.TestCase):
    def test_capture_time_is_inclusive(self):
        record = one(real_index(), "evidence_54", "account_005")
        self.assertTrue(record.visible_at(T("2026-09-02T23:00:00Z")))
        self.assertFalse(record.visible_at(T("2026-09-02T22:59:59Z")))

    def test_old_section_in_a_later_file_is_not_visible(self):
        # customer_data_snapshot_bundle_20260917.json: section captured 2026-09-09, file exported 2026-09-17
        record = [r for r in find(real_index(), "evidence_02", "account_5684") if r.collection == "customer_profiles"][0]
        self.assertEqual(record.observed_at, T("2026-09-09T12:00:00Z"))
        self.assertFalse(record.visible_at(T("2026-09-15T12:00:00Z")))
        self.assertTrue(record.visible_at(T("2026-09-17T12:00:00Z")))

    def test_record_observed_after_its_export_waits_for_its_own_time(self):
        # connector_configurations_20260812.json, exported 2026-08-12: account_1417 is NOT_FOUND in
        # records[1] (recorded 2026-08-12) and FOUND in records[38] (recorded 2026-08-14)
        index = real_index()
        earlier = one(index, "evidence_13", "account_1417", "$.records[1]")
        later = one(index, "evidence_13", "account_1417", "$.records[38]")
        self.assertEqual((earlier.state, later.state), ("ABSENT", "PRESENT"))
        self.assertTrue(earlier.visible_at(T("2026-08-13T00:00:00Z")))
        self.assertFalse(later.visible_at(T("2026-08-13T00:00:00Z")))
        self.assertTrue(later.visible_at(T("2026-08-14T09:00:00Z")))
        report = next(r for r in index.reports if r.file_id == "evidence_13")
        self.assertEqual(report.status, "partial")

    def test_matching_ignores_evidence_after_audit_at(self):
        identity = IdentityIndex(read_csv("user_identities.csv"), read_csv("user_audit_log.csv"))
        ids = identity.identifiers("user_26", T("2026-09-15T12:00:00Z"))
        case = real_index().match(ids, T("2026-09-15T12:00:00Z"))
        self.assertFalse([m for m in case.matches if m.record.evidence_id == "evidence_02"])
        later = real_index().match(identity.identifiers("user_26", T("2026-09-17T12:00:00Z")), T("2026-09-17T12:00:00Z"))
        self.assertTrue([m for m in later.matches if m.record.evidence_id == "evidence_02" and m.record.resource_id == "account_5684"])


class RealMatchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.identity = IdentityIndex(read_csv("user_identities.csv"), read_csv("user_audit_log.csv"))
        cls.owners = identifier_owners(cls.identity)

    def case(self, user_id, audit_at):
        return real_index().match(self.identity.identifiers(user_id, T(audit_at)), T(audit_at), self.owners)

    def test_decoys_with_similar_names_and_emails_are_not_matched(self):
        # evidence_57 holds search_001 (jonas.berg), search_8814 (jonas.berglund), search_4203 (joana.berg)
        resources = {r for _, r in self.case("user_06", "2026-09-10T09:15:00Z").resources()}
        self.assertIn("search_001", resources)
        self.assertFalse({"search_8814", "search_4203"} & resources)
        # evidence_54 holds account_9116 for "Mateo Rios Gil" m.rios.gil@outlook.com, a decoy of user_02
        self.assertNotIn("account_9116", {r for _, r in self.case("user_02", "2026-09-04T15:00:00Z").resources()})

    def test_historical_email_hash_matches(self):
        # service_events_ap_south_20260915.log line 92 carries only a hash as correlation_key
        old_emails = [row["old_value"] for row in read_csv("user_audit_log.csv")
                      if row["user_id"] == "user_12" and row["field_name"] == "email"]
        old_hash = email_hash(old_emails[0])
        self.assertEqual(old_hash, "44a65154bfd8e408852578fbd76813d094d1944f255ac1769ee81ba90492f10e")
        matches = [m for m in self.case("user_12", "2026-09-15T12:00:00Z").matches if m.record.resource_id == "analytics_9914"
                   and m.record.evidence_id == "evidence_01"]
        self.assertTrue(matches)
        self.assertEqual(matches[0].matched_identifiers, {("email_hash", old_hash)})

    def test_resource_expansion_reaches_records_without_identifiers(self):
        # activity_3383 (DELETE_RECORD account_4092) has no identifiers; evidence_01 links account_4092 by email
        found = {(m.record.evidence_id, m.basis) for m in self.case("user_17", "2026-09-15T12:00:00Z").matches
                 if m.record.resource_id == "account_4092"}
        self.assertIn(("activity_3383", "resource_id"), found)
        self.assertIn(("evidence_01", "identifier"), found)


# ----------------------------------------------------------------------------- synthetic
SYNTHETIC_IDENTITIES = [
    {"user_id": "user_9001", "name": "SYNTHETIC Ada", "email": "ada.new@example.test", "phone": "+1 555 0100",
     "email_hash": email_hash("ada.new@example.test"), "updated_at": "2026-05-01T00:00:00Z"},
    {"user_id": "user_9002", "name": "SYNTHETIC Ada", "email": "ada.new2@example.test", "phone": "+1 555 0199",
     "email_hash": email_hash("ada.new2@example.test"), "updated_at": "2026-05-01T00:00:00Z"},
]
SYNTHETIC_AUDIT = [
    {"audit_id": "audit_s1", "user_id": "user_9001", "event_time": "2026-05-01T00:00:00Z", "field_name": "email",
     "old_value": "ada.old@example.test", "new_value": "ada.new@example.test"},
]


class SyntheticDataset:
    """A throwaway dataset directory: evidence_files.csv plus the given files."""

    def __init__(self, files: dict[str, str], manifest: list[tuple[str, str, str]]):
        self.root = Path(tempfile.mkdtemp(prefix="SYNTHETIC_dataset_"))
        for rel, content in files.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        with open(self.root / "evidence_files.csv", "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["file_id", "file_path", "origin", "captured_at"])
            for file_id, rel, captured in manifest:
                writer.writerow([file_id, rel, "SYNTHETIC_origin", captured])

    def build(self, **kwargs) -> EvidenceIndex:
        return EvidenceIndex.build(self.root, include_activity=False, ocr_cache=None, **kwargs)

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


def report_for(index, file_id):
    return next(r for r in index.reports if r.file_id == file_id)


class SyntheticParserFailureTests(unittest.TestCase):
    def setUp(self):
        sql_attach = "ATTACH DATABASE 'SYNTHETIC_attached.db' AS other;\nCREATE TABLE other.t (x TEXT);\n"
        self.data = SyntheticDataset(
            {
                "evidence/bad.json": "{not json",
                "evidence/odd.json": '{"exported_at": "2026-01-01T00:00:00Z", "rows": []}',
                "evidence/bad.sql": "CREATE TABLE broken (;",
                "evidence/attach.sql": sql_attach,
                "evidence/mixed.log": (
                    "2026-01-01T00:00:00Z level=INFO service=account_service event=profile_lookup_completed "
                    "resource_id=account_1 rows=0 http_status=404\nthis line is SYNTHETIC garbage\n"
                ),
                "evidence/table.csv": "a,b\n1,2\n",
                "evidence/shot.png": "SYNTHETIC not really a png",
                "evidence/unlisted.json": "{}",
            },
            [
                ("SYNTHETIC_bad_json", "evidence/bad.json", "2026-01-01T00:00:00Z"),
                ("SYNTHETIC_odd_json", "evidence/odd.json", "2026-01-01T00:00:00Z"),
                ("SYNTHETIC_bad_sql", "evidence/bad.sql", "2026-01-01T00:00:00Z"),
                ("SYNTHETIC_attach_sql", "evidence/attach.sql", "2026-01-01T00:00:00Z"),
                ("SYNTHETIC_mixed_log", "evidence/mixed.log", "2026-01-01T00:00:00Z"),
                ("SYNTHETIC_csv", "evidence/table.csv", "2026-01-01T00:00:00Z"),
                ("SYNTHETIC_png", "evidence/shot.png", "2026-01-01T00:00:00Z"),
                ("SYNTHETIC_missing", "evidence/missing.json", "2026-01-01T00:00:00Z"),
                ("SYNTHETIC_mixed_log", "evidence/bad.json", "2026-01-01T00:00:00Z"),
            ],
        )
        self.index = self.data.build(ocr=False)

    def tearDown(self):
        self.data.close()

    def test_failures_are_reported_with_reasons(self):
        expectations = {
            "SYNTHETIC_bad_json": "invalid JSON",
            "SYNTHETIC_odd_json": "unrecognized JSON structure",
            "SYNTHETIC_bad_sql": "sqlite could not load",
            "SYNTHETIC_attach_sql": "sqlite could not load",
            "SYNTHETIC_csv": "unsupported evidence format",
            "SYNTHETIC_png": "OCR disabled",
            "SYNTHETIC_missing": "does not exist",
        }
        for file_id, reason in expectations.items():
            with self.subTest(file=file_id):
                report = report_for(self.index, file_id)
                self.assertEqual(report.status, "failed")
                self.assertTrue(any(reason in e for e in report.errors), report.errors)
                self.assertEqual(find(self.index, file_id), [])
        self.assertFalse((self.data.root / "SYNTHETIC_attached.db").exists())

    def test_unparseable_line_makes_a_partial_file_but_keeps_the_good_line(self):
        report = [r for r in self.index.reports if r.file_id == "SYNTHETIC_mixed_log"][0]
        self.assertEqual((report.status, report.units_accounted.get("unparsed_line")), ("partial", 1))
        self.assertEqual(len(find(self.index, "SYNTHETIC_mixed_log")), 1)

    def test_duplicate_manifest_entry_is_rejected_not_merged(self):
        reports = [r for r in self.index.reports if r.file_id == "SYNTHETIC_mixed_log"]
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[1].status, "failed")
        self.assertIn("duplicate manifest entry", reports[1].errors[0])

    def test_unlisted_file_is_reported(self):
        self.assertEqual(self.index.notes["unlisted_files"], ["evidence/unlisted.json"])


class SyntheticTimestampTests(unittest.TestCase):
    def setUp(self):
        self.data = SyntheticDataset(
            {
                "evidence/listing.json": (
                    '{"exported_at": "2026-03-01T00:00:00Z", "collection": "SYNTHETIC_items", "records": ['
                    '{"resource_id": "export_1", "observed_at": "not-a-time", "owner_email": "ada.old@example.test"},'
                    '{"resource_id": "export_2", "observed_at": "2026-02-01T10:00:00+02:00"},'
                    '{"resource_id": "export_3"}]}'
                ),
                "evidence/untimed.json": '{"collection": "SYNTHETIC_items", "records": [{"resource_id": "export_4"}]}',
            },
            [
                ("SYNTHETIC_listing", "evidence/listing.json", "2026-03-01T00:00:00Z"),
                ("SYNTHETIC_untimed", "evidence/untimed.json", ""),
            ],
        )
        self.index = self.data.build()

    def tearDown(self):
        self.data.close()

    def test_malformed_record_time_is_never_visible(self):
        record = one(self.index, "SYNTHETIC_listing", "export_1")
        self.assertIsNone(record.observed_at)
        self.assertFalse(record.visible_at(T("2030-01-01T00:00:00Z")))
        self.assertEqual(report_for(self.index, "SYNTHETIC_listing").status, "partial")

    def test_offsets_are_normalized_to_utc(self):
        record = one(self.index, "SYNTHETIC_listing", "export_2")
        self.assertEqual(record.observed_at, T("2026-02-01T08:00:00Z"))
        self.assertEqual(record.observed_at.utcoffset(), timedelta(0))

    def test_zone_less_timestamps_are_rejected(self):
        from ghostaudit.evidence.model import parse_time_or_none

        self.assertIsNone(parse_time_or_none("2026-02-01T10:00:00"))
        self.assertIsNone(parse_time_or_none("yesterday"))
        self.assertEqual(parse_time_or_none("2026-02-01T10:00:00Z"), T("2026-02-01T10:00:00Z"))

    def test_record_without_own_time_uses_the_file_capture_time(self):
        record = one(self.index, "SYNTHETIC_listing", "export_3")
        self.assertEqual((record.observed_at, record.time_basis), (T("2026-03-01T00:00:00Z"), "file:exported_at"))

    def test_no_time_anywhere_means_never_visible(self):
        record = one(self.index, "SYNTHETIC_untimed", "export_4")
        self.assertIsNone(record.observed_at)
        self.assertFalse(record.visible_at(T("2030-01-01T00:00:00Z")))


class SyntheticMatchingTests(unittest.TestCase):
    def setUp(self):
        self.data = SyntheticDataset(
            {
                "evidence/listing.json": (
                    '{"exported_at": "2026-06-01T00:00:00Z", "collection": "SYNTHETIC_items", "records": ['
                    '{"resource_id": "export_10", "owner_email": "ADA.OLD@EXAMPLE.TEST"},'
                    '{"resource_id": "export_11", "email_hash": "' + email_hash("ada.old@example.test") + '"},'
                    '{"resource_id": "export_12", "owner_email": "ada.new@example.test"},'
                    '{"resource_id": "export_13", "owner_email": "ada.olde@example.test", "full_name": "SYNTHETIC Ada"},'
                    '{"resource_id": "export_14", "full_name": "SYNTHETIC Ada"},'
                    '{"resource_id": "export_10", "note": "no identifiers on this copy"},'
                    '{"resource_id": "export_15", "owner_email": "ada.new2@example.test"}]}'
                ),
            },
            [("SYNTHETIC_listing", "evidence/listing.json", "2026-06-01T00:00:00Z")],
        )
        self.index = self.data.build()
        self.identity = IdentityIndex(SYNTHETIC_IDENTITIES, SYNTHETIC_AUDIT)

    def tearDown(self):
        self.data.close()

    def resources(self, user_id, audit_at):
        """resource_id -> the identifier-basis match if there is one, else the first match."""
        case = self.index.match(self.identity.identifiers(user_id, T(audit_at)), T(audit_at), identifier_owners(self.identity))
        found = {}
        for m in case.matches:
            if m.record.resource_id not in found or m.basis == "identifier":
                found[m.record.resource_id] = m
        return found

    def test_historical_email_and_its_hash_match_after_the_change(self):
        found = self.resources("user_9001", "2026-07-01T00:00:00Z")
        self.assertEqual(found["export_10"].matched_identifiers, {("email", "ada.old@example.test")})  # case-insensitive
        self.assertEqual(found["export_11"].matched_identifiers, {("email_hash", email_hash("ada.old@example.test"))})
        self.assertIn("export_12", found)

    def test_email_acquired_after_audit_at_does_not_match(self):
        self.data.close()
        data = SyntheticDataset(
            {"evidence/early.json": '{"exported_at": "2026-04-01T00:00:00Z", "records": [{"resource_id": "export_20", "owner_email": "ada.new@example.test"}]}'},
            [("SYNTHETIC_early", "evidence/early.json", "2026-04-01T00:00:00Z")],
        )
        try:
            index = data.build()
            case = index.match(self.identity.identifiers("user_9001", T("2026-04-15T00:00:00Z")), T("2026-04-15T00:00:00Z"))
            self.assertEqual(case.matches, [])
        finally:
            data.close()

    def test_similar_email_and_same_name_never_link(self):
        found = self.resources("user_9001", "2026-07-01T00:00:00Z")
        self.assertNotIn("export_13", found)  # ada.olde@ is not ada.old@
        self.assertNotIn("export_14", found)  # name only
        self.assertNotIn("export_15", found)  # another user's email

    def test_duplicate_resource_reference_is_kept_and_linked_by_resource(self):
        copies = [r for r in self.index.records if r.resource_id == "export_10"]
        self.assertEqual(len(copies), 2)
        self.assertNotEqual(copies[0].location, copies[1].location)
        case = self.index.match(self.identity.identifiers("user_9001", T("2026-07-01T00:00:00Z")), T("2026-07-01T00:00:00Z"))
        bases = sorted(m.basis for m in case.matches if m.record.resource_id == "export_10")
        self.assertEqual(bases, ["identifier", "resource_id"])

    def test_foreign_identifiers_are_flagged(self):
        owners = identifier_owners(self.identity)
        self.assertEqual(owners[("email", "ada.new2@example.test")], {"user_9002"})
        found = self.resources("user_9002", "2026-07-01T00:00:00Z")
        self.assertEqual(set(found), {"export_15"})
        self.assertEqual(found["export_15"].foreign_identifiers, frozenset())


if __name__ == "__main__":
    unittest.main()
