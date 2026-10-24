"""Tests for identity resolution. Run from the repo root: python3 -m unittest discover -s code/tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghostaudit.data import parse_time, read_csv  # noqa: E402
from ghostaudit.identity import IdentityIndex, email_hash  # noqa: E402


def make_index():
    identities = [
        {"user_id": "u1", "name": "Ada", "email": "ada.new@x.com", "phone": "+1 222", "email_hash": email_hash("ada.new@x.com"), "updated_at": "2026-05-01T00:00:00Z"},
        {"user_id": "u2", "name": "Bob", "email": "ada.old@x.com", "phone": "+1 333", "email_hash": email_hash("ada.old@x.com"), "updated_at": "2026-06-01T00:00:00Z"},
    ]
    audit = [
        {"audit_id": "a1", "user_id": "u1", "event_time": "2026-01-01T00:00:00Z", "field_name": "email", "old_value": "ada.old@x.com", "new_value": "ada.mid@x.com"},
        {"audit_id": "a2", "user_id": "u1", "event_time": "2026-05-01T00:00:00Z", "field_name": "email", "old_value": "ada.mid@x.com", "new_value": "ada.new@x.com"},
        {"audit_id": "a3", "user_id": "u2", "event_time": "2026-06-01T00:00:00Z", "field_name": "email", "old_value": "bob@x.com", "new_value": "ada.old@x.com"},
        {"audit_id": "a4", "user_id": "u1", "event_time": "2026-03-01T00:00:00Z", "field_name": "phone", "old_value": "+1 111", "new_value": "+1 222"},
    ]
    return IdentityIndex(identities, audit)


class SyntheticIdentityTests(unittest.TestCase):
    def setUp(self):
        self.index = make_index()

    def test_email_matching_ignores_case_and_whitespace(self):
        self.assertEqual(self.index.resolve_email("  ADA.NEW@X.COM "), "u1")

    def test_historical_email_resolves_to_original_owner(self):
        self.assertEqual(self.index.resolve_email("ada.mid@x.com", parse_time("2026-09-01T00:00:00Z")), "u1")

    def test_reused_email_belongs_to_most_recent_holder(self):
        self.assertEqual(self.index.resolve_email("ada.old@x.com", parse_time("2026-09-01T00:00:00Z")), "u2")
        self.assertEqual(self.index.resolve_email("ada.old@x.com", parse_time("2026-02-01T00:00:00Z")), "u1")

    def test_email_acquired_after_audit_time_is_unknown(self):
        self.assertIsNone(self.index.resolve_email("ada.new@x.com", parse_time("2026-04-01T00:00:00Z")))

    def test_identifiers_are_limited_to_audit_time(self):
        early = self.index.identifiers("u1", parse_time("2026-02-01T00:00:00Z"))
        self.assertEqual(early.emails, {"ada.old@x.com", "ada.mid@x.com"})
        self.assertEqual(early.phones, {"+1 111"})
        late = self.index.identifiers("u1", parse_time("2026-09-01T00:00:00Z"))
        self.assertEqual(late.emails, {"ada.old@x.com", "ada.mid@x.com", "ada.new@x.com"})
        self.assertEqual(late.phones, {"+1 111", "+1 222"})
        self.assertIn(email_hash("ada.mid@x.com"), late.email_hashes)


class DatasetIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = IdentityIndex(read_csv("user_identities.csv"), read_csv("user_audit_log.csv"))
        cls.requests = read_csv("deletion_requests.csv")

    def test_every_request_email_resolves_at_audit_time(self):
        unresolved = [
            r["case_id"] for r in self.requests
            if self.index.resolve_email(r["email"], parse_time(r["audit_at"])) is None
        ]
        self.assertEqual(unresolved, [])

    def test_sample_cases_resolve_to_expected_users(self):
        samples = {r["case_id"]: r for r in read_csv("sample_deletion_requests.csv")}
        expected = {"case_01": "user_01", "case_03": "user_43", "case_12": "user_12"}
        for case_id, user_id in expected.items():
            row = samples[case_id]
            self.assertEqual(self.index.resolve_email(row["email"], parse_time(row["audit_at"])), user_id)

    def test_current_snapshot_identifiers_are_included(self):
        snapshot = {r["user_id"]: r for r in read_csv("user_identities.csv")}
        for user_id in self.index.user_ids():
            ids = self.index.identifiers(user_id)
            self.assertIn(snapshot[user_id]["email"].lower(), ids.emails)
            self.assertIn(snapshot[user_id]["phone"], ids.phones)
            self.assertIn(snapshot[user_id]["email_hash"], ids.email_hashes)


if __name__ == "__main__":
    unittest.main()
