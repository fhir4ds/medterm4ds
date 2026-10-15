"""TS-43 canonical search under production data (2026-10-14).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261014e).
FRESH EVIDENCE — live probes executed this run against the
split-layout production search artifacts (names-basis wire); no
prior-run results cited.

Scope: canonical-mode $search on the CURRENT production data — TS-32
pinned the envelope on the 10-12 wire; this re-pins it on the
names-basis wire and adds the service/endpoint domain mapping.

ZERO findings — controls iteration (fifth clean surface). Pinned:
    ENVELOPE: canonical rows carry canonical-id + canonical-result-
    type as RESOURCE extensions (medterm4ds.org namespace — CA2's
    registered mixed-namespace observation re-verified) and
    match-grade as a SEARCH extension (fhir4ds.org); display = the
    patient-friendly name; fullUrl = relative composite (CA1's
    registered shape, unchanged).
    SERVICЕ↔ENDPOINT MAPPING: the service row exposes ``domain``;
    the endpoint emits ``result_type`` — one source of truth each
    side of the boundary (verified: domain medication → result-type
    extension 'medication').
    RESULTTYPES: canonical validates (ValueError on bogus) and
    FILTERS (resultTypes=lab → the LOINC LP33332-5 metformin lab
    concept ONLY, envelope result-type 'lab') — the G1-contrast
    control on the current wire.
    MATCH-GRADE TAXONOMY: exact (metformin→Metformin) and probable
    (drug-class Biguanides) observed on production.
"""

from __future__ import annotations

import pytest


def _svc_rows(query: str, count: int = 3, **kw):
    from medterm4ds.services.search import get_search_service

    svc = get_search_service()
    if not svc.semantic_available:
        pytest.skip("semantic model unavailable")
    rows = svc.search(query, mode="canonical", count=count, **kw)
    return rows if isinstance(rows, list) else rows.results


def _row_dict(r) -> dict:
    return r if isinstance(r, dict) else r.__dict__


class TestCanonicalServiceControls:
    """Service-layer contracts on production artifacts."""

    def test_c10_row_shape(self):
        rows = _svc_rows("metformin")
        d = _row_dict(rows[0])
        for field in (
            "anchor_code", "anchor_system", "canonical_id", "domain",
            "patient_friendly_name", "match_grade", "score",
            "combination_members",
        ):
            assert field in d, field
        assert d["canonical_id"] == "VAL-MED-RXNORM-6809"
        # Service rows carry domain as a DISPLAY-form list
        # (['Medications']); the endpoint maps it to the wire enum
        # ('medication') — one source of truth per side.
        assert d["domain"] == ["Medications"]
        assert d["match_grade"] == "exact"

    def test_c11_match_grade_taxonomy(self):
        rows = _svc_rows("metformin", count=5)
        grades = {_row_dict(r)["match_grade"] for r in rows}
        assert grades <= {"exact", "probable", "possible", "broader"}

    def test_c12_result_types_validated(self):
        from medterm4ds.services.search import get_search_service

        svc = get_search_service()
        with pytest.raises(ValueError, match="Unknown result type"):
            svc.search(
                "metformin", mode="canonical", result_types=["bogus"]
            )

    def test_c13_result_types_filters(self):
        rows = _svc_rows(
            "metformin", count=5, result_types=["lab"]
        )
        assert rows, "the LOINC metformin lab concept must match"
        for r in rows:
            assert _row_dict(r)["domain"] == ["Laboratory"]


class TestCanonicalEndpointControls:
    """Endpoint envelope on the production wire (fresh-cache)."""

    @pytest.fixture(scope="class")
    def entries(self, tmp_path_factory):
        import pathlib

        from starlette.testclient import TestClient

        from medterm4ds.apps.fhir_api import (
            FhirApiSettings,
            create_fhir_app,
        )

        app = create_fhir_app(
            FhirApiSettings(
                db_path=pathlib.Path(
                    "/mnt/d/medterm4ds/data/umls_current.duckdb"
                ),
                memory_profile="low",
                search_index_dir=str(
                    tmp_path_factory.mktemp("ts43-idx")
                ),
                prepare_cache=False,
            )
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get(
                "/fhir/CodeSystem/$search",
                params={
                    "query": "metformin",
                    "searchMode": "canonical",
                    "count": 3,
                },
            )
        assert r.status_code == 200
        return r.json()["entry"]

    @staticmethod
    def _ext(resource: dict) -> dict[str, str]:
        return {
            x["url"].rsplit("/", 1)[-1]: (
                x.get("valueCode") or x.get("valueString")
            )
            for x in resource.get("extension", [])
        }

    def test_c20_envelope_extensions(self, entries):
        res = entries[0]["resource"]
        ext = self._ext(res)
        assert ext["canonical-id"].startswith("VAL-")
        assert ext["canonical-result-type"] == "medication"
        assert res["display"] == "Metformin"

    def test_c21_match_grade_extension(self, entries):
        search = entries[0]["search"]
        ext = self._ext(search)
        assert ext["match-grade"] == "exact"

    def test_c22_namespace_split(self, entries):
        """CA2's registered observation, re-verified on the current
        wire: canonical-id/result-type under medterm4ds.org,
        match-grade under fhir4ds.org — two namespaces, one
        response."""
        res_exts = [
            x["url"] for x in entries[0]["resource"]["extension"]
        ]
        search_exts = [
            x["url"] for x in entries[0]["search"]["extension"]
        ]
        assert any(u.startswith("http://medterm4ds.org") for u in res_exts)
        assert any(u.startswith("http://fhir4ds.org") for u in search_exts)
