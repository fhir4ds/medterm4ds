# CAMPAIGN RECORD: Athena Vocabulary Support (athena-ingest)

Arc: design → stages 1/1.5/2/4 → fixbatches 1/2 → parity ruling →
release v0.0.7. Duration: 2026-10-23 → 2026-10-27 (5 days).

## Design-first (approved 2026-10-23)

docs/plans/athena-vocabulary-support-design.md — grounded in the
live 6.3M-row CONCEPT.csv census + the omoponfhir correspondence
enum. Key decisions: same-DB `athena_*` tables; CONCEPT_ANCESTOR as
exact cheap ancestry; UMLS stays canonical (no silent $subsumes
switch); the fhir4ds seam as a ConceptMap RESOURCE converter
(thin NDJSON scalar-join contract per the S2 coordination); the
licensing notice + --accept-licenses gate.

## Stage 1 + 1.5 (v0.0.6): loader + converter

`ingest-athena` (4 CSVs, staging-swap, BIGINT ids, athena_meta
provenance) + `export-conceptmap` (thin/monolithic, equivalence
classes, vocabulary_version provenance). Real-data: 4,088,225 rows
in 16-39s; zero duplicate scalar keys.

## Stage 2 (v0.0.7): query surfaces

Closure ancestry (exact — the VS02-01 family's answer), MCP ×4
tools + health block, CLI ×2 subcommands, Athena-native search
filters.

## Fixbatches

- athena-batch1 (8 findings): AR1 HIGH real-schema loader failure
  (the fixture-fidelity trap — the mini fixture was hand-written to
  the wrong schema; first REAL-download validation caught it), AG1
  edge-table semi-join (40.7M orphans → 0), AF1-4 CLI boundaries,
  AC1 engine OID map (closes AL2), AJ1 identity-equivalence.
- athena-batch2 (2): AS1 pre-batch1 schema remedy at the service
  boundary, AS2 loud vocabulary validation.
- Open tail: AS3 (batch dispatcher never forwards map_url — the
  cross-handler-helper-wiring class relearned two days later) joins
  the FA2 dispatcher fixbatch candidate (the 2×2 bypass matrix).

## Stage 4: parity measurement → LOCKED ruling

254 stratified pairs: closure 164x faster (~10.7ms vs ~1,754ms
/pair) but 23 real disagreements — ALL RxNorm definitional
(`tradename_of` exists in UMLS RELA but the isa-only walk excludes
it; spot-verified 800350→800413). RECOMMENDATION ACCEPTED
2026-10-27: the flag stays OFF-default PERMANENTLY. Standing pins:
ts90 (ancestry semantics), ts94 (resolved-side canary).

## Verification discipline (the program's own spec-comp sweeps)

12+ findings found in MY OWN landing code (AF/AG/AC/AJ/AS families)
— the maintenance program sweeping the campaign's surfaces each
merge; every finding pinned flip-ready before its fixbatch.
