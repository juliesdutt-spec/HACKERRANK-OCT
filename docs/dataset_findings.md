# Dataset findings (working notes)

Facts established by inspecting participant-facing files only. They drive the design of `code/ghostaudit/`.

## Identity

- `email_hash` = SHA-256 hex of the lower-cased email.
- `user_audit_log.csv` holds unbroken change chains for `email`, `email_hash` and `phone`; the last `new_value` equals the current snapshot. No name changes are logged.
- All 518 request emails resolve to a user at their `audit_at`. 20 differ from the stored value only by case or whitespace.
- Resource IDs are not derived from `user_id` (for example `user_43` owns `account_7566`); links come from evidence contents.

## Request set

- 518 evaluation cases (`case_100` style IDs), disjoint from the 32 samples (`case_01` to `case_32`); 2 emails appear in both sets.
- 72 distinct `audit_at` values, about 10 cases each, so a batch of cases shares the same snapshot evidence (as samples 21 to 30 all cite `evidence_92`).
- `request_text` is multilingual (EN, ES, FR, PT, KO, JA and others); English keyword rules miss deletion requests.
- 10 `CLASSIFY_REQUEST` activity rows from `privacy_intake` mark a request (by account resource) as non-deletion.

- Request intent (all 398 distinct texts inspected):
  - 453 requests use one of 12 English deletion sentences, plus a neutral scope and closing sentence;
  - 85 use fixed deletion templates in Portuguese, French, Japanese, Spanish and Korean;
  - exactly 10 (`case_533` to `case_542`) are explicit non-deletion requests: correction, opt-out, export, access, login help, invoice copies, email change.
- The 10 `CLASSIFY_REQUEST` claims could be linked by identifiers to only 1 of those 10 cases.
- `LINK_RESOURCE_IDENTITY` claims (12) each tie a screenshot or document resource to one main-set case audited about a day after the capture. `activity_4008` claims `marketing_001` for `user_09`, but the marketing console shows Soo-jin Park, `sj.park@naver.com`: a contradicted claim.

## Evidence (94 files)

All files share one underlying unit: system, resource ID, record type, state, an identifier (email, hash, `user_NN` reference or phone) and a time. Encodings:

| Kind | Count | Shape |
| --- | --- | --- |
| SQL snapshots | 24 | Three layouts: typed tables (`customer_profiles`, `customer_search_documents`, `support_records`, `connector_subscriptions`, `backup_restore_entries`, `security_audit_records` and so on, plus a `snapshot_scope` table), a generic `customer_data_primary` table with `record_json`, and `*_primary` vs `*_replica` table pairs for drift. "Captured at" is in the header comment or `snapshot_scope`. |
| Logs | 26 | Several syntaxes (key=value, syslog, JSON per line, bracketed, pipe-separated, cloud `message="..."`). Lookup pairs: a `*_started` line carries `lookup_key`, the `*_completed` line carries `resource_id` and result (`rows=0` / `http_status=404` means absent). Some logs are flat `record_lookup` lines with `system`, `resource_id`, `record_type`, `result`, `subject_reference`. |
| JSON records | 28 | Snapshot bundles (`snapshots[].records[]`), service resource exports (`service_exports.<system>`), audit-style ledgers (`records[]` with `system, resource_id, record_type, result, subject_reference`), export jobs, analytics reports. |
| Text | 1 | Shift handover with pipe-separated rows in the same ledger shape. |
| PDF | 4 | Text-extractable with `pdftotext` or `pypdf`: customer statement, support attachment, legal retention register, security retention extract. |
| PNG | 11 | Rendered admin screens; local Tesseract OCR reads emails, `user_NN` and resource IDs exactly; phone spacing can drift, so compare phones on digits. |

Observed states include `FOUND`, `NOT_FOUND`, `IDENTIFIABLE`, `ANONYMIZED`, `REDACTED`, `ACTIVE`, `DISABLED`, `BLOCKED`. Near-duplicate decoys sit in the same tables (for example `jonas.berg@...` next to `jonas.berglund@...`), so matching must be exact.

## Activity stream

- 4,011 rows; 721 `FAILED` and 376 `PARTIAL`. `deletion_worker` rows cluster near each case's `reported_at`, but windows of different cases interleave, so time alone cannot link a resource to a case.
- `LINK_RESOURCE_IDENTITY` rows (12) explicitly link a resource to an email and `user_NN`.
- `SYNC_RECORD` / `RESTORE_BACKUP` rows such as "Active connector repopulated downstream resource search_4801" describe recreation paths.

## Sample answer pattern (32 cases: 22 INCOMPLETE, 10 COMPLETE, 0 NOT_APPLICABLE)

- Every answer has exactly one `VERIFY_RECORD_ABSENT` on the user's `account_service` record.
- Findings list only non-compliant records; verification covers every relevant record in its required final state, including the retained `security_service` audit record (`VERIFY_RECORD_PRESENT`).
- `COMPLETE` answers cite the snapshot that shows the compliant state, often one file for every step. Absence is proven by a snapshot of the table that lacks the record (for example `evidence_54` for `account_001`).
- Treatment depends on record type: an `analytics_platform` record can need `DELETE_RECORD` in one case and `ANONYMIZE_RECORD` in another.
- A few early samples (for example `account_004` in case_04) reference resource IDs that appear in no participant file, so a grounded solution cannot reproduce them exactly.

## Evidence index findings (see docs/evidence_index.md)

- In all 83 structured files, the in-file capture time equals the manifest `captured_at`. Later timestamps inside files are attribute values such as `expires_at`, `retention_until` and `retained_until`.
- Every evidence ID cited in the 32 sample answers was captured at or before that case's `audit_at` (0 of 75 later), which supports the inclusive file-capture cutoff.
- `evidence_13` and `evidence_36` each contain a ledger record dated two days after the file's own export. In `evidence_13`, `account_1417` is NOT_FOUND at the export time and FOUND two days later.
- All 2,251 log lines match one of six syntaxes. Lookup lines (`*_started`, carrying `lookup_key` and similar) and state lines share a `trace_id`. No trace looks up more than one identifier.
- Matching all 550 requests: 213 identifier matches rely only on a historical email or hash. No matched record carries an identifier owned by another user.
- Resource-ID prefixes map one-to-one to systems (account, analytics, audit_record, backup, data_flow, export, legal_hold, search, support, tombstone).
