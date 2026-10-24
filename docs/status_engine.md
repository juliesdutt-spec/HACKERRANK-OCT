# Deletion-status engine

The engine combines four modules:
- `code/ghostaudit/requests.py` decides request intent;
- `code/ghostaudit/policy.py` applies the rules;
- `code/ghostaudit/audit.py` decides each case;
- the evidence index from `docs/evidence_index.md` supplies the linked evidence.

```text
python3 code/audit_cases.py                       # samples -> artifacts/case_audits_sample.json
python3 code/audit_cases.py --set main            # 518 requests -> artifacts/case_audits_main.json (intermediate, not output.csv)
python3 code/audit_cases.py --case case_12        # print one case's full audit
python3 code/evaluation/evaluate_status.py        # sample comparison -> artifacts/sample_status_evaluation.json
python3 -m unittest discover -s code/tests        # all tests
```

## 1. Request intent

All 398 distinct request texts were inspected:

| Group | Requests | What they contain |
| --- | --- | --- |
| English templates | 453 | One of 12 deletion sentences, then neutral scope and closing sentences |
| Foreign-language templates | 85 | Fixed deletion texts in Portuguese, French, Japanese, Spanish and Korean |
| Samples | 32 | Free-form, each asking for a deletion or a deletion check |
| Non-deletion | 10 (`case_533` to `case_542`) | An explicit refusal, e.g. "not a request to erase it", "Do not close the account", "please keep the account as it is" |

The classifier is deterministic:
1. An explicit refusal or keep-the-account statement means **NOT_APPLICABLE**.
2. Otherwise, a deletion term in any observed language means **DELETION**. This includes asking to confirm an outcome, e.g. "the profile is gone".
3. Otherwise the request is **AMBIGUOUS**. It is audited as a deletion and flagged, because NOT_APPLICABLE needs evidence that deletion was not requested.

On the data, the result is 10 NOT_APPLICABLE (exactly `case_533` to `case_542`), 508 deletion requests in the main set, all 32 samples as deletion requests, and 0 ambiguous.

## 2. Rules and categories

- `deletion_rules.csv` is loaded as is: one rule per category.
- Each condition is evaluated by a function registered for its **exact text**. A changed condition reports `unevaluated` instead of being guessed. A test caught one wording mismatch this way (rule_11).
- Retention conditions are checked against `retention_until` and `retained_until` (all 2031 to 2034).

A resource's category is resolved in this order:
1. An explicit `record_type` in the evidence.
2. A defining field: `email_opt_in` means MARKETING_PROFILE.
3. The collection or log event it was observed in (`CATEGORY_BY_COLLECTION`). Each entry is marked either "learned", meaning the same resources carry that explicit `record_type`, or "named", meaning the collection name and fields state the category.
4. A PDF document held for a support case means SUPPORT_ATTACHMENT.
5. The resource-ID prefix, learned from explicit record types. The `support` prefix is ambiguous and is never used.
6. Documented prefixes `tombstone` and `marketing`.
7. Otherwise the category is unresolved.

Connectors are assessed as recreation paths (DATA_FLOW). They are not a rule category.

**Disclosure.** Three mappings were set while looking at sample cases, so they are not independent of the sample evaluation:
- the `email_opt_in` field rule (case_12);
- a PDF held for a support case as an attachment (case_07);
- legacy-contact-index entries as SECONDARY_IDENTIFIER (case_10).

Each mapping rests on evidence content, but the 32/32 score below should be read with that in mind.

## 3. State at audit_at and compliance

How a resource's state at `audit_at` is established:
- **Visibility.** Only records that pass the evidence index's cutoff count. Workflow claims never set state.
- **Channels.** Observations are split into storage channels: primary versus each named replica (`_store_role` from the table name). Within each channel the latest observation wins. A replica copy is therefore not hidden by a later lookup against the primary.
- **Later snapshots.** A later complete snapshot of the same table, store role and system that does not contain the resource counts as absence in that channel. The samples cite such snapshots as proof of absence, for example `evidence_54`.
- **Exposure.** Exposure is computed from record **content** only. Lookup keys, correlation keys and the subject of a ledger check say how a record was found, not what it still contains.

| Required action | Violation | Compliant | Otherwise |
| --- | --- | --- | --- |
| DELETE | any channel still holds a copy (including a document or screenshot) | every channel absent | UNKNOWN |
| REDACT | content holds email, hash, phone or name | content without them (stable `user_NN` allowed) | UNKNOWN (absent is flagged) |
| ANONYMIZE | content attributable (direct identifier or `user_NN`) | content not attributable, or absent | UNKNOWN |
| RETAIN | record absent | record present | UNKNOWN |
| RETAIN, "Restore access can be blocked" | restore allowed | restore blocked | UNKNOWN |
| DISABLE_FLOW (connectors) | enabled | disabled | UNKNOWN |

**Claim-only links.** A resource linked to the person only through an activity claim (e.g. `LINK_RESOURCE_IDENTITY`) is marked `claim_only`. If that resource's own evidence content names other direct identifiers, the link is contradicted and the resource is excluded. Example: `marketing_001` is claimed for `user_09`, but the screenshot shows `sj.park@naver.com`.

## 4. Status

| Status | When | Confidence |
| --- | --- | --- |
| NOT_APPLICABLE | the request explicitly declines deletion | high |
| INCOMPLETE | at least one confirmed violation | `high`, or `medium` when every violation rests on a claim-only link |
| COMPLETE | no violation | `verified` only when the account is confirmed absent and nothing is UNKNOWN; otherwise `unverified`, with the gaps listed |

Missing or inconclusive evidence never makes a case INCOMPLETE. The reported workflow status is kept in the output as a claim and never read.

## 5. Results (latest run)

**Samples (32 cases):**
- Status: 32/32 (10 COMPLETE, 22 INCOMPLETE).
- Findings: 41/41 expected findings (system, resource, kind) found, with 0 extra.
- Treatment: the required action agrees with the sample's final verification on 77/77 resources both sides know.

**Main set (518 requests):** 350 INCOMPLETE (347 high, 3 medium), 158 COMPLETE (51 verified, 107 unverified), 10 NOT_APPLICABLE.
- 106 of the 107 unverified COMPLETE cases have no account resource linked by identifiers visible at `audit_at`. This is the same linkage gap described in `docs/evidence_index.md`.
- 9 main cases carry explicit ambiguity notes. All involve screenshot evidence or claim-only links:
  - `case_39`: connector state not readable from OCR.
  - `case_53`: support record category unresolved.
  - `case_55`: backup restore setting not shown.
  - `case_61`: storage object named only by file name.
  - `case_46`: tombstone known only from a claim.
  - `case_33`, `case_35`, `case_52`: findings rest on claim-only links.
  - `case_63`: contradicted claim excluded.

## 6. Limitations

- **Screenshots.** The state of a resource on a screenshot can only be read when it sits on the same OCR line, which most status labels (Healthy, Ready, Retained reference) do not. Those resources stay UNKNOWN. A layout-aware reader or a vision model would be needed, and no sample covers screenshot cases, so neither could be validated here.
- **Linkage.** Resources linkable only through workflow time windows, or only through files captured after `audit_at`, are not linked (see `docs/evidence_index.md`). This lowers confidence but has not changed a sample status.
- **Untested category.** The samples contain no NOT_APPLICABLE case, so that category is checked only against the request texts themselves.
