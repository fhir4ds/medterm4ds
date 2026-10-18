"""TS-58 $extract outputFormat bypass + XML envelope parity (2026-10-18).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261018).
FRESH EVIDENCE — live probes executed this run (warm GLiNER pipeline
after first fetch); no prior-run results cited.

Scope: $extract's XML serialization (first check: Bundle serializes
well-formed, JSON↔XML code parity holds, entry counts match) —
CONFORMANT. The sweep surfaced a param-name split in the annotated
guard.

AN1 (MEDIUM, NEW) — the annotated-format guard is PARAM-NAME
    dependent; outputFormat is accepted-and-ignored everywhere.
    format=annotated + XML → 406 fhir+xml OperationOutcome (QC-305
    held, TS-36 re-verified green this run). outputFormat=annotated +
    XML → 200 PLAIN Bundle — the annotated content is silently
    dropped and the client receives the default extraction labeled
    success. In JSON mode outputFormat=annotated returns the plain
    Bundle too (no annotated_text) on GET, POST body, and with
    Accept-xml — i.e. outputFormat is never consulted for anything,
    while being a NAME THE CLI ITSELF USES (TS-24 CLI1: the CLI's
    --outputFormat flag). A client porting from the CLI (or guessing
    the param from its name) gets a silent contract miss. TS-36's
    XA1 note registered only the unknown-body-param dimension;
    the accepted-and-ignored GET/POST dimension is new.
    Fix shape: alias outputFormat to format (one line at param
    intake), or 400 on outputFormat naming 'format'. Flip pins
    an10-an13.

Controls (fresh): JSON↔XML CONTENT parity (same codes, same entry
    counts, same text); XML well-formed Bundle root; annotated via
    the 'format' param still 406s under XML and still emits
    annotated_text under JSON (QC-305 + TS-36 held).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

pytestmark = pytest.mark.filterwarnings("ignore::FutureWarning")

NS = {"f": "http://hl7.org/fhir"}
TEXT = "Patient has type 2 diabetes and takes metformin."


@pytest.fixture(scope="module")
def extract_client(tmp_path_factory):
    """Extract-capable client (module-scoped: the first request pays
    the ~90-190s cold GLiNER load; the suite amortizes it)."""
    pytest.importorskip("fastapi")
    from starlette.testclient import TestClient

    from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app

    from .conftest import _make_conformance_db

    db_path = tmp_path_factory.mktemp("ts58") / "umls.duckdb"
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


def _codes_from_json(body: dict) -> list[str]:
    return [e["resource"]["code"] for e in body.get("entry", [])]


def _codes_from_xml(text: str) -> list[str]:
    root = ET.fromstring(text)
    codes = []
    for e in root.findall("f:entry", NS):
        res = e.find("f:resource", NS)
        if res is not None:
            code_el = res.find(".//f:code", NS)
            if code_el is not None:
                codes.append(code_el.get("value"))
    return codes


class TestAN1OutputFormatBypass:
    """AN1 — outputFormat accepted-and-ignored everywhere."""

    def test_an10_get_outputformat_annotated_xml_200(
        self, extract_client
    ):
        """outputFormat=annotated + _format=xml → 200 PLAIN Bundle
        (annotated silently dropped). Flip when outputFormat aliases
        format (→406) or 400s."""
        r = extract_client.get(
            "/fhir/CodeSystem/$extract",
            params={"text": TEXT, "outputFormat": "annotated",
                    "_format": "xml"},
        )
        assert r.status_code == 200
        assert r.text.startswith("<?xml")
        assert "Bundle" in r.text

    def test_an11_get_outputformat_annotated_json_plain(
        self, extract_client
    ):
        """JSON mode too: outputFormat=annotated returns the default
        Bundle — no annotated_text. Flip with AN1."""
        r = extract_client.get(
            "/fhir/CodeSystem/$extract",
            params={"text": TEXT, "outputFormat": "annotated"},
        )
        assert r.status_code == 200
        assert "annotated_text" not in r.json()

    def test_an12_post_body_outputformat_ignored(self, extract_client):
        """POST Parameters outputFormat=annotated — same silent
        default. Flip with AN1."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "text", "valueString": TEXT},
                {"name": "outputFormat", "valueString": "annotated"},
            ],
        }
        r = extract_client.post("/fhir/CodeSystem/$extract", json=body)
        assert r.status_code == 200
        assert "annotated_text" not in r.json()

    def test_an13_format_param_still_guards(self, extract_client):
        """CONTROL: the spec param (format=annotated) still 406s under
        XML — QC-305 held; the bypass is name-specific."""
        r = extract_client.get(
            "/fhir/CodeSystem/$extract",
            params={"text": TEXT, "format": "annotated",
                    "_format": "xml"},
        )
        assert r.status_code == 406


class TestXMLParityControls:
    """Controls — the XML envelope itself is sound."""

    def test_c10_content_parity(self, extract_client):
        rj = extract_client.get(
            "/fhir/CodeSystem/$extract",
            params={"text": TEXT, "_format": "json"},
        )
        rx = extract_client.get(
            "/fhir/CodeSystem/$extract",
            params={"text": TEXT, "_format": "xml"},
        )
        assert rj.status_code == rx.status_code == 200
        codes_j = _codes_from_json(rj.json())
        codes_x = _codes_from_xml(rx.text)
        assert sorted(codes_j) == sorted(codes_x)
        assert len(rj.json()["entry"]) == len(codes_x)

    def test_c11_xml_bundle_wellformed(self, extract_client):
        r = extract_client.get(
            "/fhir/CodeSystem/$extract",
            params={"text": TEXT, "_format": "xml"},
        )
        assert "fhir+xml" in r.headers["content-type"]
        root = ET.fromstring(r.text)
        assert root.tag.endswith("Bundle")

    def test_c12_annotated_format_json_holds(self, extract_client):
        """format=annotated JSON still emits annotated_text (TS-36
        contract held at the spec param)."""
        r = extract_client.get(
            "/fhir/CodeSystem/$extract",
            params={"text": TEXT, "format": "annotated"},
        )
        assert r.status_code == 200
        assert "annotated_text" in r.json()
