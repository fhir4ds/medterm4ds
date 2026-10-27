# Athena $subsumes Parity Measurement (stage 4a)

Status: MEASUREMENT COMPLETE — recommendation below; default stays
OFF pending Joel's ruling. Evidence: 254 stratified pairs, real
Athena download + production umls_current.duckdb, this run.

## Method

Strata: 40 random intra-vocabulary closure pairs per vocabulary
(SNOMED / RxNorm / RxNorm Extension / LOINC / ICD10PCS / ATC had
hierarchies; ICD10CM / CPT4 / HCPCS have none — 0 pairs, expected),
plus cross-domain negatives, reversed-direction pairs, and identity
pairs. Both engines answer every pair: the Athena closure
(athena_subsumes) vs the UMLS BFS walk (is_descendant, max_depth=20,
the $subsumes production path).

## Headline numbers

- 254 pairs measured. Raw agreement 141/254 (55.5%).
- **Timing: Athena 2.72s cumulative (~10.7ms/pair) vs UMLS walk
  445s (~1,754ms/pair) — 164x faster.**

## Disagreement triage (113 raw → 23 real)

- 90 of 113 disagreements are CODE-COVERAGE artifacts, not semantic:
  50 pairs have codes absent from BOTH sources' UMLS side (mostly
  SNOMED UK-extension …110000011xx codes our UMLS release does not
  carry; RxNorm Extension codes do not exist in UMLS at all), 40
  more have one code missing. The UMLS walk cannot see codes it
  does not carry → its `not-subsumed` is a false negative RELATIVE
  TO ATHENA'S richer universe.
- **23 real disagreements, ALL RxNorm, all the same shape**: the
  relationship EXISTS in UMLS as RELA `tradename_of` (verified
  spot-check 800350→800413: SCDC ingredient → SBDC tradename
  product), but the production walk only follows isa/PAR/CHD-family
  edges. Athena's OMOP closure includes ingredient/precise-product
  relationships derived from RxNorm's full relationship table.

## Interpretation

Both sources are correct under their own semantics. The gap is
DEFINITIONAL: "subsumption" for the UMLS walk = IS-A hierarchy only;
for the OMOP closure = the full ancestor table (incl. tradename-of,
ingredient-of). Neither is wrong; they answer different questions
that $subsumes collapses into one outcome vocabulary.

## Recommendation

**Do NOT default MEDTERM4DS_ATHENA_SUBSUMES=ON.** Three reasons:

1. Definitional divergence concentrated in RxNorm (23/43 RxNorm
   pairs disagree) — silently changing $subsumes answers for drug
   codes is a behavior change clients did not opt into.
2. Code-coverage asymmetry cuts the other way for UMLS-only codes:
   the fast path falls back per-pair (fine), but operators should
   choose which universe their $subsumes reflects.
3. The 164x speed win is real but not load-bearing at current
   traffic ($subsumes is not the hot path; $search is).

**Recommended posture instead**: keep the opt-in flag indefinitely
(document it), OR add a `hierarchySemantics` parameter to $subsumes
(`isa-only` default | `omop-closure`) so the choice is per-request
and visible — the honest contract for a definitional difference.
Athena-sourced ancestry (the athena-hierarchy CLI + MCP tools)
already answers closure questions on its own surface without
touching $subsumes.
