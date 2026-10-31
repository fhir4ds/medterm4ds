
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
- Zero production changes. No registry edit (findings-only run).
