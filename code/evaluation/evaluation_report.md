# Evaluation report: Ghost Data Still Exists

All numbers below come from commands run on the final code. The tables are generated from the JSON artifacts those commands write; none were typed by hand. To re-run everything:

```bash
python3 -m unittest discover -s code/tests     # 111 tests
python3 code/evaluation/main.py                # sample scores      -> artifacts/sample_scores.json
python3 code/evaluation/evaluate_status.py     # findings/treatment -> artifacts/sample_status_evaluation.json
python3 code/evaluation/validate_evidence.py   # linkage            -> artifacts/sample_evidence_validation.json
python3 code/evaluation/custom_cases.py        # custom cases       -> artifacts/custom_cases.json
python3 code/main.py && python3 code/evaluation/check_output.py   # 518-row output and independent contract check
```

## A. Methodology

1. **Unit and integration tests (111, all passing).**
   - Integration tests read the real dataset. Their expected values are copied from cited source rows and lines.
   - Edge cases use synthetic fixtures written to temporary folders. Every synthetic value is marked SYNTHETIC or uses `example.test`, and nothing is ever written under `dataset/`.
   - Areas covered: identity history; every evidence format; `audit_at` cutoffs; untimed and malformed timestamps; decoys; parse failures; provenance; rule loading; categories; per-rule assessment; claim-only links; request intent; step ordering; the output contract; behavior when Tesseract is missing; the six custom cases.
2. **Sample comparison.** `code/evaluation/main.py` runs the full pipeline (`code/main.py`) on `dataset/sample_deletion_requests.csv` and compares the result with `dataset/sample_output.csv`. Each step is compared as the tuple (phase, system_id, resource_id, operation, state, evidence_id).
   - *Status accuracy*: share of cases with the expected `deletion_status`.
   - *Step-level precision / recall / F1*: multiset match of step tuples per case. `step_no` is ignored here because it is positional.
   - *Field-level accuracy*: steps aligned by position. Every field of every position is compared, and a missing or extra position counts as a mismatch.
   - *Sequence accuracy*: share of cases whose whole steps array equals the expected one, in order.
3. **Diagnostics behind the scores.**
   - `evaluate_status.py` compares findings (system, resource, kind) and each resource's required treatment with the sample's final verification operation.
   - `validate_evidence.py` checks which expected resources the evidence index links to the person, and why the rest are not linked.
4. **Output contract.** `code/main.py` validates `output.csv` with `ghostaudit/output.py`. `code/evaluation/check_output.py` re-implements the checks without importing the solution, and a test feeds it a corrupted copy to show it can fail.
5. **Reproducibility.**
   - Two consecutive full runs produce byte-identical outputs and artifacts.
   - Fresh `pip install -r requirements.txt` environments on Python 3.11.17, 3.12.3 and 3.13.17 pass the suite and produce the same `output.csv` bytes.
6. **Data discipline.**
   - Production code reads only participant files under `dataset/`. `sample_output.csv` is read only by the evaluation scripts.
   - A search of `code/ghostaudit` and `code/main.py` finds no case IDs (except one docstring example), no reference to sample answers, and no network, LLM or environment-key use.

## B. Sample evaluation (32 public cases)

| Metric | Result |
| --- | --- |
| Cases evaluated | 32 (10 COMPLETE, 22 INCOMPLETE, 0 NOT_APPLICABLE expected) |
| Status accuracy | **100%** (32/32); confusion: COMPLETE→COMPLETE 10, INCOMPLETE→INCOMPLETE 22 |
| Step-level precision | **100%**: 159 of 159 predicted steps are in the answers; no step is invented |
| Step-level recall | **82.4%**: 159 of 193 expected steps |
| Step-level F1 | **90.3%** |
| Field-level accuracy (positional) | **71.7%** over 193 positions: step_no 82.4%, phase 82.4%, evidence_id 82.4%, operation 66.3%, state 66.3%, system_id 61.1%, resource_id 61.1% |
| Sequence accuracy | **31.2%** (10/32 cases exactly equal) |
| Findings (system, resource, kind) | 41/41 expected findings found, 0 extra |
| Required treatment vs the sample's verification operation | 77/77 resources known to both sides agree |
| Expected resources linked by the evidence index | 77/111 |
| Cited evidence found for linked resources | 59/59 (75 cited in total; the other 16 belong to unlinked resources) |
| Cited evidence captured after `audit_at` | 0/75 |

Field-level accuracy is lower than step-level F1 because positions shift. In 20 of the 22 inexact cases the account check is the first verification step and is missing, which moves every later step one position. No predicted step is wrong; steps are only missing.

Per case:

| Case | Expected | Predicted | Expected steps | Matched | Extra | Exact sequence | Missing steps (why) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| case_01 | COMPLETE | COMPLETE | 1 | 0 | 0 | no | V_ABSENT account_001 (timing only) |
| case_02 | COMPLETE | COMPLETE | 1 | 0 | 0 | no | V_ABSENT account_002 (timing only) |
| case_03 | COMPLETE | COMPLETE | 3 | 2 | 0 | no | V_ANONYMIZED analytics_9406 (timing only) |
| case_04 | COMPLETE | COMPLETE | 2 | 0 | 0 | no | V_ABSENT account_004 (not in any participant file); V_ANONYMIZED analytics_901 (timing only) |
| case_05 | INCOMPLETE | INCOMPLETE | 3 | 3 | 0 | yes | - |
| case_06 | INCOMPLETE | INCOMPLETE | 4 | 4 | 0 | yes | - |
| case_07 | INCOMPLETE | INCOMPLETE | 4 | 3 | 0 | no | V_ABSENT account_007 (timing only) |
| case_08 | INCOMPLETE | INCOMPLETE | 4 | 3 | 0 | no | V_ABSENT account_008 (timing only) |
| case_09 | INCOMPLETE | INCOMPLETE | 5 | 3 | 0 | no | V_ABSENT account_3960 (post-audit file only); V_PRESENT audit_record_4902 (post-audit file only) |
| case_10 | INCOMPLETE | INCOMPLETE | 4 | 3 | 0 | no | V_ABSENT account_010 (timing only) |
| case_11 | INCOMPLETE | INCOMPLETE | 14 | 14 | 0 | yes | - |
| case_12 | INCOMPLETE | INCOMPLETE | 5 | 5 | 0 | yes | - |
| case_13 | INCOMPLETE | INCOMPLETE | 8 | 8 | 0 | yes | - |
| case_14 | INCOMPLETE | INCOMPLETE | 5 | 5 | 0 | yes | - |
| case_15 | INCOMPLETE | INCOMPLETE | 5 | 5 | 0 | yes | - |
| case_16 | INCOMPLETE | INCOMPLETE | 7 | 7 | 0 | yes | - |
| case_17 | INCOMPLETE | INCOMPLETE | 5 | 5 | 0 | yes | - |
| case_18 | INCOMPLETE | INCOMPLETE | 14 | 12 | 0 | no | V_ABSENT account_5521 (timing only); V_PRESENT audit_record_6645 (not in any participant file) |
| case_19 | INCOMPLETE | INCOMPLETE | 8 | 8 | 0 | yes | - |
| case_20 | INCOMPLETE | INCOMPLETE | 8 | 6 | 0 | no | V_ABSENT account_5037 (timing only); V_PRESENT audit_record_5569 (not in any participant file) |
| case_21 | COMPLETE | COMPLETE | 3 | 1 | 0 | no | V_ABSENT account_6440 (timing only); V_ANONYMIZED analytics_5335 (timing only) |
| case_22 | INCOMPLETE | INCOMPLETE | 8 | 7 | 0 | no | V_ABSENT account_4990 (timing only) |
| case_23 | INCOMPLETE | INCOMPLETE | 8 | 6 | 0 | no | V_ABSENT account_410 (timing only); V_PRESENT audit_record_4128 (post-audit file only) |
| case_24 | INCOMPLETE | INCOMPLETE | 7 | 6 | 0 | no | V_PRESENT audit_record_7095 (post-audit file only) |
| case_25 | COMPLETE | COMPLETE | 3 | 1 | 0 | no | V_ABSENT account_3136 (timing only); V_ANONYMIZED analytics_1895 (timing only) |
| case_26 | COMPLETE | COMPLETE | 6 | 4 | 0 | no | V_ABSENT account_5684 (post-audit file only); V_ANONYMIZED analytics_4566 (timing only) |
| case_27 | COMPLETE | COMPLETE | 6 | 4 | 0 | no | V_ABSENT account_3866 (post-audit file only); V_ANONYMIZED analytics_4574 (timing only) |
| case_28 | COMPLETE | COMPLETE | 6 | 4 | 0 | no | V_ABSENT account_5482 (timing only); V_ANONYMIZED analytics_6077 (timing only) |
| case_29 | INCOMPLETE | INCOMPLETE | 14 | 12 | 0 | no | V_ABSENT account_7012 (timing only); V_PRESENT audit_record_3954 (post-audit file only) |
| case_30 | INCOMPLETE | INCOMPLETE | 14 | 12 | 0 | no | V_ABSENT account_4163 (timing only); V_PRESENT audit_record_1707 (post-audit file only) |
| case_31 | COMPLETE | COMPLETE | 3 | 2 | 0 | no | V_ABSENT account_4857 (timing only) |
| case_32 | INCOMPLETE | INCOMPLETE | 5 | 4 | 0 | no | V_ABSENT account_8858 (timing only) |

The samples contain no NOT_APPLICABLE case, so that status is evaluated only by the request-intent tests and custom case 4. In the main set the classifier marks exactly `case_533` to `case_542`, each of which says so explicitly (e.g. "This is an information request, not a request to erase it.").

## C. Team-authored evaluation cases

These cases were authored by the team on a **synthetic** fixture. They are not challenge data. `code/evaluation/custom_cases.py` builds the fixture in a temporary directory with a read-only copy of the real `deletion_rules.csv`, runs the real engine and step generator, and compares status plus the complete steps array with the expected result. `code/tests/test_custom_cases.py` runs them in the suite, and also checks that a corrupted expectation is detected as a mismatch (negative control).

Shared fixture settings: every request has `requested_at` 2026-09-01, `reported_status` COMPLETE (a claim only), `audit_at` 2026-09-15T12:00Z and the text "Please permanently delete the account associated with this email address." unless a case says otherwise. The six people are `user_9201` to `user_9206`, with `example.test` emails. Evidence lives in four synthetic files: `SYNTHETIC_checks` (a lookup ledger), `SYNTHETIC_export` (service exports), `SYNTHETIC_bundle` (a snapshot bundle) and `SYNTHETIC_late` (captured after `audit_at`).

Result of the last run: **6/6 PASS**.

### CUSTOM_01_retained_security_record
- **Scenario (synthetic):** The account is gone and a security audit record within its retention period remains.
- **Fixture:** request `sam@example.test` (`user_9202`). `SYNTHETIC_checks` (captured 2026-09-12): `account_9202` USER_PROFILE `NOT_FOUND` for `sam@example.test`. `SYNTHETIC_export` (2026-09-10): `security_service` `audit_record_9202` for `user_9202`, `retention_until` 2033-01-01.
- **Rule basis:** rule_01 deletes USER_PROFILE; rule_12 retains SECURITY_AUDIT_LOG within the retention period (retention_until 2033 > audit_at). COMPLETE cases contain only evidence-backed verification steps; the retained record is verified PRESENT and never deleted.
- **Expected:** `COMPLETE` with 2 steps: VERIFY_RECORD_ABSENT account_9202 @SYNTHETIC_checks, VERIFY_RECORD_PRESENT audit_record_9202 @SYNTHETIC_export
- **Actual:** `COMPLETE` with 2 steps; engine reason: "every linked resource meets its rule at audit_at"
- **Result:** PASS

### CUSTOM_02_historical_email_and_near_match_decoy
- **Scenario (synthetic):** The request uses the person's former email in different case; a search document still holds it, and a decoy document with the same name and a one-letter-different email (rita.olde@) sits beside it.
- **Fixture:** request `"  Rita.Old@Example.Test "`, the former email of `user_9201` (audit row `audit_s9201` changed it to `rita.new@example.test` on 2026-05-01). `SYNTHETIC_checks`: `account_9201` `NOT_FOUND`. `SYNTHETIC_export`: search document `search_9201` with email `RITA.OLD@EXAMPLE.TEST`, and decoy `search_9299` with `rita.olde@example.test` and the same display name.
- **Rule basis:** Identifiers include historical emails up to audit_at and match case-insensitively; names and similar emails never link. rule_03 deletes SEARCH_INDEX_ENTRY, so the remaining document is a finding.
- **Expected:** `INCOMPLETE` with 4 steps: REPORT_REMAINING_RECORD search_9201 @SYNTHETIC_export, DELETE_RECORD search_9201, VERIFY_RECORD_ABSENT account_9201, VERIFY_RECORD_ABSENT search_9201
- **Actual:** `INCOMPLETE` with 4 steps; engine reason: "confirmed unmet requirement(s): REMAINING_RECORD search_service/search_9201"
- **Result:** PASS

### CUSTOM_03_active_flow_and_restore_risk
- **Scenario (synthetic):** The account is gone, but a connector for the person is still ACTIVE and a backup still allows restore; a workflow claim says the connector was disabled.
- **Fixture:** request `tess@example.test` (`user_9203`). `SYNTHETIC_checks`: `account_9203` `NOT_FOUND`; `data_flow_9203` DATA_FLOW `ACTIVE` for `tess@example.test`; `backup_9203` BACKUP_ARCHIVE `ACTIVE` for `user_9203`. Activity row `SYNTHETIC_act_1` (2026-09-05): `DISABLE_DATA_FLOW` on `data_flow_9203`, reported `SUCCESS`.
- **Rule basis:** No active sync or restore path may remain (problem statement). rule_14 retains BACKUP_ARCHIVE only with restore blocked. The claim SYNTHETIC_act_1 is not proof. Remediation disables the flow and blocks the restore first, in the operation order of the specification.
- **Expected:** `INCOMPLETE` with 7 steps: REPORT_ACTIVE_DATA_FLOW data_flow_9203 @SYNTHETIC_checks, REPORT_RESTORE_RISK backup_9203 @SYNTHETIC_checks, DISABLE_DATA_FLOW data_flow_9203, BLOCK_RESTORE backup_9203, VERIFY_RECORD_ABSENT account_9203, VERIFY_DATA_FLOW_DISABLED data_flow_9203, VERIFY_RESTORE_BLOCKED backup_9203
- **Actual:** `INCOMPLETE` with 7 steps; engine reason: "confirmed unmet requirement(s): RESTORE_RISK backup_service/backup_9203, ACTIVE_DATA_FLOW integration_service/data_flow_9203"
- **Result:** PASS

### CUSTOM_04_non_deletion_request
- **Scenario (synthetic):** The person asks for a copy of their data and explicitly declines deletion, although a search document for them exists.
- **Fixture:** request `uma@example.test` with the text "Could you send me a copy of my data? Please don't delete or change anything." `SYNTHETIC_export`: search document `search_9204` with `uma@example.test`.
- **Rule basis:** A request for access or export is NOT_APPLICABLE with steps_json [] (AGENTS.md 6.5), whatever data exists.
- **Expected:** `NOT_APPLICABLE` with 0 steps: `[]`
- **Actual:** `NOT_APPLICABLE` with 0 steps; engine reason: "the request explicitly declines deletion or asks to keep the account"
- **Result:** PASS

### CUSTOM_05_evidence_after_audit_at_is_ignored
- **Scenario (synthetic):** The account was confirmed absent before audit_at; a file captured three weeks after audit_at shows a search document for the person.
- **Fixture:** request `vic@example.test` (`user_9205`). `SYNTHETIC_checks` (2026-09-12): `account_9205` `NOT_FOUND`. `SYNTHETIC_late` (captured 2026-10-05, after `audit_at`): search document `search_9205` with `vic@example.test`.
- **Rule basis:** Only information available at or before audit_at decides the case, and later evidence is never cited (AGENTS.md 6.2, 6.6).
- **Expected:** `COMPLETE` with 1 steps: VERIFY_RECORD_ABSENT account_9205 @SYNTHETIC_checks
- **Actual:** `COMPLETE` with 1 steps; engine reason: "every linked resource meets its rule at audit_at"
- **Result:** PASS

### CUSTOM_06_tombstone_still_exposes_identifiers
- **Scenario (synthetic):** The account is gone, but its deleted-account reference still carries the person's name and email.
- **Fixture:** request `wen@example.test` (`user_9206`). `SYNTHETIC_checks`: `account_9206` `NOT_FOUND`. `SYNTHETIC_bundle` (2026-09-11): `identity_service` collection `deleted_customer_references`, record `tombstone_9206` with `subject_reference` `user_9206`, `account_status` DELETED, and the name and email still filled in.
- **Rule basis:** rule_15: an ACCOUNT_TOMBSTONE keeps only the stable user reference; name and email must be redacted, so the exposed identifiers are a finding and the fix is REDACT_RECORD.
- **Expected:** `INCOMPLETE` with 4 steps: REPORT_EXPOSED_PII tombstone_9206 @SYNTHETIC_bundle, REDACT_RECORD tombstone_9206, VERIFY_RECORD_ABSENT account_9206, VERIFY_RECORD_REDACTED tombstone_9206
- **Actual:** `INCOMPLETE` with 4 steps; engine reason: "confirmed unmet requirement(s): EXPOSED_PII identity_service/tombstone_9206"
- **Result:** PASS

## D. Error analysis and improvements

### Remaining mismatches (all are missing steps; no step is wrong or extra)

| Why the step is missing | Steps | Cases |
| --- | --- | --- |
| The resource is tied to the person only by deletion-worker activity near the request time, with no identifier on any record of it | 23 | 01, 02, 03, 04, 07, 08, 10, 18, 20, 21, 22, 23, 25, 26, 27, 28, 29, 30, 31, 32 |
| An identifier link exists only in a file captured after `audit_at` | 8 | 09 (2), 23, 24, 26, 27, 29, 30 |
| No participant file mentions the resource ID at all | 3 | 04 (`account_004`), 18 (`audit_record_6645`), 20 (`audit_record_5569`) |

**Root cause.** Linking deliberately uses only exact identifiers and resource-ID hops. Timing association and post-audit files were ruled out to avoid decoy matches and to respect the `audit_at` rule (AGENTS.md 6.5, 6.6). The cost is the 34 missing verification steps. The status was unaffected in all 32 cases, because none of those resources is in violation.

**Where the sample answer cannot be derived from participant evidence.** This is not a claim that the samples are wrong; these resources simply cannot be reached from the files a participant receives:
- **`case_04` cites `evidence_54`** for `VERIFY_RECORD_ABSENT account_004`. `evidence_54` (`customer_accounts_failover_replica_20260902.sql`) contains no row, ID or identifier for `account_004` or `user_04`, and a search of every participant file finds the string `account_004` nowhere.
- **`case_26`** expects `VERIFY_RECORD_ABSENT account_5684` citing `evidence_92`, where `account_5684` appears only as a 404 lookup without identifiers. The only file tying `account_5684` to `almeida.mariana@gmail.com` is `evidence_02`, exported 2026-09-17, after the case's `audit_at` of 2026-09-15T12:00Z.
- **`case_18` and `case_20`** expect retained security records (`audit_record_6645`, `audit_record_5569`) that appear in no participant file.

### Improvements made during development (measured before → after)

1. **Status, case_12 (31/32 → 32/32).** `analytics_9914` is observed only as a contact lookup with `email_opt_in=true`, plus the hash of the person's former email. Treating a marketing-consent record as `MARKETING_PROFILE` (rule_10, delete) replaced an unknown analytics state with a confirmed remaining record.
2. **Steps, case_17 (1 extra step → 0).** `account_6523` was evidenced only by a March lookup made for the same person's earlier request (`case_46`). Resources whose evidence all lies in an earlier request's window of the same person are now set aside. A first, broader version also dropped `case_34`'s own security record, so it was narrowed. In the whole data it now sets aside exactly 2 resources.
3. **Claim-only decoy (main `case_63`).** A data-catalog claim attributed `marketing_001` to `user_09`, but the screenshot shows another person (`sj.park@naver.com`). Claim-only links contradicted by the record's own content are now excluded.
4. **Activity records split resources.** Activity rows keyed by their target system (e.g. `account_failover_replica`) produced duplicate resources. Keying them by the resource's own system cut extra linked resources in the linkage check from 6 to 3, all genuine identifier links.
5. **Defects caught by tests:**
   - timestamps with offsets were not normalized to UTC;
   - rule_11's condition text ("Log contains a user email address") did not match its evaluator, which would have left the condition unevaluated;
   - an unclosed SQLite connection.
6. **Silent divergence without OCR.** A run without Tesseract changed 5 main-set cases (3 status flips) without any warning. `code/main.py` now stops with exit code 3 and lists the unparsed files, unless `--allow-parse-failures` is given (tested).
7. **Exposure from content only.** Lookup keys and correlation keys say how a record was found, not what it still contains. Excluding them prevents false "exposed PII" findings on records that only matched through a lookup.

### Known limitations

- **Screenshots.** Status labels on screenshots are on different OCR lines from the resource IDs, so the states of 4 main-set resources stay unknown: `case_39`, `case_53`, `case_55` and `case_61`. Their cases are COMPLETE with an "unverified" note in the audit trail. No sample covers screenshots, so no screenshot interpretation could be validated.
- **Sample-shaped rules.** Four rules were shaped while studying sample cases: the `email_opt_in` field rule (case_12), PDFs held for a support case as attachments (case_07), legacy contact-index entries as secondary identifiers (case_10), and the earlier-request scoping (case_17). Each rests on evidence content, but the sample scores overstate generalisation.
- **Main set.** 349 INCOMPLETE, 159 COMPLETE (51 verified, 108 unverified) and 10 NOT_APPLICABLE. Six COMPLETE cases output `[]` because nothing provable can be verified. Nine main cases carry explicit ambiguity notes in `artifacts/case_audits_main.json`.
