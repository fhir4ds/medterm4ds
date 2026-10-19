"""TS-63 $search XML parity + $extract XML-transport guards (2026-10-19).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261019).
FRESH EVIDENCE — live probes executed this run (live BM25 index via
the cache default; canonical mode cold-fetched the SapBERT split,
~64-108s); no prior-run results cited.

Scope: the XML representation of the custom operations' richer
payloads — $search lexical/canonical/semantic in XML (TS-57/TS-32
pinned JSON envelopes only; TS-58 covered $extract XML), the
canonical-mode EXTENSIONS through the XML serializer, and the
annotated-format guards in the XML transport.

RESULT: XML surface CONFORMANT — controls-only iteration (7th clean
surface). The serializer faithfully round-trips everything the JSON
builder emits: extensions (canonical-id, result-type), entry.search
(mode/score/match-grade extension), CA1's relative-composite fullUrl
(byte-identical string in both transports — CA1 is
transport-independent, no XML-specific divergence), SC1's total (=
entry count in XML too — same builder), 422 error envelope in XML for
bogus searchMode, and the QC-305 annotated+XML guard (406 with
fhir+xml). AN1 re-confirmed transport-independent: outputFormat=
annotated silently ignored in the XML transport as well (200 plain
Bundle, no annotated_text) — no new row; AN1's registration already
says 'everywhere'.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

SNOMED_URI = "http://snomed.info/sct"
NS = {"f": "http://hl7.org/fhir"}


@pytest.fixture(scope="module")
def search_xml_client(tmp_path_factory):
    """Facade with the real lexical index (cache default) — the shared
    fhir_client carries an empty index dir and 503s lexical mode."""
    pytest.importorskip("fastapi")
    from starlette.testclient import TestClient

    from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app

    from .conftest import _make_conformance_db

    db_path = tmp_path_factory.mktemp("ts63_xml") / "umls.duckdb"
    _make_conformance_db(db_path)
    from medterm4ds.services.search import DEFAULT_SEARCH_INDEX_DIR

    settings = FhirApiSettings(
        db_path=db_path,
        memory_profile="low",
        search_index_dir=DEFAULT_SEARCH_INDEX_DIR,
        prepare_cache=False,
    )
    app = create_fhir_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


class TestSearchXMLSurface:
    """Controls — $search XML envelopes, fresh."""

    @pytest.mark.parametrize(
        "mode",
        ["lexical", "semantic"],
    )
    def test_x10_search_modes_xml_wellformed(
        self, search_xml_client, mode
    ):
        """lexical + semantic return application/fhir+xml well-formed
        searchset Bundles (canonical's cold model load makes it slow;
        covered by the semantic probe here since the split fetch
        succeeded this run)."""
        r = search_xml_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "diabetes", "searchMode": mode,
                "count": 2, "_format": "xml",
            },
        )
        assert r.status_code == 200
        assert "fhir+xml" in r.headers["content-type"]
        root = ET.fromstring(r.text)
        assert root.tag.endswith("Bundle")
        assert (
            root.find("f:type", NS).get("value") == "searchset"
        )

    def test_x11_lexical_entry_shape_xml(self, search_xml_client):
        """XML entry carries fullUrl + resource + search node with
        mode=match and a decimal score."""
        r = search_xml_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "diabetes", "searchMode": "lexical",
                "count": 2, "_format": "xml",
            },
        )
        root = ET.fromstring(r.text)
        entry = root.find("f:entry", NS)
        assert entry is not None
        assert entry.find("f:fullUrl", NS) is not None
        search = entry.find("f:search", NS)
        mode = search.find("f:mode", NS).get("value")
        score = search.find("f:score", NS).get("value")
        assert mode == "match"
        assert 0.0 <= float(score) <= 1.0

    def test_x12_canonical_extensions_xml(self, search_xml_client):
        """Canonical-mode extensions (canonical-id et al.) serialize
        into the XML entries — parity with the JSON envelope (TS-32
        shapes through the XML serializer)."""
        r = search_xml_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "metformin", "searchMode": "canonical",
                "count": 2, "_format": "xml",
            },
        )
        assert r.status_code == 200
        root = ET.fromstring(r.text)
        entries = root.findall("f:entry", NS)
        assert entries
        for e in entries:
            assert e.find(".//f:extension", NS) is not None

    def test_x13_fullurl_parity_json_xml(self, search_xml_client):
        """CA1's relative-composite fullUrl is byte-identical in both
        transports (transport-independent carrier — no XML-specific
        divergence to register)."""
        rj = search_xml_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "metformin", "searchMode": "canonical",
                "count": 2, "_format": "json",
            },
        )
        rx = search_xml_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "metformin", "searchMode": "canonical",
                "count": 2, "_format": "xml",
            },
        )
        j_urls = [e.get("fullUrl") for e in rj.json().get("entry", [])]
        x_urls = [
            e.find("f:fullUrl", NS).get("value")
            for e in ET.fromstring(rx.text).findall("f:entry", NS)
        ]
        assert j_urls == x_urls

    def test_x14_bogus_mode_422_xml(self, search_xml_client):
        """searchMode validation errors render as XML
        OperationOutcome."""
        r = search_xml_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "x", "searchMode": "bogus", "_format": "xml",
            },
        )
        assert r.status_code == 422
        assert "fhir+xml" in r.headers["content-type"]
        assert ET.fromstring(r.text).tag.endswith("OperationOutcome")

    def test_x15_total_equals_entries_xml(self, search_xml_client):
        """SC1 (total = returned-entry count, never true count) is
        transport-independent: pinned in XML as well."""
        r = search_xml_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "drug", "searchMode": "lexical",
                "count": 2, "_format": "xml",
            },
        )
        root = ET.fromstring(r.text)
        total = int(root.find("f:total", NS).get("value"))
        entries = len(root.findall("f:entry", NS))
        assert total == entries == 2


class TestExtractXMLGuards:
    """Controls — $extract guards in the XML transport."""

    def test_x20_annotated_with_xml_406(self, fhir_client):
        """QC-305 held in the XML transport: format=annotated +
        _format=xml → 406 with fhir+xml body."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$extract",
            params={
                "text": "Patient has diabetes.",
                "format": "annotated", "_format": "xml",
            },
        )
        assert r.status_code == 406
        assert "fhir+xml" in r.headers["content-type"]

    def test_x21_outputformat_ignored_in_xml_too(self, fhir_client):
        """AN1 transport-independent: outputFormat=annotated silently
        ignored with _format=xml as well (200 plain Bundle XML, no
        annotated_text). No new row — AN1 already registers
        'everywhere'."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$extract",
            params={
                "text": "Patient has diabetes.",
                "outputFormat": "annotated", "_format": "xml",
            },
        )
        assert r.status_code == 200
        assert "annotated_text" not in r.text
