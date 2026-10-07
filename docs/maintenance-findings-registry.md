# Maintenance Findings Registry (committed)

This file is the COMMITTED mirror of the maintenance-program findings
that live in the gitignored `docs/.ai_loop/AGENTS.md` registry. The
.ai_loop state is worktree-local and has been lost repeatedly when
maintenance worktrees were pruned after merge (iterations 03b, 03c).
Committed here so the findings survive worktree lifecycle; the full
narrative entries (methodology, fix shapes, provenance) remain in
AGENTS.md within whichever worktree ran the iteration — the pin
SUITES themselves are committed and are the load-bearing contracts.

## Open findings (severity-ordered)

| id | sev | found | surface | finding | pin suite (committed) |
|---|---|---|---|---|---|
| F1 | HIGH | 2026-10-02 | VS-01/VS-03 $expand | multiple compose.include.filters OR-composed; R4 4.9.12 says "SHALL all be true" (AND) | test_vs01_filter_semantics_20261002.py (f10) |
| CF-HISTORIAN-VS02-01 | HIGH | 2026-08 (sweep) | VS-02 $expand | exact un-truncated `total` under BFS caps deferred; observability landed 2026-10-02 | test_vs02_vs0201_observable_20261002.py (d10-d50) |
| P2 | MEDIUM | 2026-10-03 | TS-04/VS-04 $expand paging | `expansion.total` page-dependent on same expansion (+1-probe window) — paging clients silently drop concepts; fix together with VS02-01 | test_ts04_paging_semantics_20261003.py (p20/p21) |
| T1 | MEDIUM | 2026-10-03 | TS-02/CM-02 $translate | `reverse=true` silently returns FORWARD results (silent wrong direction) | test_ts02_translate_semantics_20261003.py (t10/t11) |
| C1 | MEDIUM | 2026-10-03 | CM-03 $closure | one unknown code poisons whole request; spec's `unmatched` equivalence unused; first-only diagnostic | test_cm03_closure_semantics_20261003.py (c10/c12) |
| F2 | LOW | 2026-10-02 | VS-01/VS-03 $expand | vsd-3 (concept+filter coexistence) accepted, not 400 | test_vs01_filter_semantics_20261002.py (f20) |
| P1 | LOW | 2026-10-03 | TS-04/VS-04 $expand paging | `expansion.offset` never echoed under paging | test_ts04_paging_semantics_20261003.py (p10/p11) |
| T2 | LOW | 2026-10-03 | TS-02/CM-02 $translate | "one (and only one)" input contract unenforced; silent scalar precedence | test_ts02_translate_semantics_20261003.py (t20) |
| X1 | LOW | 2026-10-04 | TS-01 XML surface | cross-format CONTENT divergence: QC-300 XML control-char sanitizer alters message content vs JSON path for identical requests (JSON 'The display "w\x08rong"…' vs XML 'The display "wrong"…'); neither side spec-illegal; asymmetry undocumented. Fix: sanitize at message-building layer (both formats agree) OR document as intended | test_ts01_xml_parity_20261004.py (x10-x12) |
| M1 | MEDIUM | 2026-10-06 | TS-10 transport parity | GET $lookup rejects `coding` (422, unknown FastAPI Query param) while POST accepts it (derives system/code, QA-022); R4 declares coding for BOTH transports. Client porting transports silently loses a spec-declared capability. Fix: accept coding on GET OR declare the gap in TerminologyCapabilities | test_ts10_transport_parity_20261006.py (m10-m12) |
| S1 | MEDIUM | 2026-10-04 | TS-03 $subsumes | unknown codeA/codeB (either or both) → confident 200 not-subsumed; R4 §4.8.21.3: "If the server is unable to determine the relationship… returns an error response with an OperationOutcome" — silent wrong answer, inverse of C1 | test_ts03_subsumes_semantics_20261004.py (s10-s12) |
| S2 | LOW | 2026-10-04 | TS-03 $subsumes | instance-level form /CodeSystem/{id}/$subsumes 404s; R4 defines it, and it is the only legal path for system-less params | test_ts03_subsumes_semantics_20261004.py (s20) |
| B1 | MEDIUM | 2026-10-04 | Bundle $transaction | transaction processed with BATCH (non-atomic) semantics: failing entries still yield HTTP 200 transaction-response + per-entry statuses; R4 §3.7.2 all-or-nothing violated; CS advertises transaction. Fix: 400 not-supported (read-only server) or whole-transaction failure | test_ts04_bundle_semantics_20261004.py (b10-b12) |
| B2 | LOW | 2026-10-04 | Bundle $batch | metadata unreachable from batch entries ('metadata', 'fhir/metadata', '/fhir/metadata' all per-entry 404); R4 §3.6.1 entry URLs resolve against the FHIR base | test_ts04_bundle_semantics_20261004.py (b20) |
| L1 | LOW-MED | 2026-10-04 | $lookup | R4 `property` In-param (0..*, client-side Out filtering) accepted but ignored — property=display/child returns the full default set (cui/tty/aui/…); param never declared in lookup_get (fhir_api.py:2341) so FastAPI silently drops it | test_ts05_cross_op_consistency_20261004.py (l10/l11) |
| E1 | MEDIUM | 2026-10-05 | $extract | invalid resultTypes → HTTP 500 (unhandled ValueError from _result_types_to_prefixes) on GET AND POST; $extract lacks the ValueError→400 translation $search applies (QC-123 pattern); fix: pre-dispatch validation or wrapper | test_ts06_extract_contract_20261005.py (e10/e11) |
| G1 | MEDIUM | 2026-10-05 | $search | resultTypes silently IGNORED on the semantic path (resultTypes=lab returns ICD-10-CM condition entries, GET+POST identical); canonical mode validates + 400s the same parameter — load-bearing in one mode, no-op in another | test_ts07_search_contract_20261005.py (g10-g12) |
| G2 | LOW | 2026-10-05 | $search | availability gate (503) precedes input validation — invalid engine/resultTypes under hybrid returns 503 (index missing), masking malformed requests until the index exists; enum params not route-guarded unlike $extract's mode/minGrade | test_ts07_search_contract_20261005.py (g20/g21) |
| K1 | LOW-MED | 2026-10-05 | artifact governance | RESOLVED 2026-10-05 (maint/fix-serving-pin-20261005): effective_embedding_space() raises ManifestError on a set-but-unaccepted pin (naming accepted values); lexicographic scan now applies only when the effective space's unit is absent locally — never as a typo swallower | test_artifact_governance_20261005.py (k10 flipped/k11) |
| K2 | LOW | 2026-10-05 | artifact governance | RESOLVED 2026-10-05 (maint/fix-serving-pin-20261005): cache-info split summary carries serving_space + serving_space_source (default vs pinned) — rotation state observable, K1-class typos visible | test_artifact_governance_20261005.py (k20 flipped) |
| K1 | LOW-MED | 2026-10-05 | artifact governance | a SET-but-garbage MEDTERM4DS_EMBEDDING_SPACE silently degrades to the lexicographic fallback (sorted()[0] = OLD space esp_5a50) — an operator typo of the rotation knob silently undoes the rotation; fallback should apply only when NO pin is set | test_artifact_governance_20261005.py (k10/k11) |
| K2 | LOW | 2026-10-05 | artifact governance | cache-info reports split_root/mode/models/data_revision but NOT the serving pin or would-serve space — the rotation knob's effective value is unobservable (a K1 typo would be invisible there too) | test_artifact_governance_20261005.py (k20) |
| V1 | HIGH | 2026-10-06 | VS $validate-code | url-scoping ABSENT (documented at fhir_api.py:2781 as spec-compat acceptance, severity never registered): out-of-valueSet codes validate TRUE — RxNorm metformin TRUE against the SNOMED isa-DM set whose expansion is {73211009, 44054006}; clinical filtering clients get silent TRUEs; fix = expansion membership test or explicit not-supported | test_ts09_vs_validate_membership_20261006.py (v10-v12) |
| V2 | LOW | 2026-10-06 | VS $validate-code | inline valueSet In-param accepted (shape-parsed) but ignored for membership — compose-include of exactly one code still validates non-member codes TRUE; distinct fix surface from V1 (compose evaluation vs url-form expansion) | test_ts09_vs_validate_membership_20261006.py (v12) |
| Q1 | MEDIUM-HIGH | 2026-10-06 | TS-11 request-body negotiation | XML request bodies rejected 422 on ALL POST ops + $batch while CapabilityStatement advertises format=['json','xml'] (R4 §2.1.0.7 declares format codes for the full RESTful surface incl. request bodies; R4 §3.1.0 JSON+XML mandatory). Response-side XML fully supported (TS-01). Fix: parse fhir+xml bodies (from_fhir_xml inverse of to_fhir_xml) or stop advertising 'xml' | test_ts11_request_body_negotiation_20261006.py (q10-q13) |
| Q2 | LOW | 2026-10-06 | TS-11 request-body negotiation | 422 diagnostic for well-formed XML body is misleading ('Parameter unknown: Input should be a valid dictionary' = JSON-parser artifact). Rides with Q1 fix (parse or 415 w/ accurate reason) | test_ts11_request_body_negotiation_20261006.py (q20) |
| R1 | MEDIUM | 2026-10-06 | TS-12 search/read surface | searchset Bundle lacks `self` link — R4 §3.1.0.14 'All searches SHALL return this value'; stub searchset keys are resourceType/type/total only; paging clients cannot anchor traversal; fix = emit link:[{relation:self}] in search_resource | test_ts12_search_read_surface_20261006.py (r10/r11) |
| R2 | LOW-MED | 2026-10-06 | TS-12 search/read surface | POST [base]/[type]/_search 405s (both form + Parameters encodings) — R4 §3.1.0.13 'Servers SHALL support' the POST _search endpoint; fix = alias handler or declare GET-only in CapabilityStatement | test_ts12_search_read_surface_20261006.py (r20-r22) |
| R3 | LOW | 2026-10-06 | TS-12 search/read surface | HEAD on read 405s — R4 §3.1.0.2 'A HEAD request can also be used'; rides with stub-read design; fix when read gains semantics or route HEAD→GET | test_ts12_search_read_surface_20261006.py (r30/r31) |
| W1 | MEDIUM | 2026-10-06 | TS-13 $extract input | over-limit text (50k cap): GET → 422 (FastAPI Query max_length) vs POST → 400 (handler check) — same violation, different status+diagnostic per transport (TS-10 family, reversed polarity); fix = normalize via $search's ValueError→400 wrapper pattern (QA-005) | test_ts13_extract_input_surface_20261006.py (w10-w12) |
| W2 | LOW | 2026-10-06 | TS-13 $extract input | whitespace-only text runs the FULL NLP pipeline (79s cold) for a guaranteed-zero result — trivially scriptable exhaustion surface; fix = strip() pre-check → 400 or pipeline short-circuit | test_ts13_extract_input_surface_20261006.py (w20) |
| W3 | LOW | 2026-10-06 | TS-13 $extract input | $extract/$search OperationDefinitions advertised at medterm4ds.org URLs that are DNS-unresolvable + no local OperationDefinition route — custom-op contracts unintrospectable anywhere (advertised-capability family, cf. Q1); fix = host OperationDefinition resources locally | test_ts13_extract_input_surface_20261006.py (w30-w32) |
| L1 | MEDIUM | 2026-10-07 | TS-14 $closure input | unknown In-param (e.g. 'entities', not an R4 param) silently falls to the RESET branch — a typo'd add-request WIPES the closure at 200 (token reverts to empty-table hash); QC-264 guarded all-malformed-concept but not wrong-param-name; fix = 400 on unknown params or never reset when other params present | test_ts14_closure_params_20261007.py (l10-l12) |
| L2 | LOW-MED | 2026-10-07 | TS-14 $closure mounting | R4 mounts $closure at [base]/$closure (ConceptMap-level); server serves only /fhir/CodeSystem/$closure ([base]/$closure and /fhir/ConceptMap/$closure both 405) and declares it under CodeSystem ops in CapabilityStatement; fix = mount spec aliases + correct the declaration | test_ts14_closure_params_20261007.py (l20/l21) |
| L3 | MEDIUM | 2026-10-07 | TS-14 $closure input | version-resync request (In 'version' = send-entries-since, no concept entries) WIPEs the closure via the same over-broad reset branch — resync against old token after growth returned the EMPTY-TABLE hash, destroying state at 200; fix = honor version as resync or 501, never reset | test_ts14_closure_params_20261007.py (l30) |
| CF-SKEPTIC-CS05-01/02 | LOW | 2026-07 (sweep) | CS-05 $lookup | abstract hardcoded False + inactive never emitted; observability landed 2026-10-02 | test_cs05_abstract_observable_20261002.py (a30, d70) |
| CF-HISTORIAN-VS01-01 | MEDIUM | sweep | equivalence enum | subsumedby/not-relatedto not in R4 enum | (sweep suites, VS-01 historian) |
| CF-TERMINOLOGIST-VS01-01 | MEDIUM | sweep | VS-01 | omitted-display canonical resolution | (sweep suites) |
| CF-TERMINOLOGIST-CM01-01 | MEDIUM | sweep | CM-01 export | subsumes/specializes → relatedto fallback latent gap | (sweep suites) |
| CF-SKEPTIC-VS02-01 | LOW | sweep | VS-02 | count=0 rejected 422 vs spec-allowed empty | (sweep suites) |
| CF-EXPLORER-TS01-01 | LOW | sweep | TS-01 | generic Accept MIME returns FHIR MIME | (sweep suites) |
| CF-EXPLORER-VS04-01 | LOW | sweep | VS-04 | explicit-port URL form rejected | (sweep suites) |
| CF-SKEPTIC-CM03-01 | MEDIUM | sweep | CM-03 $closure | Out `return` valueString token vs 1..1 ConceptMap | (sweep suites + c20 control) |
| CF-HISTORIAN-CM03-02 | LOW | sweep | CM-03 | incomplete_since not surfaced | (sweep suites) |

## Resolved during maintenance program

- CF-HISTORIAN-VS02-02 (implicit-VS canonical system) — fix had landed via TS-03 QA-001; registry corrected 2026-10-02.
- "exclude[].filter ignored" — stale; QC-242/QC-244 landed it; corrected 2026-10-02 with probes r10/r11.

## Program ledger

See docs/.ai_loop/spec_comp/MAINTENANCE_PROGRAM_LEDGER.md (gitignored;
worktree-local) — iterations 1-7 summarized. Committed equivalents:
the maintenance suites themselves + this registry.

## Rules

- A resolved finding MUST be flipped here AND in the pin suite in the
  same change.
- New findings: add a row here, an AGENTS.md narrative entry, and a
  committed pin suite with flip-on-fix instructions.
