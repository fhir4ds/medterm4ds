
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
- Zero production changes. No registry edit (findings-only run).
