"""TS-70 $search resultTypes validation by mode (2026-10-20).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261020d).
FRESH EVIDENCE — live probes executed this run (real BM25 index;
canonical mode warm from the cached SapBERT split); no prior-run
results cited.

Scope: the resultTypes enum contract ACROSS MODES. G1 (fixed,
fixbatch6) wired the filter on legacy modes; TS-43 pinned canonical
validation+filtering. The CROSS-MODE validation posture was never
matrixed.

RQ1 (MEDIUM, NEW) — resultTypes validation is mode-dependent:
    bogus values silently return total=0 on LEXICAL and HYBRID (200,
    no diagnostic — indistinguishable from a legitimate empty
    result) while CANONICAL 400s with the enum ('Unknown result
    type: bogus. Valid: condition, drug_class, lab, medication,
    procedure, symptom, vaccine, vital.'). Same server, same param,
    opposite verdicts by mode. The miss-encoding class at its
    sharpest: a typo'd resultTypes on lexical yields a confident
    empty searchset — the client believes the vocabulary has no
    matches. Fix shape: route legacy modes through the same
    result-type validation the canonical service applies (the enum
    exists; one call site). Pinned rq10-rq13.

RQ2 (LOW-MED) — enum CONTENTS diverge by mode: canonical's validated
    enum carries service-domain members (drug_class, symptom,
    vital) that the lexical index cannot serve (drug_class → 200
    total=0, silent). A client discovering the enum from a canonical
    400 then running lexical gets quiet empty results for two of the
    eight advertised values. Fix shape: mode-aware enum in the 400
    diagnostic + reject unservable members loudly on lexical.
    Pinned rq20/rq21.

Controls (fresh): canonical validates AND filters (medication 200
    for metformin); lexical filters correctly on valid members
    (condition/lab/medication/procedure return 1 on the diabetes
    fixture; case-SENSITIVE matching pinned as documentation —
    CONDITION → 0); multi-value [condition, lab] unions; empty
    string = unfiltered (1); vaccine/body_structure 0 on this
    fixture (no matching rows — legitimate empties, contrast rq10).
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")


@pytest.fixture(scope="module")
def search_client(tmp_path_factory):
    from starlette.testclient import TestClient

    from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app
    from medterm4ds.services.search import DEFAULT_SEARCH_INDEX_DIR

    from .conftest import _make_conformance_db

    db_path = tmp_path_factory.mktemp("ts70_rt") / "umls.duckdb"
    _make_conformance_db(db_path)
    settings = FhirApiSettings(
        db_path=db_path,
        memory_profile="low",
        search_index_dir=DEFAULT_SEARCH_INDEX_DIR,
        prepare_cache=False,
    )
    app = create_fhir_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


class TestRQ1ValidationByMode:
    """RQ1 — bogus values: silent-0 on legacy, 400 on canonical."""

    def test_rq10_lexical_bogus_silent_zero(self, search_client):
        """resultTypes=bogus on lexical → 200 total=0, no diagnostic
        (indistinguishable from a legitimate empty result). Flip when
        legacy modes validate."""
        r = search_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "diabetes", "searchMode": "lexical",
                "count": 1, "resultTypes": "bogus",
            },
        )
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_rq11_canonical_bogus_400(self, search_client):
        """The same param on canonical → 400 with the enum. The
        cross-mode divergence premise."""
        r = search_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "diabetes", "searchMode": "canonical",
                "count": 1, "resultTypes": "bogus",
            },
        )
        assert r.status_code == 400
        assert "Unknown result type" in (
            r.json()["issue"][0]["diagnostics"]
        )

    def test_rq12_hybrid_bogus_silent_zero(self, search_client):
        """Hybrid matches lexical's silent-0 posture."""
        r = search_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "diabetes", "searchMode": "hybrid",
                "count": 1, "resultTypes": "bogus",
            },
        )
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_rq13_contrast_legitimate_empty(self, search_client):
        """vaccine on the diabetes fixture is a LEGITIMATE 0 (no
        vaccine rows match) — the contrast that makes rq10's silent-0
        dangerous: both look identical."""
        r = search_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "diabetes", "searchMode": "lexical",
                "count": 1, "resultTypes": "vaccine",
            },
        )
        assert r.status_code == 200
        assert r.json()["total"] == 0


class TestRQ2EnumContentsDiverge:
    """RQ2 — service-domain members unservable on lexical."""

    def test_rq20_enum_lists_service_members(self, search_client):
        """Canonical's 400 advertises drug_class/symptom/vital —
        members the lexical index cannot serve."""
        r = search_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "x", "searchMode": "canonical",
                "resultTypes": "bogus",
            },
        )
        diag = r.json()["issue"][0]["diagnostics"]
        for member in ("drug_class", "symptom", "vital"):
            assert member in diag

    def test_rq21_lexical_drug_class_silent_zero(self, search_client):
        """drug_class on lexical → 200 total=0 (quietly unservable).
        Flip when lexical rejects unservable members or serves
        them."""
        r = search_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "metformin", "searchMode": "lexical",
                "count": 3, "resultTypes": "drug_class",
            },
        )
        assert r.status_code == 200
        assert r.json()["total"] == 0


class TestTS70Controls:
    """Controls — valid members filter correctly everywhere."""

    @pytest.mark.parametrize(
        "rt", ["condition", "lab", "medication", "procedure"]
    )
    def test_c10_lexical_valid_filters(self, search_client, rt):
        r = search_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "diabetes", "searchMode": "lexical",
                "count": 1, "resultTypes": rt,
            },
        )
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_c11_canonical_medication_filters(self, search_client):
        r = search_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "metformin", "searchMode": "canonical",
                "count": 1, "resultTypes": "medication",
            },
        )
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_c12_multi_value_unions(self, search_client):
        r = search_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "diabetes", "searchMode": "lexical",
                "count": 1, "resultTypes": ["condition", "lab"],
            },
        )
        assert r.status_code == 200

    def test_c13_empty_string_unfiltered(self, search_client):
        r = search_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "diabetes", "searchMode": "lexical",
                "count": 1, "resultTypes": "",
            },
        )
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_c14_case_sensitive_documented(self, search_client):
        """CONDITION (uppercase) → 0: case-sensitive matching, pinned
        as documentation (canonical's enum diagnostic is the
        discovery surface)."""
        r = search_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "diabetes", "searchMode": "lexical",
                "count": 1, "resultTypes": "CONDITION",
            },
        )
        assert r.status_code == 200
        assert r.json()["total"] == 0
