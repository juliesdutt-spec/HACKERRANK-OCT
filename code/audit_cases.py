"""Run the deletion audit and write the per-case audit trail with steps (not output.csv; see main.py).

    python3 code/audit_cases.py                         # samples -> artifacts/case_audits_sample.json
    python3 code/audit_cases.py --set main              # 518 requests -> artifacts/case_audits_main.json
    python3 code/audit_cases.py --case case_11          # print one sample case
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
from ghostaudit.steps import build_steps  # noqa: E402

REQUEST_FILES = {"sample": "sample_deletion_requests.csv", "main": "deletion_requests.csv"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", choices=sorted(REQUEST_FILES), default="sample")
    parser.add_argument("--case", nargs="*", default=[], help="print these case IDs instead of writing the file")
    args = parser.parse_args(argv)

    engine = AuditEngine.build()
    requests = read_csv(REQUEST_FILES[args.set])

    def audit(request):
        result = engine.audit(request)
        result["steps"], result["step_notes"] = build_steps(result)
        return result

    if args.case:
        wanted = set(args.case)
        for request in requests:
            if request["case_id"] in wanted:
                print(json.dumps(audit(request), indent=2, ensure_ascii=False))
        return 0
    results = sorted((audit(r) for r in requests), key=lambda r: r["case_id"])
    out = REPO_ROOT / "artifacts" / f"case_audits_{args.set}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    statuses = collections.Counter((r["status"], r["confidence"]) for r in results)
    print(f"{len(results)} cases: {dict(sorted(statuses.items(), key=str))}")
    print(f"cases with ambiguities: {sum(1 for r in results if r['ambiguities'])}")
    print(f"written to {out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
