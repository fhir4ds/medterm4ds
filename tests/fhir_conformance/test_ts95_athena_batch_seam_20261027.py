"""TS-95 Athena 4b seam transport coverage (2026-10-27).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261027b).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the 4b seam's TRANSPORT coverage — $batch entries and XML
were never probed (the direct GET/POST pins predate the seam's
merge; the BX family taught that the dispatcher is its own surface).

AS3 (MEDIUM, NEW) — $batch entries CANNOT select the athena
    ConceptMap: the dispatcher's $translate branch never forwards
    map_url (the direct routes added it; the batch call site was
    missed — the exact cross-handler-helper-wiring class the N1/N2
    fix comment warns about). Consequence: url=urn:medterm4ds:
    athena-conceptmap in a batch entry silently falls to the
    CROSSWALK path, where targetSystem=urn:athena:concept-id fails
    system resolution → 400 'Unrecognized target system URI' both
    transports, while the IDENTICAL request succeeds direct. Fix
    shape: forward the resolved url in the dispatcher's
    _do_translate call (one argument, mirroring the direct route).
    Pinned as30/as31/as32.

Controls (fresh): the direct-route seam still GREEN on all six
    contract pins (happy equivalent / deprecated unmatched / wrong
    target 400 / reverse 400 / unknown-code false / default
    crosswalk unchanged — re-verified in this worktree post-merge);
    the seam serializes to XML correctly (fhir+xml, well-formed).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

SNOMED_URI = "http://snomed.info/sct"
ACM = "urn:medterm4ds:athena-conceptmap"
TGT = "urn:athena:concept-id"
NS = {"f": "http://hl7.org/fhir"}


@pytest.fixture(scope="module")
def seam_client(tmp_path_factory):
    import duckdb
    from starlette.testclient import TestClient

    from medterm4ds.apps.fhir_api import (
        FhirApiSettings,
        create_fhir_app,
    )
    from medterm4ds.services.athena import ingest_athena

    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts95") / "m.duckdb"
    _make_conformance_db(db)
    con = duckdb.connect(str(db))
    ingest_athena(
        con, "/tmp/opencode/athena-mini",
        vocabularies=("SNOMED",),
        accept_licenses=True, licenses_accepted_at="ts95",
    )
    con.close()
    app = create_fhir_app(FhirApiSettings(
        db_path=db, memory_profile="low",
        search_index_dir=str(db.parent / "noi"),
        prepare_cache=False,
    ))
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


class TestAS3BatchCannotSelect:
    """AS3 — the dispatcher never forwards map_url."""

    def test_as30_batch_get_400(self, seam_client):
        """Batch GET entry with the athena url: 400 (falls to the
        crosswalk where the concept-id target fails resolution).
        Flip when the dispatcher forwards map_url."""
        batch = {
            "resourceType": "Bundle", "type": "batch",
            "entry": [{
                "request": {
                    "method": "GET",
                    "url": (
                        "ConceptMap/$translate"
                        f"?url={ACM}&system={SNOMED_URI}"
                        "&code=44054006"
                        f"&targetSystem={TGT}"
                    ),
                },
            }],
        }
        r = seam_client.post("/fhir", json=batch)
        status = r.json()["entry"][0]["response"]["status"]
        assert status == "400"

    def test_as31_batch_post_400(self, seam_client):
        batch = {
            "resourceType": "Bundle", "type": "batch",
            "entry": [{
                "request": {
                    "method": "POST",
                    "url": "ConceptMap/$translate",
                },
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "url", "valueUri": ACM},
                        {"name": "system", "valueUri": SNOMED_URI},
                        {"name": "code", "valueCode": "44054006"},
                        {"name": "targetSystem", "valueUri": TGT},
                    ],
                },
            }],
        }
        r = seam_client.post("/fhir", json=batch)
        status = r.json()["entry"][0]["response"]["status"]
        assert status == "400"

    def test_as32_direct_route_works(self, seam_client):
        """Premise control: the IDENTICAL request succeeds direct —
        the divergence is the dispatcher, not the seam."""
        r = seam_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "url": ACM, "system": SNOMED_URI,
                "code": "44054006", "targetSystem": TGT,
            },
        )
        assert r.status_code == 200


class TestTS95Controls:
    """Controls — the direct seam held post-merge + XML."""

    def test_c10_direct_equivalent(self, seam_client):
        r = seam_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "url": ACM, "system": SNOMED_URI,
                "code": "44054006", "targetSystem": TGT,
            },
        )
        match = next(
            p for p in r.json()["parameter"]
            if p.get("name") == "match"
        )
        concept = next(
            q.get("valueCoding")
            for q in match["part"]
            if q.get("name") == "concept"
        )
        assert concept["code"] == "21602787"

    def test_c11_default_crosswalk_unchanged(self, seam_client):
        r = seam_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": "http://hl7.org/fhir/sid/icd-10-cm",
                "code": "E11", "targetSystem": SNOMED_URI,
            },
        )
        assert r.status_code == 200

    def test_c12_xml_seam(self, seam_client):
        r = seam_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "url": ACM, "system": SNOMED_URI,
                "code": "44054006", "targetSystem": TGT,
                "_format": "xml",
            },
        )
        assert r.status_code == 200
        assert "fhir+xml" in r.headers["content-type"]
        root = ET.fromstring(r.text)
        # FHIR XML: the concept_id rides as <code value="..."/> inside
        # <valueCoding> — an element named code, not valueCode.
        found = any(
            n.get("value") == "21602787"
            for n in root.iter("{http://hl7.org/fhir}code")
        )
        assert found
