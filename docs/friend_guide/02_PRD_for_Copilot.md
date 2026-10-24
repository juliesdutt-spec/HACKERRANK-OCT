# PRD: Ghost Data Still Exists (deletion-audit agent)

Instructions for the AI assistant (Copilot): read this whole file and `AGENTS.md`. Build one milestone at a time when asked. Keep code simple, readable and deterministic, and add a small test per milestone. Explain your code when asked. Never use files outside `dataset/` for predictions.

## 1. Goal

A commerce platform's deletion workflow can say "Complete" while personal data still exists in other systems. Build a terminal program that, for each request in `dataset/deletion_requests.csv`, decides whether the deletion is really complete at the case's `audit_at` time, and writes `output.csv`.

## 2. Hard requirements

- Runs from the terminal: `python3 code/main.py`. Reads only `dataset/`. Writes `output.csv` at the repo root.
- Exactly one row per `case_id` in `deletion_requests.csv` (518 rows), sorted by `case_id`.
- Columns exactly: `case_id,deletion_status,steps_json`.
- `deletion_status` is `COMPLETE`, `INCOMPLETE` or `NOT_APPLICABLE`.
- `NOT_APPLICABLE` rows have `steps_json` equal to `[]`.
- Deterministic output. No secrets in code (use environment variables).
- Use only information at or before the case's `audit_at`. Evidence or activity captured later must be ignored.
- Deliver: `README.md` (setup and run), `code/evaluation/evaluation_report.md`, `code/evaluation/usage_report.md`.

## 3. Input files (all under `dataset/`)

| File | Use |
| --- | --- |
| `deletion_requests.csv` | The 518 cases: `case_id,email,requested_at,reported_status,reported_at,audit_at,request_text` |
| `user_identities.csv` | Current identity per user: `user_id,name,email,phone,email_hash,updated_at` |
| `user_audit_log.csv` | History of changes to `email`, `email_hash`, `phone` |
| `system_activity.csv` | Global event stream (about 4,000 rows): deletes, syncs, exports, backups, restores, indexing |
| `deletion_rules.csv` | 15 rules: what treatment each data category needs |
| `evidence_files.csv` | Manifest: `file_id,file_path,origin,captured_at` for files in `dataset/evidence/` |
| `sample_deletion_requests.csv` and `sample_output.csv` | 32 cases with correct answers. Use them to check your work. |

`reported_status` (`COMPLETE`, `PARTIALLY_COMPLETE`, `NOT_STARTED`) and the `reported_result` column in the activity stream are **claims, not proof**. About one in five activity rows is `FAILED`. Establish the real state from evidence.

## 4. Facts already verified about the data (use them)

- `email_hash` is the SHA-256 hex digest of the **lower-cased** email.
- Request emails can differ from the stored email only by case or spaces. Normalize with strip and lower.
- A request email can be a **historical** email. Each field's history in `user_audit_log.csv` is a chain: the first event's `old_value` is the original value, and every `new_value` follows. The last `new_value` equals the current snapshot value. All 518 request emails resolve to a user this way.
- Resource IDs in evidence are **not** derived from `user_id`. For example `user_43` has the account resource `account_7566`. Link them through evidence contents (emails, hashes, phones, source IDs).
- Evidence contains **decoys**: similar people in the same table (for example `jonas.berg@...` next to `jonas.berglund@...` and `joana.berg@...`). Match on exact identifiers or exact resource links, never on similar names.
- `request_text` is written in many languages. Do not rely on English keywords. Some requests are not deletion requests (access, correction, export, marketing opt-out) and must be `NOT_APPLICABLE`. The activity stream has a few `CLASSIFY_REQUEST` rows saying a request was classified as non-deletion; use them as a cross-check, not the only signal. None of the 32 samples is `NOT_APPLICABLE`, so write your own test cases for that.
- Evidence formats: SQL snapshots (SQLite-compatible, can be run with Python's `sqlite3`), logs, JSON, plain text, 4 PDFs and 11 PNG screenshots. SQL files can hold a primary plus regional, reporting, read or failover replicas, so compare tables inside the same file for replication drift. Each SQL file has a "Captured at" time in its header comments.
- Systems that appear in the sample answers: `account_service`, `analytics_platform`, `backup_service`, `export_service`, `identity_service`, `integration_service`, `search_service`, `security_service`, `support_platform`.

## 5. Policy: `deletion_rules.csv` to treatment

| Required action | Remediation operation | Final-state verification |
| --- | --- | --- |
| DELETE | `DELETE_RECORD` (`ABSENT`) | `VERIFY_RECORD_ABSENT` |
| REDACT | `REDACT_RECORD` (`REDACTED`) | `VERIFY_RECORD_REDACTED` |
| ANONYMIZE | `ANONYMIZE_RECORD` (`ANONYMIZED`) | `VERIFY_RECORD_ANONYMIZED` |
| RETAIN | none (do not touch it) | `VERIFY_RECORD_PRESENT` |

Read the `condition` column too: several rules only apply when a condition holds (for example a legal hold is active, a retention period applies, an attachment belongs only to the deletion subject). The account tombstone keeps only the stable `user_id`; name, email, phone and email hash must be removed.

## 6. Decision logic

1. **Is it a deletion request?** If not, `NOT_APPLICABLE` with `[]`.
2. **Resolve the user** from the request email (current or historical) and collect all identifiers held up to `audit_at`: emails, hashes, phones, name.
3. **Find the user's records** in all evidence captured at or before `audit_at`, matching on those identifiers and on grounded resource links.
4. **Reconstruct the state** of each record at `audit_at`, apply the policy to it, and note active data flows (sync, import) and restore risks (backups that can bring data back).
5. **Status:** `COMPLETE` only if every relevant copy already has the required treatment, every required retained record still exists, and no active flow or restore path can recreate prohibited data. Otherwise `INCOMPLETE`.

## 7. Output steps

Each step has exactly these fields: `step_no`, `phase`, `system_id`, `resource_id`, `operation`, `state`, `evidence_id`. Number steps from 1 with no gaps.

| Phase | Operation | State |
| --- | --- | --- |
| FINDING | `REPORT_REMAINING_RECORD` | `PRESENT` |
| FINDING | `REPORT_EXPOSED_PII` | `PRESENT` |
| FINDING | `REPORT_ACTIVE_DATA_FLOW` | `ACTIVE` |
| FINDING | `REPORT_RESTORE_RISK` | `ACTIVE` |
| REMEDIATION | `DISABLE_DATA_FLOW` | `DISABLED` |
| REMEDIATION | `BLOCK_RESTORE` | `BLOCKED` |
| REMEDIATION | `DELETE_RECORD` | `ABSENT` |
| REMEDIATION | `REDACT_RECORD` | `REDACTED` |
| REMEDIATION | `ANONYMIZE_RECORD` | `ANONYMIZED` |
| VERIFICATION | `VERIFY_RECORD_ABSENT` | `ABSENT` |
| VERIFICATION | `VERIFY_RECORD_PRESENT` | `PRESENT` |
| VERIFICATION | `VERIFY_RECORD_REDACTED` | `REDACTED` |
| VERIFICATION | `VERIFY_RECORD_ANONYMIZED` | `ANONYMIZED` |
| VERIFICATION | `VERIFY_DATA_FLOW_DISABLED` | `DISABLED` |
| VERIFICATION | `VERIFY_RESTORE_BLOCKED` | `BLOCKED` |

Do not invent other phases, operations or states.

**Ordering:** phases in order FINDING, REMEDIATION, VERIFICATION. Inside a phase, order by the operation order of the table above, then `system_id` ascending, then `resource_id` ascending.

**Content:**
- `COMPLETE`: only VERIFICATION steps, each citing evidence (a `file_id`, `activity_id` or `audit_id`) that proves the current compliant state.
- `INCOMPLETE`: findings (each citing the evidence that proves it), the full remediation plan, and verification steps for every remediated record or flow plus retained records. Remediation steps and verification steps use `evidence_id` equal to the empty string `""`.
- `NOT_APPLICABLE`: `[]`.
- Never cite organizer-only data or evidence captured after `audit_at`.
- Disable data flows and block unsafe restores before changing dependent records; process downstream copies before their source records when one depends on the other.

Look at `sample_output.csv` for the exact pattern (the account record verification appears in all 32 sample answers).

## 8. Milestones (do one at a time)

1. **Load and identity.** Load all CSVs. Resolve each request email to a `user_id` (current or historical, case-insensitive, limited to `audit_at`). *Accept:* all 518 resolve, and the sample cases map to the same user the sample answers imply.
2. **Request classifier.** Decide deletion vs non-deletion for any language. *Accept:* every sample is a deletion request; add at least 3 of your own non-deletion test texts; list all distinct request texts and read them.
3. **Evidence index.** Parse every evidence file (SQL with `sqlite3`, logs, JSON, text; PDFs and images last) into records with: file_id, system, resource id, identifiers present, and capture time. Respect `audit_at`. *Accept:* for each sample case you can list the records that belong to that user and none that belong to decoys.
4. **State and policy.** For each user, decide the treatment each record needs and whether it already has it; detect active flows and restore risks from activity plus evidence. *Accept:* `deletion_status` matches `sample_output.csv` for all 32 samples.
5. **Step generator and writer.** Produce `steps_json` in the exact order and format above and write `output.csv`. *Accept:* matches the sample steps; the full 518-row run passes the format checks (columns, sorting, valid JSON, consecutive numbering, only allowed operations).
6. **PDFs and images.** Extract facts from the 4 PDFs and 11 PNGs (a PDF text library and OCR, or a vision-capable model with its key in an environment variable). *Accept:* sample cases that depend on them now match.
7. **Scorer.** In `code/evaluation/main.py`: status accuracy, step-level and field-level accuracy, and sequence accuracy against the samples. Add at least 3 custom evaluation cases of your own.
8. **Reports and README.** Write `code/evaluation/evaluation_report.md` (metrics, custom cases, failures found, improvements made), `code/evaluation/usage_report.md` (if no model was called, say 0 calls and 0 tokens; otherwise provider, model names, call counts, input and output tokens, total and average per request, estimated cost), and `README.md`.
9. **Package.** Build `code.zip` as described in the repo README.

## 9. Definition of done

- A clean run produces exactly 518 valid rows in `output.csv`, sorted, in the required column order.
- The sample statuses match, and the report explains any remaining differences.
- No secrets, no `dataset/` in the zip, no hardcoded per-case answers.
- The author can explain every file in plain words.
