# Ghost Data Still Exists: deletion-audit agent

Solution for **HackerRank Orchestrate, October 2026**. For every request in `dataset/deletion_requests.csv`, the program:
1. decides whether the person actually asked for deletion;
2. resolves the person's current and historical identifiers;
3. links them to the evidence that existed at the case's `audit_at`;
4. applies `dataset/deletion_rules.csv` to each linked record;
5. writes `output.csv`: `COMPLETE`, `INCOMPLETE` or `NOT_APPLICABLE` for each case, with the ordered findings, remediation and verification steps.

The pipeline is deterministic. It uses **no LLM, no network access and no API key**.

## Requirements

- **Python 3.11 to 3.13.** The suite was run on 3.11.17, 3.12.3 and 3.13.17, and all three produce a byte-identical `output.csv`. Other versions were not tested.
- **Python packages** from `requirements.txt`: `pypdf==6.17.0` reads the PDFs, and `Pillow==12.3.0` prepares the screenshots for OCR.
- **Tesseract OCR 5.x**, which reads the 11 PNG screenshots. Version 5.3.4 was used.

## Setup

Place the solution files in the challenge repository root, next to `dataset/`. All paths are resolved from the repository root.

```text
<repo root>/
├── dataset/               challenge data (not part of code.zip)
├── README.md
├── requirements.txt
├── code/                  solution, tests and evaluation scripts
└── docs/                  design notes
```

```bash
python3 -m venv .venv && source .venv/bin/activate   # optional
pip install -r requirements.txt
```

Install Tesseract and check that `tesseract --version` works:

| OS | Command |
| --- | --- |
| Ubuntu / Debian | `sudo apt-get install -y tesseract-ocr` |
| macOS (Homebrew) | `brew install tesseract` |
| Windows | Install the Tesseract 5 build from the UB Mannheim project (`https://github.com/UB-Mannheim/tesseract/wiki`) and add its folder (e.g. `C:\Program Files\Tesseract-OCR`) to `PATH` |

Without Tesseract, `code/main.py` stops with exit code 3 and writes nothing. It does not silently produce a different `output.csv`. In a test without Tesseract, 5 cases changed. `--allow-parse-failures` overrides this.

## Run

All commands run from the repository root.

| Purpose | Command | Writes |
| --- | --- | --- |
| Generate the submission | `python3 code/main.py` | `output.csv` (validated against the output contract) and `artifacts/case_audits_main.json` |
| Run all tests (111) | `python3 -m unittest discover -s code/tests` | nothing |
| Independent check of `output.csv` (shares no code with the built-in validator) | `python3 code/evaluation/check_output.py` | nothing (exit 1 on any problem) |
| Score the 32 samples | `python3 code/evaluation/main.py` | `artifacts/sample_predictions.csv`, `artifacts/sample_scores.json` |
| Team-authored edge cases | `python3 code/evaluation/custom_cases.py` | `artifacts/custom_cases.json` |
| Status, finding and treatment checks on the samples | `python3 code/evaluation/evaluate_status.py` | `artifacts/sample_status_evaluation.json` |
| Evidence-linkage checks on the samples | `python3 code/evaluation/validate_evidence.py` | `artifacts/sample_evidence_validation.json` |
| Evidence parse summary | `python3 code/index_evidence.py` | `artifacts/evidence_parse_summary.json` |
| Full audit trail of one case | `python3 code/audit_cases.py --case case_11` | printed |

The independent-checker test reads the generated `output.csv`. Until `python3 code/main.py` has run, it is skipped and the suite reports `OK (skipped=1)`.

Timing (4 vCPU container): 16 to 17 s on the first run, when the 11 screenshots are OCR'd, and 2.2 to 2.4 s afterwards. OCR text is cached in `.cache/ocr/`, keyed by image hash, scale and Tesseract version; the cache is safe to delete.

## Inputs and outputs

- **Reads** `dataset/deletion_requests.csv`, `sample_deletion_requests.csv`, `user_identities.csv`, `user_audit_log.csv`, `system_activity.csv`, `deletion_rules.csv` and `evidence_files.csv`, plus every file the manifest lists under `dataset/evidence/`. `sample_output.csv` is read only by the evaluation scripts.
- **Writes** `output.csv` at the repository root, the audit trails and scores in `artifacts/`, and the OCR cache in `.cache/ocr/`. The dataset is never modified.

### Output schema

`output.csv` has the columns `case_id,deletion_status,steps_json`:
- one row per request (518), ordered by case number (`case_33` to `case_550`), as in `deletion_requests.csv`;
- `steps_json` is a compact JSON array of objects with exactly `step_no`, `phase`, `system_id`, `resource_id`, `operation`, `state` and `evidence_id`.

| Status | Steps |
| --- | --- |
| `NOT_APPLICABLE` | `[]` |
| `COMPLETE` | `VERIFICATION` steps only, each citing the `file_id` that proves the state at `audit_at` |
| `INCOMPLETE` | `FINDING` steps (citing evidence), then `REMEDIATION`, then post-remediation `VERIFICATION` (`evidence_id` `""`) |

Steps are ordered by phase, then the operation order of the challenge table, then `system_id` and `resource_id`. `step_no` counts from 1. Every cited evidence ID exists and was captured at or before `audit_at`.

## How it works

Details are in `docs/evidence_index.md` and `docs/status_engine.md`.

1. **Evidence index** (`code/ghostaudit/evidence/`). One parser per observed format turns all 94 evidence files and the activity stream into records. Each record keeps its provenance (file ID and location) and carries system, resource, state, identifiers and time.
   - Formats: SQLite snapshots, six log syntaxes, three JSON layouts, a text handover, PDF text, and Tesseract OCR of screenshots.
   - A record is visible at `audit_at` only if both its observation time and its file's capture time are at or before `audit_at`.
   - Activity rows are kept as claims and never set a state.
2. **Identity** (`code/ghostaudit/identity.py`). Current snapshot plus audit-log history gives every email, hash and phone the person held up to `audit_at`. Email matching is case-insensitive.
3. **Request intent** (`code/ghostaudit/requests.py`). An explicit refusal, e.g. "not a request to erase it", gives `NOT_APPLICABLE`. Otherwise a deletion term in English, Spanish, Portuguese, French, Japanese or Korean gives a deletion audit.
4. **Linking and policy** (`code/ghostaudit/audit.py`, `policy.py`).
   - Records link by exact identifier, then one hop through resource IDs. Names and timestamps never link.
   - A claim-only link contradicted by the record's own content is dropped.
   - Each resource gets its rule from `deletion_rules.csv`, evaluated per storage channel (primary versus replicas) at `audit_at`, with exposure taken from record content only.
5. **Steps and output** (`code/ghostaudit/steps.py`, `output.py`, `code/main.py`). Assessments become ordered steps, which are written to `output.csv` and checked by a contract validator.

## Results on the 32 public samples

| Metric | Result |
| --- | --- |
| Status accuracy | 100% |
| Step precision | 100% (0 invented steps) |
| Step recall | 82.4% |
| Step F1 | 90.3% |
| Field-level accuracy | 71.7% |
| Sequence accuracy | 31.2% |
| Team-authored custom cases | 6/6 pass |

Every missing step belongs to a resource that participant evidence cannot link to the person without timing heuristics or post-audit files. See `code/evaluation/evaluation_report.md`. Model usage: none (`code/evaluation/usage_report.md`).

## Known limitations

- **Screenshots.** On screenshots, status labels sit on different OCR lines from resource IDs, so those resources' states stay unknown (4 main-set cases). OCR is version-sensitive: another Tesseract version may read screenshots differently.
- **Linkage gaps.** Resources that can only be tied to a person by workflow timing, or by files captured after `audit_at`, are not linked. Such cases get fewer verification steps. Six COMPLETE cases have no provable verification step and output `[]`.
- **Sample-shaped mappings.** Three category mappings and the earlier-request scoping rule were shaped while studying sample cases. The sample scores therefore overstate generalisation.

## Determinism

- No randomness; all iteration is sorted.
- Repeated runs, and runs on Python 3.11, 3.12 and 3.13, give a byte-identical `output.csv`, as long as the Tesseract version is the same.

## Submission artifacts

| Item | Location |
| --- | --- |
| `output.csv` | repository root |
| `code.zip` | repository root; contains `README.md`, `requirements.txt`, `code/` (including `code/evaluation/evaluation_report.md` and `usage_report.md`) and the solution docs in `docs/` |
| Chat transcript | `log.txt` at the repository root |

Rebuild `code.zip` from the repository root:

```bash
rm -f code.zip && zip -r code.zip README.md requirements.txt code docs/dataset_findings.md docs/evidence_index.md docs/status_engine.md -x "*/__pycache__/*" "*.pyc" "*/.venv/*" "*/.DS_Store"
```
