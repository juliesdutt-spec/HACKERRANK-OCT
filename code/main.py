"""Ghost Data Still Exists: audit every deletion request and write output.csv at the repo root.

    python3 code/main.py                       # dataset/deletion_requests.csv -> output.csv
    python3 code/main.py --requests sample_deletion_requests.csv --output artifacts/sample_predictions.csv

Also writes artifacts/case_audits_<name>.json with each case's audit trail and steps. The run is
deterministic and needs no API key; screenshots need the Tesseract binary (see README.md). If any
evidence file cannot be parsed, the run stops with exit code 3 instead of silently writing a
different output, unless --allow-parse-failures is given.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ghostaudit.audit import AuditEngine  # noqa: E402
from ghostaudit.data import REPO_ROOT, read_csv  # noqa: E402
from ghostaudit.output import citable_evidence_times, validate_output, write_output  # noqa: E402
from ghostaudit.steps import build_steps  # noqa: E402


def run(requests_file: str, output: Path, audits_out: Path | None, allow_parse_failures: bool = False) -> int:
    engine = AuditEngine.build()
    statuses = collections.Counter(r.status for r in engine.index.reports)
    print(f"evidence: {len(engine.index.reports)} files {dict(sorted(statuses.items()))}")
    failed = [r for r in engine.index.reports if r.status == "failed"]
    if failed:
        print(f"WARNING: {len(failed)} evidence files could not be parsed; results would differ from a full run:", file=sys.stderr)
        for report in failed[:12]:
            print(f"  {report.file_id} {report.file_path}: {'; '.join(report.errors)}", file=sys.stderr)
        if not allow_parse_failures:
            print("no output written (install the missing tool, or pass --allow-parse-failures)", file=sys.stderr)
            return 3
    requests = read_csv(requests_file)
    audits = []
    for request in requests:
        audit = engine.audit(request)
        audit["steps"], audit["step_notes"] = build_steps(audit)
        audits.append(audit)
    unresolved = [a["case_id"] for a in audits if a["status"] is None]
    if unresolved:
        print(f"cannot decide a status for {unresolved}; no output written", file=sys.stderr)
        return 2
    output.parent.mkdir(parents=True, exist_ok=True)
    write_output(((a["case_id"], a["status"], a["steps"]) for a in audits), output)

    times = citable_evidence_times({name: read_csv(f"{name}.csv") for name in ("evidence_files", "system_activity", "user_audit_log")})
    errors, warnings = validate_output(output, requests, times)
    if audits_out is not None:
        audits_out.parent.mkdir(parents=True, exist_ok=True)
        audits_out.write_text(json.dumps(sorted(audits, key=lambda a: a["case_id"]), indent=1, ensure_ascii=False) + "\n",
                              encoding="utf-8")
    statuses = collections.Counter(a["status"] for a in audits)
    steps = collections.Counter(s["phase"] for a in audits for s in a["steps"])
    shown = output.relative_to(REPO_ROOT) if output.is_relative_to(REPO_ROOT) else output
    print(f"{len(audits)} cases -> {shown}: {dict(sorted(statuses.items()))}; steps {dict(steps)}")
    print(f"validation: {len(errors)} errors, {len(warnings)} warnings")
    for message in errors[:20] + warnings[:5]:
        print(f"  {message}")
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--requests", default="deletion_requests.csv", help="request file inside dataset/")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "output.csv")
    parser.add_argument("--no-audit-trail", action="store_true", help="skip writing artifacts/case_audits_*.json")
    parser.add_argument("--allow-parse-failures", action="store_true",
                        help="write output even if some evidence files could not be parsed (e.g. Tesseract missing)")
    args = parser.parse_args(argv)
    name = "main" if args.requests == "deletion_requests.csv" else Path(args.requests).stem.replace("_deletion_requests", "")
    trail = None if args.no_audit_trail else REPO_ROOT / "artifacts" / f"case_audits_{name}.json"
    return run(args.requests, args.output, trail, args.allow_parse_failures)


if __name__ == "__main__":
    raise SystemExit(main())
