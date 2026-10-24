"""Score predictions against expected answers (by default: predict the 32 samples, then score them).

    python3 code/evaluation/main.py
    python3 code/evaluation/main.py --predictions artifacts/sample_predictions.csv --expected dataset/sample_output.csv

Metrics (written to artifacts/sample_scores.json):
* status accuracy: share of cases with the expected deletion_status;
* step-level precision / recall / F1: steps compared as (phase, system_id, resource_id, operation,
  state, evidence_id), ignoring step_no, as multisets per case;
* field-level accuracy: steps aligned by position (step_no); each of the 7 fields of every expected
  position is compared, and missing or extra positions count as mismatches;
* sequence accuracy: share of cases whose whole steps array equals the expected one in order.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))

from ghostaudit.steps import STEP_FIELDS  # noqa: E402

STEP_KEY = ("phase", "system_id", "resource_id", "operation", "state", "evidence_id")


def load(path: Path) -> dict[str, dict]:
    with open(path, newline="", encoding="utf-8") as handle:
        return {row["case_id"]: {"status": row["deletion_status"], "steps": json.loads(row["steps_json"])} for row in csv.DictReader(handle)}


def score(predicted: dict[str, dict], expected: dict[str, dict]) -> dict:
    tp = fp = fn = 0
    field_hits = Counter()
    field_total = 0
    sequences = statuses = 0
    details = []
    for case_id in sorted(expected):
        exp, pred = expected[case_id], predicted.get(case_id, {"status": None, "steps": []})
        status_ok = exp["status"] == pred["status"]
        statuses += status_ok
        e = Counter(tuple(s[k] for k in STEP_KEY) for s in exp["steps"])
        p = Counter(tuple(s[k] for k in STEP_KEY) for s in pred["steps"])
        hit = sum((e & p).values())
        tp, fp, fn = tp + hit, fp + sum(p.values()) - hit, fn + sum(e.values()) - hit
        positions = max(len(exp["steps"]), len(pred["steps"]))
        field_total += positions
        for i in range(positions):
            a = exp["steps"][i] if i < len(exp["steps"]) else None
            b = pred["steps"][i] if i < len(pred["steps"]) else None
            for field in STEP_FIELDS:
                field_hits[field] += bool(a and b and a.get(field) == b.get(field))
        same = [tuple(s[k] for k in STEP_FIELDS) for s in exp["steps"]] == [tuple(s[k] for k in STEP_FIELDS) for s in pred["steps"]]
        sequences += same
        details.append({
            "case_id": case_id, "expected_status": exp["status"], "predicted_status": pred["status"],
            "status_match": status_ok, "sequence_match": same,
            "missing_steps": [list(k) for k in sorted((e - p).elements())],
            "extra_steps": [list(k) for k in sorted((p - e).elements())],
        })
    n = len(expected)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    per_field = {f: round(field_hits[f] / field_total, 4) if field_total else 0.0 for f in STEP_FIELDS}
    return {
        "cases": n,
        "status_accuracy": round(statuses / n, 4),
        "step_level": {"matched": tp, "predicted_only": fp, "expected_only": fn, "precision": round(precision, 4),
                       "recall": round(recall, 4), "f1": round(2 * precision * recall / (precision + recall), 4) if tp else 0.0},
        "field_level": {"positions_compared": field_total, "per_field": per_field,
                        "overall": round(sum(field_hits.values()) / (field_total * len(STEP_FIELDS)), 4) if field_total else 0.0},
        "sequence_accuracy": round(sequences / n, 4),
        "case_details": details,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--predictions", type=Path, help="predictions CSV; default: predict the samples first")
    parser.add_argument("--expected", type=Path, default=ROOT / "dataset" / "sample_output.csv")
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts" / "sample_scores.json")
    args = parser.parse_args(argv)
    predictions = args.predictions
    if predictions is None:
        predictions = ROOT / "artifacts" / "sample_predictions.csv"
        subprocess.run([sys.executable, str(ROOT / "code" / "main.py"), "--requests", "sample_deletion_requests.csv",
                        "--output", str(predictions)], check=True)
    result = score(load(predictions), load(args.expected))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    s = result["step_level"]
    print(f"cases {result['cases']}: status accuracy {result['status_accuracy']:.1%}; steps P {s['precision']:.1%} "
          f"R {s['recall']:.1%} F1 {s['f1']:.1%} ({s['matched']} matched, {s['predicted_only']} extra, {s['expected_only']} missing); "
          f"field accuracy {result['field_level']['overall']:.1%}; sequence accuracy {result['sequence_accuracy']:.1%}")
    print(f"per field: {result['field_level']['per_field']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
