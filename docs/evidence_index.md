# Evidence index (`code/ghostaudit/evidence/`)

Turns every evidence file in `dataset/evidence_files.csv`, plus `system_activity.csv`, into one list of `EvidenceRecord`s and `SnapshotScope`s. A record keeps its provenance: the citable `evidence_id`, the file path, and the location inside the source. The index answers one question for the next stage: which records, visible at `audit_at`, belong to this person.

```text
python3 code/index_evidence.py                     # writes artifacts/evidence_parse_summary.json
python3 code/index_evidence.py --trace case_06     # request -> identity -> matched evidence
python3 code/evaluation/validate_evidence.py       # sample comparison -> artifacts/sample_evidence_validation.json
python3 -m unittest discover -s code/tests         # identity and evidence tests
```

Dependencies: `pip install -r requirements.txt` (pypdf, Pillow) and the Tesseract binary for screenshots. Without Tesseract, every PNG is reported as `failed` with the reason; nothing is skipped silently.

## Record schema

| Field | Meaning |
| --- | --- |
| `evidence_id` | Manifest `file_id`, or the `activity_id` for activity rows |
| `location` | Table and row, JSON path, log line (with the linked lookup line), PDF page and lines, or OCR line |
| `system_id`, `system_basis` | Explicit in the source, else from the resource-ID prefix, else from the collection, else the log's emitting service. Prefix and collection maps are learned only from records that name their system, and ambiguous keys are dropped. |
| `resource_id`, `collection`, `record_type` | `record_type` only when the source names a category such as `USER_PROFILE` |
| `state`, `state_basis` | Normalized state and the source value that set it (see below) |
| `observed_at`, `time_basis`, `file_captured_at` | Observation time, where it came from, and the manifest capture time |
| `identifiers` | `(kind, value)` with kind `email`, `email_hash`, `user_ref`, `phone` or `name` |
| `related_resources` | Other resource IDs the record points at (for example `source_resource_id`) |
| `extraction` | `structured`, `pdf_text` or `ocr` |
| `is_claim` | True for activity rows: they report what a workflow claimed and never set a state |

A `SnapshotScope` records which resource IDs a table or collection held when it was captured. The index never concludes deletion from a missing ID. Scopes from inventories whose `snapshot_scope` does not list the system have `coverage_confirmed = False`.

## States

A state is set only by an explicit source value. `derive_state` applies these rules in order:

| Source value | State |
| --- | --- |
| `result` FOUND, hit / NOT_FOUND | PRESENT / ABSENT |
| `result` IDENTIFIABLE, ANONYMIZED, REDACTED, ACTIVE, DISABLED, BLOCKED | the same value |
| `rows` 0 / ≥1, `http_status` or `status_code` 404 / 200 | ABSENT / PRESENT |
| `enabled` true / false | ACTIVE / DISABLED |
| `restore_allowed` true / false, `decision` allow / deny | ACTIVE / BLOCKED |
| `eligible` with `deletion_block` | ACTIVE unless blocked |
| `direct_identifiers` > 0 / 0, `detector=direct_identifier` | IDENTIFIABLE / ANONYMIZED, IDENTIFIABLE |
| `marker_found=false`, `available=true` | PRESENT |

Other cases:
- A row in a snapshot, listing or search hit with none of the values above is PRESENT (`row_present`, `listed_in_*`, `search_hit`).
- A log event ending in `_read_completed`, `_read` or `_loaded` returns the record's fields, so it is PRESENT (`event=<name>`).
- PDFs and screenshots state no status. Their records are MENTIONED.
- Anything else keeps `state = None`.
- An unmapped value keeps `state = None` and adds a warning.

## Time rule (audit_at cutoff)

1. All timestamps are converted to UTC. Zone-less or malformed values are rejected rather than guessed.
2. Observation time, in order of preference:
   - the record's own `recorded_at` or `observed_at`;
   - the log line time;
   - the snapshot section's `captured_at`;
   - the per-system time in an inventory's `snapshot_scope`;
   - the file's own header (`Captured at`, `snapshot_completed_at`, `exported_at`, `captured_at`, `Snapshot:`).

   PDFs and screenshots have no machine-readable capture time, so they use the manifest `captured_at`. In all 83 structured files, the in-file header equals the manifest capture time. Attribute dates such as `created_at`, `updated_at`, `expires_at` and retention dates never count as observation times. File modification times are never used.
3. A record is visible at `audit_at` only if its observation time **and** its file's manifest capture time are both at or before `audit_at` (inclusive).
4. If a record's own time field is malformed, the record gets no time; it does not fall back to the file time. A record with no usable time can never establish state.

Two consequences, both tested:
- A bundle section captured on 09-09 inside a file exported on 09-17 is invisible at 09-15.
- `evidence_13` has a record dated two days after its own export. It stays invisible until its own time, and the file is flagged `partial`.

## Matching rule

`EvidenceIndex.match(identifiers, audit_at, owners)` works in four steps:

1. **Keys.** Build the person's keys from `IdentityIndex.identifiers(user, audit_at)`: every email (lower-cased), every email hash, the `user_id`, and every phone (digits only, since OCR and the sources space numbers differently).
2. **Direct matches.** A visible record whose identifiers contain one of those keys is a direct match (`basis = identifier`). Names never link.
3. **One hop of expansion.** The expansion covers:
   - other visible records of the same resource IDs (`resource_id`);
   - resources a direct match points at (`related_resource`);
   - records that point at a linked resource (`references_linked_resource`).
4. **Exclusions.** Timestamps alone never link. `foreign_identifiers` lists any identifier on a matched record that belongs only to another user. Across all 550 sample and evaluation requests there are none.

## Known limitations

- Screenshot OCR uses a 2x grayscale upscale, which read every tested identifier exactly. It can still mangle tokens: in `evidence_42`, `user_17` comes out as `user 17`, so that screen yields no record. `evidence_46` (object storage) shows only file names. Both are reported as `partial`.
- `marketing_001` (OCR, `evidence_60`) has a prefix never seen in structured data, so its system stays unresolved.
- On the 32 samples, 34 of 111 expected resources are not linked. See `artifacts/sample_evidence_validation.json`:
  - 23 resources appear only in identifier-free records plus deletion-worker activity near the request time.
  - 8 resources are linked by identifiers only in files captured after `audit_at`.
  - 3 resources appear in no participant file at all.

  Linking the first two groups would need time-window association or post-audit evidence. Both are deliberately left out of this stage.
