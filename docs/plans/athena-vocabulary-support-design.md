# Design Note: OHDSI Athena Vocabulary Support (athena-ingest)

Status: DRAFT FOR JOEL REVIEW (design-first per process; no implementation yet)
Author: medterm4ds team · 2026-10-23
Campaign: ATHENA VOCABULARY SUPPORT (OMOP pair, medterm4ds side)

## 0. Source material studied

- Local Athena sample: `/mnt/d/decombustor/omop/CONCEPT.csv` —
  6,313,416 rows; schema `concept_id, concept_name, domain_id,
  vocabulary_id, concept_class_id, standard_concept, concept_code,
  valid_start_date, valid_end_date, invalid_reason`. Vocabulary
  census: RxNorm Extension 2.1M, NDC 1.17M, SNOMED 1.05M, SPL 677K,
  RxNorm 307K, LOINC 269K, OSM 203K, ICD10PCS 195K, ICD10CM 99K,
  HCPCS 12K, ATC 6.7K, … Domain census (first 2M rows): Drug,
  Measurement, Procedure, Condition, Observation, Geography, …
- omoponfhir (omoponfhir-omopv5-r4-mapping): the FHIR-URI ↔
  vocabulary_id correspondence table used by real OMOP consumers
  (OmopCodeableConceptMapping.java) — ATC/CPT4/HCPCS/ICD10CM/LOINC/
  NDC/NDFRT/RxNorm/SNOMED/UCUM…; CPT is `CPT4` in OMOP, HCPCS uses an
  OID urn. This is the seam precedent: consumers map by URI→vocab.
- Standard Athena release also ships CONCEPT_ANCESTOR (ancestor_id,
  descendant_id, min_levels_of_separation, max_…), CONCEPT_RELATIONSHIP
  (concept_id_1, concept_id_2, relationship_id, valid_*),
  VOCABULARY (vocabulary_id, vocabulary_name, vocabulary_reference,
  vocabulary_version, vocabulary_concept_id), plus CONCEPT_SYNONYM,
  DRUG_STRENGTH (justified only for the drug-exposure story — deferred).

## 1. Ingest (loader)

New CLI: `medterm4ds data ingest-athena --athena-dir <dir> [--db
<duckdb>] [--vocabularies SNOMED,RxNorm,LOINC,ICD10CM,ICD10PCS,CPT4,HCPCS,ATC]`.

- Tables loaded (named `athena_<lowercase>`): CONCEPT, CONCEPT_ANCESTOR,
  CONCEPT_RELATIONSHIP, VOCABULARY. CONCEPT_SYNONYM optional
  (`--with-synonyms`; ~20M rows, only needed if search parity with
  UMLS STR matching is wanted — recommend deferring).
- Types: concept_id BIGINT (Athena ids exceed int32), codes VARCHAR,
  dates as DATE via strptime of YYYYMMDD.
- Normalization at load: uppercase vocabulary_id kept verbatim (the
  OMOP spellings — CPT4, not CPT — are load-bearing for the bridge);
  a derived column `fhir_system` computed at load via the §3
  correspondence table (NULL for OMOP-internal vocabs like OS M,
  Type Concept, SNOMED-derived value sets).
- Storage recommendation: **SAME DB, new `athena_*` tables** (not a
  separate athena.duckdb). Rationale: (a) the concept bridge (§3)
  is a JOIN across mrconso↔CONCEPT — cross-DB duckdb joins need
  ATTACH and complicate the single-connection executor model;
  (b) table-prefix namespacing avoids every collision; (c) the
  existing read-only connection + memory-profile machinery works
  unchanged. Cost: DB grows by ~2-3 GB with default vocabs —
  documented; `--vocabularies` is the control.
- Idempotence: loader writes to `athena_*_staging`, then swaps in a
  transaction; `athena_meta` (loader version, source dir name,
  vocabulary_version strings from VOCABULARY.csv, row counts) is the
  provenance block — same doctrine as the embedding-index manifests.

## 2. Query surfaces

- Hierarchy: `get_athena_ancestors/descendants(concept_id)` reading
  CONCEPT_ANCESTOR directly (transitive-closure table — no BFS
  needed; this is CHEAPER and exact vs the UMLS walk, and answers
  the VS02-01 exact-total family trivially for Athena-sourced sets).
- `$subsumes`/`$closure`: NOT auto-switched. The UMLS-walk machinery
  stays canonical for UMLS-sourced codes. When BOTH codes resolve to
  Athena concepts (via the §3 bridge), the closure path MAY consult
  CONCEPT_ANCESTOR — behind a capability flag
  (`MEDTERM4DS_ATHENA_SUBSUMES=1`) until parity is measured.
  Design intent: keep the fixbatch-era $subsumes contract (U2
  code-in-system validation) intact; Athena is an additional
  evidence source, not a replacement engine.
- Search: `search_names` gains `domain_id`/`vocabulary_id` filters
  when athena tables are present (Athena-native concept_name is the
  display); `concept_class_id` and `invalid_reason` (NULL = valid,
  'D' = deprecated, 'U' = updated) surface as row fields. CLI
  `athena-concepts` subcommand mirrors `sample-codes` conventions
  (unknown vocabulary → hard Error per the HQ1-fix doctrine).
- MCP: `athena_lookup(concept_id)`, `athena_ancestors(concept_id)`,
  `athena_map_to_fhir(concept_id)` tools; runtime health's `sources`
  gains an `athena: true|false` block (fixing HH1's configured-vs-
  live gap for the new tables at birth).

## 3. Concept-mapping bridge — the FHIR4DS INTEGRATION POINT

**Deliverable (per Joel's sharpened seam): a converter that
translates the Athena CONCEPT table into a FHIR ConceptMap
RESOURCE**, which the fhir4ds side registers and joins in their OMOP
table VDs.**

Artifact: `medterm4ds athena export-conceptmap [--vocabularies …]
--out conceptmap_athena.json` producing:

```json
{"resourceType": "ConceptMap",
 "url": "urn:medterm4ds:athena-conceptmap",
 "version": "<vocabulary_version of the newest source vocab>",
 "group": [{
   "source": "http://snomed.info/sct",            // FHIR system
   "target": "urn:athena:concept-id",              // target system
   "element": [
     {"code": "44054006", "display": "…",
      "target": [{"code": "316139", "equivalence": "equal",
                  "comment": "vocabulary_id=SNOMED; concept_class=SCT Clinical finding; domain=Condition; standard_concept=S"}]}]}]}
 ...one group per vocabulary with a FHIR system correspondence...]}
```

- Group-per-vocabulary; source = the FHIR system URI; target =
  `urn:athena:concept-id` with concept_id as the target code (the
  OMOP join key the VDs want).
- vocabulary_id ↔ FHIR-system correspondence table (seeded from the
  omoponfhir enum, our canonical-systems table as tiebreaker):
  SNOMED→snomed.info/sct, RxNorm→nlm rxnorm URI, LOINC→loinc.org,
  ICD10CM/ICD10PCS→hl7 sid icd-10-cm / icd-10-pcs, CPT4→ama cpt,
  HCPCS→its OID urn, ATC→whocc.no/atc, NDC→hl7 sid ndc.
  Vocabs WITHOUT a FHIR system (SPL, OSM, OMOP Genomic, Type
  Concept,…) are EXCLUDED by default; `--include-internal` emits
  them under `urn:athena:<vocabulary_id-lower>` groups.
- `equivalence`: "equal" for standard_concept='S' rows where
  concept_code matches the FHIR-side code verbatim; "relatedto" for
  non-standard (RxNorm Extension brick-and-mortar codes, source
  NDCs); deprecated rows (invalid_reason) carry
  equivalence="unmatched" + comment (keeps C1's semantics and the
  fixbatch-6 mixed [equivalent,unmatched] convention).
- Group metadata: each group carries the source
  `vocabulary_version` string (from VOCABULARY.csv) as
  `group.version`-style extension — provenance for the VD join.
- Scale note: default vocab set ≈ 2.0M elements (~350-500 MB JSON;
  NDJSON option `--ndjson` for streaming consumers). The fhir4ds VD
  join is a duckdb-side registration; they get a stable
  `urn:medterm4ds:athena-conceptmap` url + per-vocabulary groups.
- Runtime seam (staged later, designed now): `ConceptMap/$translate
  ?url=urn:medterm4ds:athena-conceptmap` resolves against the
  converter's in-DB view — same resolve_concept_map_url contract
  the implicit crosswalk uses (extend the accepted-set; the N1/N2
  version semantics apply unchanged).

## 4. Dedupe / coexistence with UMLS-sourced concepts

- Principle: **UMLS remains canonical for terminology identity;
  Athena is a parallel source keyed by its own concept_id**. The two
  never merge silently: athena_* tables are a namespaced store; the
  bridge (§3) is the ONLY cross-reference artifact, and it is
  code-identity based (concept_code = the UMLS CODE).
- Where both could answer ($lookup on a SNOMED code, say): the UMLS
  path answers (unchanged contracts — the 90-iteration surface
  stays stable). Athena answers only on explicitly-Athena surfaces
  (§2) or via the bridge artifact.
- Conflict case (same concept_code, differing display/validity):
  documented, not adjudicated — the ConceptMap row carries Athena's
  name + validity as comment data; consumers choose. Version-skew
  (Athena SNOMED edition vs UMLS release) is a user-configuration
  fact surfaced in athena_meta.

## 5. Licensing

Athena downloads bundle vocabularies under DIFFERENT terms than the
UMLS Metathesaurus license medterm4ds already assumes: SNOMED CT
(Apache-2.0 distribution in Athena, but the underlying SNOMED
Affiliates license governs use in member/non-member countries),
RxNorm/LOINC/HCPCS (NLM/Regenstrief terms, generally permissive with
attribution), others vary (ATC free; CPT via UMLS license only —
CPT4 in Athena still requires the AMA/UMLS agreement). Design
consequences: (a) the loader prints a licensing notice table
(vocabulary → license family → pointer) and requires
`--accept-licenses` for first load; (b) athena_meta records the
acceptance flag; (c) the ConceptMap artifact is derived data —
redistribution terms follow the strictest source vocabulary (the
export CLI prints the same notice; fhir4ds consuming it in-memory
for VD joins is use, not redistribution); (d) docs page addition
"Data licensing" subsection. Users decide what they may load; we
surface, we do not enforce.

## 6. Implementation staging (post-approval)

1. Loader + athena_* schema + CLI (small, self-contained).
2. Query surfaces (ancestry + search filters + MCP tools).
3. ConceptMap converter (the fhir4ds seam — can land at stage 1.5
   if the VD work needs it early; no dependency on stage 2).
4. Optional $translate runtime seam + subsumes flag (measure first).
Tests ride the existing conformance discipline: new suites pin the
loader's provenance block, the correspondence table, and the
converter's group/element shape (incl. equivalence classes + the
deprecated-row unmatched encoding).

## Addendum (2026-10-26): the runtime $translate seam (stage 4b)

The design sketched "ConceptMap/$translate?url=urn:medterm4ds:
athena-conceptmap resolves against the converter's in-DB view". The
concrete seam, now specified:

- The implicit-map resolver's accepted set EXTENDS to include the
  athena ConceptMap url when athena_* tables are present: url=
  urn:medterm4ds:athena-conceptmap selects the bridge; the DEFAULT
  (url omitted) stays the UMLS crosswalk — the athena map is
  opt-in per request, never a silent replacement.
- Direction: source system+code → target urn:athena:concept-id.
  The $translate In-params carry the FHIR system + code; the
  targetSystem is the concept-id urn. targetSystem values OTHER
  than the concept-id urn 400 (the map has one target).
- Version semantics: the map is unversioned like the implicit
  crosswalk (the athena_meta loaded_at is the provenance, surfaced
  in cache-info, not a version dial) — conceptMapVersion rejected
  identically to N2.
- Equivalence: the converter's classes ride through unchanged
  (identity vocababs equal; deprecated unmatched; RxNorm Extension
  relatedto).
- The seam exists ONLY when athena tables are ingested; without
  them the url 400s as unknown (the resolve_concept_map_url
  contract, extended-set behavior only).
