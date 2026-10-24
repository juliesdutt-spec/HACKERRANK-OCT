"""Independent check of output.csv against the challenge contract. It deliberately does not import
ghostaudit, so it does not share code with the built-in validator.

    python3 code/evaluation/check_output.py          # checks <repo root>/output.csv; exit 1 on any problem
"""
import csv, json, re, sys
from datetime import datetime
from pathlib import Path
root = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[2])
T = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
OPS = {"FINDING": ["REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK"],
       "REMEDIATION": ["DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD"],
       "VERIFICATION": ["VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED", "VERIFY_RECORD_ANONYMIZED",
                        "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED"]}
STATE = dict(zip(sum(OPS.values(), []), ["PRESENT", "PRESENT", "ACTIVE", "ACTIVE", "DISABLED", "BLOCKED", "ABSENT", "REDACTED",
                                          "ANONYMIZED", "ABSENT", "PRESENT", "REDACTED", "ANONYMIZED", "DISABLED", "BLOCKED"]))
ORDER = [op for ph in ("FINDING", "REMEDIATION", "VERIFICATION") for op in OPS[ph]]
FIELDS = ["step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"]
req = {r["case_id"]: r for r in csv.DictReader(open(f"{root}/dataset/deletion_requests.csv", encoding="utf-8"))}
cite = {r["file_id"]: T(r["captured_at"]) for r in csv.DictReader(open(f"{root}/dataset/evidence_files.csv", encoding="utf-8"))}
cite |= {r["activity_id"]: T(r["event_time"]) for r in csv.DictReader(open(f"{root}/dataset/system_activity.csv", encoding="utf-8"))}
cite |= {r["audit_id"]: T(r["event_time"]) for r in csv.DictReader(open(f"{root}/dataset/user_audit_log.csv", encoding="utf-8"))}
with open(f"{root}/output.csv", newline="", encoding="utf-8") as f:
    rows = list(csv.reader(f))
problems = []
check = lambda cond, msg: cond or problems.append(msg)
check(rows[0] == ["case_id", "deletion_status", "steps_json"], f"header {rows[0]}")
data = rows[1:]
ids = [r[0] for r in data]
check(len(data) == 518, f"{len(data)} rows")
check(sorted(ids) == sorted(req) and len(set(ids)) == len(ids), "case_ids do not match requests one-to-one")
num = [int(re.sub(r"\D", "", i)) for i in ids]
check(num == sorted(num), "not sorted by case number")
counts = {}
for case_id, status, raw in data:
    counts[status] = counts.get(status, 0) + 1
    check(status in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"), f"{case_id} status {status}")
    steps = json.loads(raw)
    check(isinstance(steps, list), f"{case_id} not a list")
    audit = T(req[case_id]["audit_at"])
    if status == "NOT_APPLICABLE":
        check(steps == [], f"{case_id} NA with steps")
    phases = [s["phase"] for s in steps]
    if status == "COMPLETE":
        check(set(phases) <= {"VERIFICATION"}, f"{case_id} COMPLETE has non-verification steps")
    if status == "INCOMPLETE":
        check("FINDING" in phases and "REMEDIATION" in phases and "VERIFICATION" in phases, f"{case_id} INCOMPLETE missing a phase")
    keys = []
    for i, s in enumerate(steps, 1):
        check(list(s) == FIELDS or set(s) == set(FIELDS), f"{case_id} step {i} fields {list(s)}")
        check(s["step_no"] == i, f"{case_id} step numbering")
        check(s["operation"] in OPS.get(s["phase"], []), f"{case_id} {s['operation']} in {s['phase']}")
        check(STATE.get(s["operation"]) == s["state"], f"{case_id} state {s['state']} for {s['operation']}")
        check(bool(s["system_id"]) and bool(s["resource_id"]), f"{case_id} step {i} empty system/resource")
        needs = status == "COMPLETE" or s["phase"] == "FINDING"
        if needs:
            check(s["evidence_id"] in cite and cite[s["evidence_id"]] <= audit, f"{case_id} step {i} evidence {s['evidence_id']} missing or after audit_at")
        else:
            check(s["evidence_id"] == "", f"{case_id} step {i} future step with evidence")
        keys.append((ORDER.index(s["operation"]), s["system_id"], s["resource_id"]))
    check(keys == sorted(keys), f"{case_id} step order")
    if status == "INCOMPLETE":
        f_res = {(s["system_id"], s["resource_id"]) for s in steps if s["phase"] == "FINDING"}
        r_res = {(s["system_id"], s["resource_id"]) for s in steps if s["phase"] == "REMEDIATION"}
        v_res = {(s["system_id"], s["resource_id"]) for s in steps if s["phase"] == "VERIFICATION"}
        check(f_res == r_res and r_res <= v_res, f"{case_id} findings/remediation/verification do not line up")
print("rows", len(data), "statuses", counts, "| problems:", len(problems))
for p in problems[:20]: print("  ", p)
sys.exit(1 if problems else 0)
