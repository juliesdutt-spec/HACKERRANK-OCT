"""Turn one case's audit result into the challenge's ordered steps_json.

Contract (problem statement, AGENTS.md 6.3-6.6) and the pattern every sample answer follows:
* NOT_APPLICABLE: [].
* COMPLETE: only VERIFICATION steps, one per resource whose compliant state is established,
  each citing the evidence that proves it.
* INCOMPLETE: a FINDING per violation (citing the evidence that shows it), the REMEDIATION that
  fixes it, and a VERIFICATION of the required final state for every resource in the plan:
  remediated ones, already-compliant ones, and evidence-linked ones whose current state is
  unknown but whose required treatment is known. Remediation and verification are future work,
  so their evidence_id is "".
* Order: phase, then the operation order of the problem statement's table, then system_id and
  resource_id ascending; step_no counts from 1.
"""
from __future__ import annotations

from typing import Any

OPERATION_ORDER = [
    "REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK",
    "DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD",
    "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED", "VERIFY_RECORD_ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED",
]
PHASE = {op: ("FINDING" if op.startswith("REPORT_") else "VERIFICATION" if op.startswith("VERIFY_") else "REMEDIATION")
         for op in OPERATION_ORDER}
PHASE_ORDER = ["FINDING", "REMEDIATION", "VERIFICATION"]
STATE = {
    "REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT", "REPORT_ACTIVE_DATA_FLOW": "ACTIVE",
    "REPORT_RESTORE_RISK": "ACTIVE", "DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED",
    "DELETE_RECORD": "ABSENT", "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT", "VERIFY_RECORD_REDACTED": "REDACTED",
    "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED": "DISABLED", "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}
STEP_FIELDS = ("step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id")

FINDING_OPERATION = {
    "REMAINING_RECORD": "REPORT_REMAINING_RECORD",
    "EXPOSED_PII": "REPORT_EXPOSED_PII",
    "ACTIVE_DATA_FLOW": "REPORT_ACTIVE_DATA_FLOW",
    "RESTORE_RISK": "REPORT_RESTORE_RISK",
}
RESTORE_CONDITION = "Restore access can be blocked"


def remediation_operation(finding: str, required_action: str | None) -> str | None:
    if finding == "REMAINING_RECORD":
        return "DELETE_RECORD"
    if finding == "EXPOSED_PII":
        return {"REDACT": "REDACT_RECORD", "ANONYMIZE": "ANONYMIZE_RECORD"}.get(required_action or "")
    if finding == "ACTIVE_DATA_FLOW":
        return "DISABLE_DATA_FLOW"
    if finding == "RESTORE_RISK":
        return "BLOCK_RESTORE"
    return None


def verification_operation(resource: dict[str, Any]) -> str | None:
    """The final state the resource's rule requires."""
    action = resource.get("required_action")
    if action == "RETAIN" and (resource.get("rule_condition") or "").strip() == RESTORE_CONDITION:
        return "VERIFY_RESTORE_BLOCKED"
    return {
        "DELETE": "VERIFY_RECORD_ABSENT", "REDACT": "VERIFY_RECORD_REDACTED", "ANONYMIZE": "VERIFY_RECORD_ANONYMIZED",
        "RETAIN": "VERIFY_RECORD_PRESENT", "DISABLE_FLOW": "VERIFY_DATA_FLOW_DISABLED",
    }.get(action or "")


def cited_evidence(resource: dict[str, Any]) -> str | None:
    """The evidence that decided the resource's state: the latest deciding observation (ties by ID),
    or the later snapshot that no longer lists it."""
    deciding = resource.get("deciding_evidence") or []
    if deciding:
        latest = max(deciding, key=lambda d: (d["observed_at"] or "", d["evidence_id"]))
        return latest["evidence_id"]
    if resource.get("deciding_scope"):
        return resource["deciding_scope"]["evidence_id"]
    return None


def build_steps(audit: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """(steps, notes). Notes say why a resource got no step, so nothing is dropped silently."""
    status = audit.get("status")
    notes: list[str] = []
    if status not in ("COMPLETE", "INCOMPLETE"):
        return [], notes
    raw: list[tuple[str, str, str, str]] = []  # (operation, system_id, resource_id, evidence_id)
    for resource in audit.get("resources", []):
        system_id, resource_id = resource["system_id"], resource["resource_id"]
        label = f"{system_id}/{resource_id}"
        if system_id is None:
            notes.append(f"{label}: no system_id could be established; no step")
            continue
        verify = verification_operation(resource)
        outcome = resource["outcome"]
        if status == "COMPLETE":
            evidence = cited_evidence(resource)
            if outcome == "COMPLIANT" and verify and evidence:
                raw.append((verify, system_id, resource_id, evidence))
            else:
                notes.append(f"{label}: {outcome.lower()} with no citable proof of the required state; no verification step")
            continue
        if outcome == "VIOLATION":
            finding = FINDING_OPERATION.get(resource["finding"] or "")
            fix = remediation_operation(resource["finding"] or "", resource.get("required_action"))
            evidence = cited_evidence(resource)
            if not (finding and fix and evidence):
                notes.append(f"{label}: violation {resource['finding']} has no allowed finding/remediation or no citable evidence")
                continue
            raw.append((finding, system_id, resource_id, evidence))
            raw.append((fix, system_id, resource_id, ""))
        elif outcome == "UNKNOWN" and resource.get("link_strength") != "evidence_content":
            notes.append(f"{label}: unknown state and linked only by a claim; not planned")
            continue
        if verify:
            raw.append((verify, system_id, resource_id, ""))
        else:
            notes.append(f"{label}: no required treatment is known; no verification step")
    return order_steps(raw), notes


def order_steps(raw: list[tuple[str, str, str, str]]) -> list[dict[str, Any]]:
    unique = sorted(set(raw), key=lambda s: (PHASE_ORDER.index(PHASE[s[0]]), OPERATION_ORDER.index(s[0]), s[1], s[2], s[3]))
    return [
        {"step_no": n, "phase": PHASE[op], "system_id": system_id, "resource_id": resource_id,
         "operation": op, "state": STATE[op], "evidence_id": evidence_id}
        for n, (op, system_id, resource_id, evidence_id) in enumerate(unique, start=1)
    ]
