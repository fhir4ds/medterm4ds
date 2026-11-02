
## 2026-10-28 iteration 106 — TS-98 CI-guard quality (maint/spec-comp-20261028)

- Survey: the v0.0.7 CI-portability guards as a META-surface — do any
  hide real regressions? Census: local 16 skips (all pre-existing
  classes; ZERO new guards fire locally — full env, full suite); CI
  178 (release run) → ~162 tests CI-skip = 2.0% of the suite. CI
  green now under-specifies; the LOCAL gate is load-bearing.
- GQ1 (LOW, informational): the posture recorded as a decision —
  mitigation pinned: guards probe HEALTH (503/empty/overlay codes)
  not directory presence, so an environment DEGRADATION cannot
  masquerade as mere absence... it CAN still skip (the residual
  risk, named in the row).
- CONTROLS fresh: the local skip census classified; the conftest
  athena-mini fixture builds; no suite references the machine-local
  path (the guard's own docstring self-match caught + excluded).
- Suite test_ts98_guard_quality_20261028.py: 5 tests green.
- Gates FRESH this run: conformance 8037/16sk (20:42), unit 859/2sk
  (6:06 — count moved: the release's unit split includes the guard
  suite), ruff clean. Zero production changes.
- NOTE: dev synced from main post-v0.0.7 fold before this iteration.

## 2026-10-28 iteration 107 — TS-99 $extract filter interaction (maint/spec-comp-20261028b)

- Survey: the two $extract filters TOGETHER (annotationFields ×
  resultTypes — each swept alone by TS-36/TS-13; the interaction
  never probed).
- ZERO FINDINGS — 14th clean controls surface. The combination is
  exactly compositional: resultTypes filters FIRST (diabetes
  excluded; only the RXNORM metformin resolves), annotationFields
  reshapes the SURVIVING markers' field values (status/canonical_id
  present); no cross-domain leak under filtering; the unfiltered
  baseline confirms both concepts resolve without the filter.
- Suite test_ts99_extract_filter_interaction_20261028.py: 3 tests
  green (2:13, warm-pipeline fixture amortizes the ~180s GLiNER
  load).
- Gates: unit 850/11sk (14:59). CONFORMANCE GATE LESSON: the first
  full run (33:12 — machine thrashing under MY OWN parallel unit run
  + relaunch) showed 2 extract-mimetype failures; both re-verified
  PASSING serially (182s/226s) — resource-contention flakes from
  running two full suites + GLiNER simultaneously, not code. Never
  run the conformance and unit gates in parallel on this box (the
  third serial-pass evidence recorded).
- Zero production changes. No registry edit.

## 2026-10-30 — TS-104 batch Bundle envelope shape (worktree maint/spec-comp-20261030b)

- Surface: $batch Bundle-level SHAPE contract (R4 §3.7) — order,
  cardinality under isolation, empty batch, transaction-response
  mirroring, string status, non-list/null entry envelopes. The
  dispatcher param sweeps (ts44/48/95/96/97) never probed the
  envelope itself.
- RESULT: ZERO FINDINGS — controls-only (17th clean surface). 8/8
  fresh probes pass: order P/P/OO/P preserved; cardinality exact;
  empty batch → 200 no fabricated entries; transaction-response
  mirrored; response.status string on success AND failure; entry
  object/null → 400 OperationOutcome.
- OBSERVATION (pinned contract, not a finding): unknown-code
  $lookup carries HTTP 200 + OperationOutcome body per cases.json
  'lookup-invalid-code'; batch mirrors direct faithfully.
- Gates (fresh): conformance 8061 passed / 0 failed / 19 skipped
  (27:10 serial); unit 850 passed / 11 skipped (7:18); ruff clean +
  formatted on the new file. ts69's 9 failures were WORKTREE-ENV
  (missing .venv), fixed by the standard symlink — pre-existing on
  the base commit, verified via stash.

## 2026-10-30 — TS-105 athena bridge identity-vocab equivalence E2E (worktree maint/spec-comp-20261030c)

- Surface: the AJ1 identity-vocabulary rule (NATIVE_IDENTITY_VOCABS)
  through the LIVE FHIR $translate seam with an Athena DB actually
  carrying identity-vocab rows — ts88 pinned the rule only at the
  registry-membership level (aj11 asserts set membership); the mini
  fixture has no ICD10CM/ATC rows, so _do_translate_athena_bridge's
  equivalence branches were never exercised end-to-end.
- Fixture: mini-Athena extended with REAL-download shapes (ICD10CM
  E11.9 + ATC A10BA02 with standard_concept NULL; ICD10PCS with S;
  a RxNorm Extension row that must stay excluded).
- RESULT: ZERO FINDINGS — controls-only (18th clean surface). 8/8
  fresh probes PASS: NULL-S identity rows → equivalence='equivalent'
  (AJ1 holds through the seam); RxNorm Extension excluded (empty
  match, not identity); deprecated S → 'unmatched'; unknown code →
  empty match; direct-route controls green.
- Gates (fresh, serial): conformance 8061 passed / 0 failed / 19
  skipped (29:23); unit 850 passed / 11 skipped (9:28); ruff clean +
  formatted. .venv symlinked per the 20261030b lesson (ts69 green
  from the start this run).
## 2026-10-31 — TS-106 $subsumes fast-path partial-hit fallbacks (worktree maint/spec-comp-20261031)

- Surface: the MEDTERM4DS_ATHENA_SUBSUMES fast path's PARTIAL
  resolution — ts92 pinned both/neither-resolvable; the partial-hit
  pairs (one code athena-resolvable, one UMLS-only, either order),
  the RxNorm-Extension same-URI exclusion, and equivalence-first
  under the flag were never probed.
- RESULT: ZERO FINDINGS — controls-only (19th clean surface). 8/8
  fresh probes PASS. Builder-level mechanism evidence for the
  Extension exclusion: the fast path's vocab filter resolves the
  RxNorm URI to ['RxNorm'] only — Extension codes are invisible to
  resolution, per-code None → clean fallback; no closure answer can
  leak for an excluded vocabulary. Partial-hit verdicts identical
  flag-on/flag-off; equivalence-first holds for known and unknown
  identical codes.
- Gates (fresh, serial): conformance 8061 passed / 0 failed / 19
  skipped (24:05); unit 850 passed / 11 skipped (5:35); ruff clean +
  formatted.
## 2026-10-31 — TS-107 client lookup/resolve cross-surface agreement (worktree maint/spec-comp-20261031b)

- Surface: the CLIENT library's two overlapping entry points —
  Terminology.lookup() (get_code_infos) vs Terminology.resolve()
  (effective_code_refs + resolve_codes). test_client.py pins shapes;
  no suite ever verified SEMANTIC cross-agreement on the same code
  (display drift, activity disagreement, mode-vocabulary drift).
- RESULT: ZERO FINDINGS — controls-only (20th clean surface). 7/7
  fresh probes PASS: displays agree per-row (batch zip-aligned);
  activity verdicts consistent; unknown codes carry the same
  not-found story; invalid resolve_mode raises the identical
  exception type on both surfaces.
- Gates (fresh, serial): conformance 8060 passed / 0 failed / 19
  skipped (34:49); unit 850 passed / 11 skipped (12:47); ruff clean
  + formatted.
- Zero production changes. No registry edit (findings-only run).
## 2026-10-31 — TS-108 data-verify athena coexistence + verdict edges (worktree maint/spec-comp-20261031c)

- Surface: the `data verify` deploy gate (verify_duckdb) against the
  ATHENA coexistence shape — every verify pin predates the athena
  campaign; none probed a DB with athena_* tables (or staging
  residue) alongside mrconso/mrrel, nor the malformed-mrconso edge.
- RESULT: TWO FINDINGS, pinned strict-xfail flip-rows:
  VV1 (MEDIUM) — verify_duckdb's Python API silently accepts unknown
    source names (CLI validates via _normalize_sources_or_exit; the
    API returns ok with zero counts — QC-451's 'gate is a no-op'
    shape on the validation axis; QC-446/QC-453 API/CLI asymmetry
    class).
  VV2 (MEDIUM) — a MALFORMED mrconso (table present, required column
    absent) crashes with a raw duckdb BinderException instead of
    ok:false; QC-441 covered only the missing-TABLE shape.
  Controls PASS fresh: athena tables + interrupted-swap staging
  residue do not flip has_required_tables or ok; source_counts stay
  UMLS-only (no athena-vocab leakage); CLI-vs-API verdict symmetric
  via the console script; plain-DB ok and missing-table not-ok hold.
- Gates (fresh, serial): conformance 8058 passed / 0 failed / 19
  skipped / 2 xfailed (27:33); unit 850 passed / 11 skipped (5:40);
  ruff clean + formatted.
- Zero production changes (findings-only; fixes left to a fixbatch).

## 2026-11-01 — TS-109 terminology repoint LIVE integration (worktree maint/spec-comp-20261101)

- Surface: the repoint (cce25c6) shipped with mocked-download unit
  tests only; no conformance test exercised the PUBLISHED
  fhir4ds/terminology repo through the runtime's own resolver. This
  sweep pins the live contract against release 2026.10.31.1 (the
  current published tag — NEWER than the handoff's v2026.11.1).
- RESULT: ZERO FINDINGS — controls-only (21st clean surface). 5/5
  live probes PASS (T6 skipped: default release == live release):
  fingerprint recomputes to esp_468d01ebbab03648 from the shipped
  model; manifest single-stamp across families; lexical pairing
  exact under the new filename contract; semantic pairing exact (6
  categories); canonical JSON-only with value_sets == anchors ==
  manifest rows. OBSERVATION (not a defect): the 2026.10.31.1
  manifest's lexical family declares 'records' where v2026.11.1
  declared 'rows' — manifest-shape drift worth a changelog note from
  the model team.
- Network-gated + cache-isolated (own /tmp cache root; operator
  cache untouched).
- Gates (fresh, serial): conformance 8110 passed / 0 failed / 20
  skipped / 2 xfailed (25:48); unit 860 passed / 11 skipped (7:33);
  ruff clean + formatted.
- Zero production changes. No registry edit (findings-only run).
## 2026-11-01 — TS-110 canonical card fidelity on terminology layout, LIVE (worktree maint/spec-comp-20261101b)

- Surface: the 54c49f2 canonical_batch card-field fallbacks were
  pinned only at unit level (3-concept synthetic release); never
  probed against the PUBLISHED release end-to-end.
- RESULT: ZERO FINDINGS — controls-only (22nd clean surface). 6/6
  live probes PASS against release 2026.10.31.1: metformin card
  byte-equal to the live value_sets row; condition/lab cards
  complete; result_types + sources filters hold; batch/single top-1
  aligned. OBSERVATIONS (documented in-test, not defects): filtered
  queries over-fetch only count*5 (min 50) neighbors — window
  starvation is possible in principle (verified benign live: the
  starved 'diabetes'→VAL-MED neighbor scores 0.43, below the 0.70
  floor that makes empty filtered results trustworthy).
- Gates (fresh, serial): conformance 8111 / 0 / 19 + 2 xfailed
  (24:30); unit 860 / 11 (7:34); ruff clean + formatted.
- Zero production changes. No registry edit.
