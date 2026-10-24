"""Tests for rule loading, category resolution and per-resource assessment.

Records here are synthetic EvidenceRecord objects (evidence IDs start with SYNTHETIC); the rules
are the real dataset/deletion_rules.csv, so every expected treatment comes from that file.
"""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghostaudit.data import parse_time, read_csv  # noqa: E402
from ghostaudit.evidence.index import MatchedRecord  # noqa: E402
from ghostaudit.evidence.model import EvidenceRecord, SnapshotScope, identifiers_from_fields  # noqa: E402
from ghostaudit.policy import (  # noqa: E402
    COMPLIANT, UNKNOWN, VIOLATION, Rule, assess_resource, content_identifiers, evaluate_condition,
    learn_prefix_categories, load_rules, resolve_category,
)

T = parse_time
AUDIT = T("2026-09-15T12:00:00Z")
RULES = load_rules()
PREFIXES = {"account": "USER_PROFILE", "analytics": "ANALYTICS_PROFILE", "audit_record": "SECURITY_AUDIT_LOG",
            "backup": "BACKUP_ARCHIVE", "data_flow": "DATA_FLOW", "search": "SEARCH_INDEX_ENTRY"}


def rec(resource_id, state, when="2026-09-10T00:00:00Z", *, collection=None, record_type=None, fields=None,
        state_basis="row_present", role=None, fmt="json", claim=False, evidence_id="SYNTHETIC_ev"):
    fields = dict(fields or {})
    fields.setdefault("resource_id", resource_id)
    if role:
        fields["_store_role"] = role
    return EvidenceRecord(
        evidence_id=evidence_id, source="activity" if claim else "file", file_format=fmt, file_path="SYNTHETIC",
        location=f"SYNTHETIC {resource_id} {when}", system_id="SYNTHETIC_system", system_basis="explicit",
        resource_id=resource_id, collection=collection, record_type=record_type, state=state, state_basis=state_basis,
        observed_at=T(when), time_basis="record", file_captured_at=T(when),
        identifiers=frozenset(identifiers_from_fields(fields)), related_resources=frozenset(),
        extraction="structured", is_claim=claim, fields=fields,
    )


def assess(resource_id, *records, scopes=()):
    matches = [MatchedRecord(r, "identifier", frozenset(), None, frozenset()) for r in records]
    return assess_resource("SYNTHETIC_system", resource_id, matches, RULES, PREFIXES, list(scopes), AUDIT)


class RuleTests(unittest.TestCase):
    def test_rules_are_loaded_verbatim(self):
        rows = read_csv("deletion_rules.csv")
        self.assertEqual(len(RULES), len(rows))
        for row in rows:
            rule = RULES[row["data_category"]]
            self.assertEqual((rule.rule_id, rule.required_action, rule.condition), (row["rule_id"], row["required_action"], row["condition"]))

    def test_every_rule_condition_has_an_evaluator(self):
        for rule in RULES.values():
            self.assertNotEqual(evaluate_condition(rule, [], AUDIT)[0], "unevaluated", rule.condition)

    def test_unknown_condition_text_is_reported_not_guessed(self):
        rule = Rule("SYNTHETIC_rule", "SYNTHETIC_CATEGORY", "Only on Tuesdays", "DELETE")
        self.assertEqual(evaluate_condition(rule, [], AUDIT)[0], "unevaluated")

    def test_learned_prefixes_skip_ambiguous_ones(self):
        learned = learn_prefix_categories([
            rec("support_1", "PRESENT", record_type="SUPPORT_MESSAGE"), rec("support_2", "PRESENT", record_type="SUPPORT_ATTACHMENT"),
            rec("account_1", "PRESENT", record_type="USER_PROFILE"),
        ])
        self.assertEqual(learned, {"account": "USER_PROFILE"})


class CategoryTests(unittest.TestCase):
    def test_precedence(self):
        self.assertEqual(resolve_category([rec("support_1", "PRESENT", record_type="SUPPORT_ATTACHMENT", collection="tickets")], PREFIXES)[:2],
                         ("SUPPORT_ATTACHMENT", "explicit_record_type"))
        self.assertEqual(resolve_category([rec("analytics_1", "PRESENT", fields={"email_opt_in": "true"})], PREFIXES)[:2],
                         ("MARKETING_PROFILE", "field_email_opt_in"))
        self.assertEqual(resolve_category([rec("support_1", "PRESENT", collection="ticket_attachments")], PREFIXES)[0], "SUPPORT_ATTACHMENT")
        self.assertEqual(resolve_category([rec("search_1", "PRESENT")], PREFIXES)[:2], ("SEARCH_INDEX_ENTRY", "resource_prefix_learned"))

    def test_ambiguous_support_prefix_without_context_is_unresolved(self):
        self.assertEqual(resolve_category([rec("support_1", "MENTIONED", fmt="png")], PREFIXES)[:2], (None, "unresolved"))
        self.assertEqual(resolve_category([rec("support_1", "MENTIONED", fmt="pdf")], PREFIXES)[0], "SUPPORT_ATTACHMENT")


class AssessmentTests(unittest.TestCase):
    def test_delete_rule_with_a_replica_copy_left(self):
        a = assess("account_1", rec("account_1", "ABSENT", "2026-09-10T00:00:00Z", state_basis="rows=0"),
                   rec("account_1", "PRESENT", "2026-09-02T00:00:00Z", role="failover_replica", collection="customer_profiles"))
        self.assertEqual((a.outcome, a.finding, a.rule.rule_id, a.required_action), (VIOLATION, "REMAINING_RECORD", "rule_01", "DELETE"))
        self.assertEqual(a.channels, {"failover_replica": "PRESENT", "primary": "ABSENT"})

    def test_later_absence_and_later_recreation(self):
        gone = assess("search_1", rec("search_1", "PRESENT", "2026-09-01T00:00:00Z"), rec("search_1", "ABSENT", "2026-09-10T00:00:00Z"))
        self.assertEqual((gone.outcome, gone.state_at_audit), (COMPLIANT, "ABSENT"))
        back = assess("search_1", rec("search_1", "ABSENT", "2026-09-01T00:00:00Z"), rec("search_1", "MENTIONED", "2026-09-10T00:00:00Z", fmt="png"))
        self.assertEqual((back.outcome, back.finding), (VIOLATION, "REMAINING_RECORD"))

    def test_later_snapshot_without_the_replica_row_refutes_it(self):
        scope = SnapshotScope("SYNTHETIC_scope", "SYNTHETIC", "table=x_failover_replica", "table_snapshot", "SYNTHETIC_system",
                              "explicit", "customer_profiles", "failover_replica", T("2026-09-12T00:00:00Z"), "header",
                              T("2026-09-12T00:00:00Z"), frozenset({"account_9"}), True)
        a = assess("account_1", rec("account_1", "PRESENT", "2026-09-02T00:00:00Z", role="failover_replica", collection="customer_profiles"),
                   scopes=[scope])
        self.assertEqual(a.outcome, COMPLIANT)
        self.assertEqual(a.deciding_scope.evidence_id, "SYNTHETIC_scope")

    def test_tombstone_redaction(self):
        exposed = assess("tombstone_1", rec("tombstone_1", "PRESENT", collection="deleted_customer_references",
                                            fields={"subject_reference": "user_1", "name": "SYNTHETIC Ada", "email": "ada@example.test"}))
        self.assertEqual((exposed.outcome, exposed.finding, exposed.rule.rule_id), (VIOLATION, "EXPOSED_PII", "rule_15"))
        clean = assess("tombstone_1", rec("tombstone_1", "PRESENT", collection="deleted_customer_references",
                                          fields={"subject_reference": "user_1", "name": None, "email": None}))
        self.assertEqual((clean.outcome, clean.state_at_audit), (COMPLIANT, "REDACTED"))

    def test_analytics_anonymization(self):
        named = assess("analytics_1", rec("analytics_1", "PRESENT", fields={"user_id": "user_1", "email": "ada@example.test"}))
        self.assertEqual((named.outcome, named.finding, named.rule.rule_id), (VIOLATION, "EXPOSED_PII", "rule_07"))
        anonymous = assess("analytics_1", rec("analytics_1", "PRESENT", fields={"user_id": None, "email": None}))
        self.assertEqual((anonymous.outcome, anonymous.state_at_audit), (COMPLIANT, "ANONYMIZED"))

    def test_lookup_keys_are_not_exposure(self):
        record = rec("analytics_1", "PRESENT", state_basis="rows=1", fields={"rows": "1", "correlation_key": "a" * 64})
        self.assertEqual(content_identifiers(record), frozenset())
        self.assertEqual(assess("analytics_1", record).outcome, UNKNOWN)

    def test_retained_security_record(self):
        kept = assess("audit_record_1", rec("audit_record_1", "PRESENT", fields={"retention_until": "2033-01-01T00:00:00Z"}))
        self.assertEqual((kept.outcome, kept.required_action, kept.condition_status), (COMPLIANT, "RETAIN", "met"))
        missing = assess("audit_record_1", rec("audit_record_1", "ABSENT", state_basis="rows=0"))
        self.assertEqual((missing.outcome, missing.finding), (VIOLATION, "RETAINED_RECORD_MISSING"))
        expired = assess("audit_record_1", rec("audit_record_1", "PRESENT", fields={"retention_until": "2026-01-01T00:00:00Z"}))
        self.assertEqual((expired.outcome, expired.condition_status), (UNKNOWN, "not_met"))

    def test_restore_risk_and_data_flow(self):
        self.assertEqual(assess("backup_1", rec("backup_1", "ACTIVE", state_basis="restore_allowed=true")).finding, "RESTORE_RISK")
        self.assertEqual(assess("backup_1", rec("backup_1", "BLOCKED", state_basis="restore_allowed=false")).outcome, COMPLIANT)
        self.assertEqual(assess("backup_1", rec("backup_1", "MENTIONED", fmt="png")).outcome, UNKNOWN)
        self.assertEqual(assess("data_flow_1", rec("data_flow_1", "ACTIVE", state_basis="enabled=true")).finding, "ACTIVE_DATA_FLOW")
        self.assertEqual(assess("data_flow_1", rec("data_flow_1", "DISABLED", state_basis="enabled=false")).outcome, COMPLIANT)

    def test_claims_alone_never_establish_state(self):
        a = assess("account_1", rec("account_1", None, claim=True, state_basis=None, fields={"reported_result": "SUCCESS"}))
        self.assertEqual(a.outcome, UNKNOWN)
        self.assertIn("claims", a.basis)


if __name__ == "__main__":
    unittest.main()
