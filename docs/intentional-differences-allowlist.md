# Intentional differences allowlist

This file tracks reviewed differences between `medterm4ds` and legacy
`/mnt/d/medterm` behavior. It is part of the parity/quality gate and should be
updated whenever a mismatch is accepted as non-regression.

Machine-readable classifications for benchmark reports can be maintained in
[`intentional-differences-allowlist.csv`](intentional-differences-allowlist.csv)
and passed to benchmark or sampled parity scripts:

```bash
python scripts/compare_patient_friendly_benchmark.py \
  --classification-csv docs/intentional-differences-allowlist.csv

python scripts/compare_patient_friendly_parity.py \
  --classification-csv docs/intentional-differences-allowlist.csv

python scripts/run_patient_friendly_parity_matrix.py \
  --classification-csv docs/intentional-differences-allowlist.csv
```

Every accepted difference must include:

- `scope`: source/workflow affected.
- `classification`: one of `intentional_fix`, `release_drift`, `loader_data_issue`, or `acceptable_display_only_difference`.
- `rationale`: why the legacy behavior should not be reproduced.
- `evidence`: report, test, or code reference used for review.

## Active allowlist

| Scope | Classification | Rationale | Evidence |
| --- | --- | --- | --- |
| CPT patient-friendly display | `acceptable_display_only_difference` | Legacy atom ordering can select all-caps synonyms or long technical procedure names. `medterm4ds` uses deterministic CPT display ordering that prefers consumer-facing `ETCF`, `ETCLIN`, `PT`, then `SY`. | `docs/parity-matrix.md`; CPT source-strategy tests. |
| Historical/obsolete code handling | `intentional_fix` | `medterm4ds` can resolve obsolete or historical inputs before lookup/patient-friendly naming instead of treating every input as active. | Resolution tests and public `resolve_mode` support. |
| NDC to RxNorm resolution | `intentional_fix` | `medterm4ds` normalizes NDC formats and resolves to active RxNorm candidates where available before downstream workflows. | NDC resolution tests and notebook/API examples. |
| Structured provenance | `intentional_fix` | `medterm4ds` emits structured `matched_via` paths for lookup, mapping, ConceptMap, and patient-friendly outputs. Legacy output often used looser dictionaries or omitted path details. | ConceptMap/output tests and model schema tests. |
| SNOMED patient-friendly target routing | `intentional_fix` | SNOMED patient-friendly resolution walks target-source hierarchies first in priority order `ICD10CM`, `ICD10PCS`, `LNC`, `CPT`, `HCPCS`, and does not route through RxNorm. This prevents drug/ingredient concepts from becoming generic patient-friendly labels for unrelated SNOMED findings. | `docs/plans/terminology-normalization-implementation-plan.md`; source-strategy tests; raw fallback target-list updates. |
| Synthetic ICD prefix hierarchy removal | `intentional_fix` | ICD/CPT/HCPCS/LOINC hierarchy walking uses UMLS/prepared hierarchy edges only. Prefix/range inference is not used for patient-friendly naming, mapping, walking, or optimize behavior. | `docs/architecture.md`; `docs/terminology-architecture-requirements.md`; prepared hierarchy tests. |
| RxNorm patient-friendly implementation | `intentional_fix` | RxNorm patient-friendly naming uses bounded prepared TTY path tables and active-target ranking instead of broad raw graph recursion. | RxNorm TTY walk tests and materialization candidate tests. |

## Not allowlisted

These classifications are not acceptable as final unresolved differences:

- `algorithm_bug`
- unclassified mismatch
- missing DB role/release/schema metadata
- mismatch caused by comparing unlabeled UMLS releases
- unrelated broad MEDLINEPLUS/CHV labels selected over original source display

Any report containing these must return to development or receive an explicit
review decision before release.
