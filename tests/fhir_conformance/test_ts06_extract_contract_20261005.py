"""TS-06 $extract contract sweep (2026-10-05).

Maintenance spec-comp iteration 15 (worktree maint/spec-comp-20261005).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: first maintenance sweep of the custom $extract operation's
parameter-error contract (GET + POST forms). Every enum-ish parameter
probed with an invalid value, plus text-edges and boolean coercion.

E1 (MEDIUM, NEW) — invalid resultTypes → HTTP 500 (unhandled
    ValueError). resultTypes=bogus on GET (and valueCode on POST)
    crashes with a raw ValueError from
    search.py:_result_types_to_prefixes — the $extract route never
    wraps extract_service() in the ValueError→400 translation that
    $search applies (GLOBAL_RULES service-delegation pattern, count=6
    recurring: QC-123). Client impact: 500 + non-FHIR error body
    instead of a 400 OperationOutcome naming the valid set.
    Root cause: _do_extract (fhir_api.py:4187) calls extract_service
    with no try/except ValueError. Fix shape: validate resultTypes
    pre-dispatch (like QA-005 annotationFields) or wrap and translate.
    Pinned: e10 (GET) / e11 (POST) with flip-on-fix.

E2 (NOTE, no pin) — nerLabels=bogus silently accepted (200, zero
    matches). Labels are a free-form detection contract (default set
    documented); an unknown label simply never matches. Defensible
    soft contract; recorded so the asymmetry with resultTypes' hard
    contract is a documented decision, not an accident. If E1's fix
    hardens resultTypes, consider documenting WHY nerLabels stays soft.

Controls verified fresh (e-series): format enum guarded (bogus → 422);
mode/minGrade enums guarded (422); empty text rejected (422) per the
min_length contract; whitespace-only text accepted (200 — deliberate:
the pipeline treats it as content-free); annotationFields=bogus → 400
(QA-005 pre-NER validation held); includeNegated=banana → 422 (bool
coercion guarded); format=codes/terms → 200 Bundle on the fixture DB.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from .conftest import _make_conformance_db

EXTRACT = "/fhir/CodeSystem/$extract"


@pytest.fixture(scope="module")
def extract_client(tmp_path_factory):
    """Non-raising client — the 500 probes must observe the HTTP
    status, not surface the server's raw exception (the shared
    fhir_client uses TestClient's raising default)."""
    pytest.importorskip("fastapi")
    from medterm4ds.apps.fhir_api import (
        FhirApiSettings,
        create_fhir_app,
    )

    db_path = tmp_path_factory.mktemp("ts06") / "umls.duckdb"
    _make_conformance_db(db_path)
    settings = FhirApiSettings(
        db_path=db_path,
        memory_profile="low",
        search_index_dir=str(tmp_path_factory.mktemp("no_index")),
        prepare_cache=False,
    )
    app = create_fhir_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


class TestE1ResultTypes500:
    """E1 — invalid resultTypes crashes with 500, not 400."""

    def test_e10_get_500(self, extract_client):
        """GET resultTypes=bogus → 500 (CURRENT, deviation). Flip
        when fixed: expect 400 OperationOutcome naming valid types."""
        r = extract_client.get(
            EXTRACT,
            params={"text": "diabetes", "resultTypes": "bogus"},
        )
        assert r.status_code == 500

    def test_e11_post_500(self, extract_client):
        """POST valueCode resultTypes=bogus → 500 (same root cause
        on the POST form)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "text", "valueString": "diabetes"},
                {"name": "resultTypes", "valueCode": "bogus"},
            ],
        }
        r = extract_client.post(EXTRACT, json=body)
        assert r.status_code == 500


class TestExtractControls:
    """Controls: guarded params + happy paths, fresh."""

    @pytest.mark.parametrize(
        "param", ["format", "mode", "minGrade"]
    )
    def test_e20_enum_guards_422(self, fhir_client, param):
        r = fhir_client.get(
            EXTRACT, params={"text": "x", param: "bogus"}
        )
        assert r.status_code == 422

    def test_e21_empty_text_422(self, fhir_client):
        r = fhir_client.get(
            EXTRACT, params={"text": "", "format": "codes"}
        )
        assert r.status_code == 422

    def test_e22_whitespace_text_200(self, fhir_client):
        """Whitespace-only text is accepted (content-free input,
        empty result) — deliberate, pinned so it can't drift."""
        r = fhir_client.get(
            EXTRACT, params={"text": "   ", "format": "codes"}
        )
        assert r.status_code == 200
        assert r.json().get("resourceType") == "Bundle"

    def test_e23_annotation_fields_400(self, fhir_client):
        """QA-005 pre-NER validation held: garbage annotationFields
        fails fast with 400 (not a 500 from the executor)."""
        r = fhir_client.get(
            EXTRACT,
            params={
                "text": "diabetes",
                "format": "annotated",
                "annotationFields": "bogus",
            },
        )
        assert r.status_code == 400

    def test_e24_bool_coercion_422(self, fhir_client):
        r = fhir_client.get(
            EXTRACT,
            params={"text": "diabetes", "includeNegated": "banana"},
        )
        assert r.status_code == 422

    @pytest.mark.parametrize("fmt", ["codes", "terms"])
    def test_e25_happy_paths(self, fhir_client, fmt):
        r = fhir_client.get(
            EXTRACT, params={"text": "diabetes", "format": fmt}
        )
        assert r.status_code == 200
        assert r.json().get("resourceType") == "Bundle"
