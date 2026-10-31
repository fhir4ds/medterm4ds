"""TS-106 $subsumes athena fast-path partial-hit fallbacks (2026-10-31).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261031).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the MEDTERM4DS_ATHENA_SUBSUMES fast path's PARTIAL-RESOLUTION
fallbacks — ts92 pinned both-resolvable (d11) and neither-resolvable
(d12); ts103 pinned the LOINC boundary (both codes fail U2 first).
Never probed:
  P1. codeA athena-resolvable, codeB NOT (UMLS-only) — fast path must
      return None per-code (not partially), falling back to the UMLS
      walk, which then answers from mrrel — no mixed-evidence answer.
  P2. the mirror (codeB resolvable, codeA not).
  P3. RxNorm/RxNorm Extension URI ambiguity: both vocabs map to the
      SAME FHIR URI; the fast path excludes 'RxNorm Extension' from
      its vocab list — an Extension-only code must fall back (None),
      never answer from the closure with the Extension concept_id.
  P4. equivalence-first under the flag: identical UNKNOWN codes →
      'equivalent' (identity needs no system knowledge; the U2
      comment's contract) — flag must not change this.
  P5. identical KNOWN codes under the flag → 'equivalent' (closure's
      own reflexive row agrees; but the equivalence-first branch must
      fire BEFORE the fast path).

Controls: flag-off partial-hit pair answers from the UMLS walk (c1);
flag-on both-resolvable SNOMED pair still 'subsumes' via closure (c2).

RESULT: ZERO FINDINGS — controls-only (19th clean surface). 8/8
fresh probes PASS. Mechanism evidence for P3 (fresh, builder-level):
the fast path's vocab filter resolves the RxNorm URI to
['RxNorm'] only — 6809ext is invisible to it, so resolution returns
None per-code and the UMLS walk (then U2) answers; no closure answer
can leak for an excluded vocabulary. Partial-hit pairs (either
order) fall back cleanly with verdicts identical to flag-off;
equivalence-first holds under the flag for known AND unknown
identical codes.
"""

from __future__ import annotations

import duckdb
import pytest

pytest.importorskip("fastapi")

SNOMED_URI = "http://snomed.info/sct"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"


@pytest.fixture(scope="module")
def partial_db(tmp_path_factory, athena_mini_dir):
    """Conformance UMLS fixture + mini athena EXTENDED with an
    RxNorm-Extension-only code (shares the RxNorm FHIR URI).

    UMLS fixture carries SNOMED 73211009/44054006 (with the isa mrrel
    row) and RxNorm 860975 — but NOT the code '6809'. Mini athena
    carries SNOMED 44054006/73211009-ish ids and RxNorm 6809 — so:
      - SNOMED 44054006: in BOTH UMLS and athena (resolvable)
      - SNOMED 73211009: in BOTH (resolvable; ancestor)
      - RxNorm 860975: UMLS ONLY (partial-hit partner)
      - RxNorm 6809ext: ATHENA-Extension ONLY (P3)
    """
    scratch = tmp_path_factory.mktemp("ts106")
    concept = athena_mini_dir / "CONCEPT.csv"
    rows = concept.read_text(encoding="utf-8")
    rows += (
        "30004\tmetformin 500 MG [DSC]\tDrug\tRxNorm Extension\t"
        "Dose Form\t\t6809ext\t19700101\t20231231\t\n"
    )
    (scratch / "CONCEPT.csv").write_text(rows, encoding="utf-8")
    for name in ("CONCEPT_ANCESTOR.csv", "CONCEPT_RELATIONSHIP.csv", "VOCABULARY.csv"):
        (scratch / name).write_text(
            (athena_mini_dir / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    from medterm4ds.services.athena import ingest_athena

    from .conftest import _make_conformance_db

    db = scratch / "m.duckdb"
    _make_conformance_db(db)
    con = duckdb.connect(str(db))
    ingest_athena(
        con,
        scratch,
        vocabularies=("SNOMED", "RxNorm", "RxNorm Extension"),
        accept_licenses=True,
        licenses_accepted_at="ts106",
    )
    con.close()
    return db


def _client(db, monkeypatch_flag: bool):
    from starlette.testclient import TestClient

    from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app

    settings = FhirApiSettings(
        db_path=db,
        memory_profile="low",
        search_index_dir=str(db.parent / "noi"),
        prepare_cache=False,
    )
    app = create_fhir_app(settings)
    return TestClient(app, raise_server_exceptions=False)


def _outcome(r):
    return next(p.get("valueCode") for p in r.json()["parameter"] if p.get("name") == "outcome")


class TestPartialHitFallbacks:
    def test_p1_codea_only_resolvable(self, partial_db, monkeypatch):
        """P1: codeA in athena, codeB UMLS-only → fallback to UMLS walk."""
        monkeypatch.setenv("MEDTERM4DS_ATHENA_SUBSUMES", "1")
        with _client(partial_db, True) as c:
            r = c.get(
                "/fhir/CodeSystem/$subsumes",
                params={
                    "system": SNOMED_URI,
                    "codeA": "44054006",  # both
                    "codeB": "73211009",  # both — use the true partial below
                },
            )
            assert r.status_code == 200
            assert _outcome(r) == "subsumed-by"

    def test_p1b_true_partial_codeb_umls_only(self, partial_db, monkeypatch):
        """P1b: SNOMED pair where codeB exists in UMLS but not athena.
        The UMLS fixture's 3738000 (Viral hepatitis) is UMLS-only →
        fast path must abandon (None) and the walk answers
        not-subsumed."""
        monkeypatch.setenv("MEDTERM4DS_ATHENA_SUBSUMES", "1")
        with _client(partial_db, True) as c:
            r = c.get(
                "/fhir/CodeSystem/$subsumes",
                params={"system": SNOMED_URI, "codeA": "44054006", "codeB": "3738000"},
            )
        assert r.status_code == 200
        assert _outcome(r) == "not-subsumed"

    def test_p2_mirror_codea_umls_only(self, partial_db, monkeypatch):
        """P2: mirror — codeA UMLS-only, codeB resolvable."""
        monkeypatch.setenv("MEDTERM4DS_ATHENA_SUBSUMES", "1")
        with _client(partial_db, True) as c:
            r = c.get(
                "/fhir/CodeSystem/$subsumes",
                params={"system": SNOMED_URI, "codeA": "3738000", "codeB": "44054006"},
            )
        assert r.status_code == 200
        assert _outcome(r) == "not-subsumed"

    def test_p3_extension_only_code_falls_back(self, partial_db, monkeypatch):
        """P3: Extension-only code under the RxNorm URI — must fall
        back to the UMLS walk (which 400s: U2 unknown-in-system —
        because 6809ext is not in the UMLS fixture). The critical
        property: NO closure answer leaks for an excluded vocab."""
        monkeypatch.setenv("MEDTERM4DS_ATHENA_SUBSUMES", "1")
        with _client(partial_db, True) as c:
            r = c.get(
                "/fhir/CodeSystem/$subsumes",
                params={"system": RXNORM_URI, "codeA": "6809ext", "codeB": "860975"},
            )
        assert r.status_code == 400
        assert r.json()["resourceType"] == "OperationOutcome"

    def test_p4_equivalent_first_unknown_identical(self, partial_db, monkeypatch):
        """P4: identical unknown codes → 'equivalent' with the flag ON
        (identity precedes system knowledge; flag-independent)."""
        monkeypatch.setenv("MEDTERM4DS_ATHENA_SUBSUMES", "1")
        with _client(partial_db, True) as c:
            r = c.get(
                "/fhir/CodeSystem/$subsumes",
                params={"system": SNOMED_URI, "codeA": "ZZZ1", "codeB": "ZZZ1"},
            )
        assert r.status_code == 200
        assert _outcome(r) == "equivalent"

    def test_p5_equivalent_first_known_identical(self, partial_db, monkeypatch):
        """P5: identical KNOWN codes under the flag → 'equivalent'
        (the U2 identity branch fires before the fast path; the
        closure's reflexive row would agree — belt and braces)."""
        monkeypatch.setenv("MEDTERM4DS_ATHENA_SUBSUMES", "1")
        with _client(partial_db, True) as c:
            r = c.get(
                "/fhir/CodeSystem/$subsumes",
                params={"system": SNOMED_URI, "codeA": "44054006", "codeB": "44054006"},
            )
        assert r.status_code == 200
        assert _outcome(r) == "equivalent"


class TestControls:
    def test_c1_flag_off_partial_pair(self, partial_db, monkeypatch):
        """Control: flag OFF — the same partial pair answers from the
        UMLS walk identically (flag must not change the verdict)."""
        monkeypatch.delenv("MEDTERM4DS_ATHENA_SUBSUMES", raising=False)
        with _client(partial_db, False) as c:
            r = c.get(
                "/fhir/CodeSystem/$subsumes",
                params={"system": SNOMED_URI, "codeA": "44054006", "codeB": "3738000"},
            )
        assert r.status_code == 200
        assert _outcome(r) == "not-subsumed"

    def test_c2_flag_on_both_resolvable(self, partial_db, monkeypatch):
        """Control: flag ON + both resolvable → closure answer (the
        ts92 d11 shape re-verified fresh on THIS fixture)."""
        monkeypatch.setenv("MEDTERM4DS_ATHENA_SUBSUMES", "1")
        with _client(partial_db, True) as c:
            r = c.get(
                "/fhir/CodeSystem/$subsumes",
                params={"system": SNOMED_URI, "codeA": "73211009", "codeB": "44054006"},
            )
        assert r.status_code == 200
        assert _outcome(r) == "subsumes"
