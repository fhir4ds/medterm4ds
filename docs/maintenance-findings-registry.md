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
