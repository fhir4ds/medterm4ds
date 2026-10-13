"""TS-36 $extract annotated-marker contract (2026-10-13).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261013b).
FRESH EVIDENCE — live probes executed this run on the real GLiNER +
ConText pipeline (~120-250s cold per probe); no prior-run results
cited.

Scope: the annotated output format's MARKER semantics — the format
that embeds machine-readable spans in clinical text (the highest-
stakes render path). TS-24 pinned the codes-format record; this
suite pins the annotated document contract (format=annotated,
annotationFields, marker composition, span alignment, negation
markers, QC-305's XML rejection).

AN findings: NONE — the marker contract is CONFORMANT end to end.
    This is a CONTROLS iteration (the third clean surface after
    TS-28 discovery and TS-33 resolve-mode); everything pinned so
    drift fails loudly:

    - Default marker composition [text|type] ('[type 2 diabetes|
      disorder]'); each annotationFields selection reshapes the
      marker deterministically (text → bare text; name → canonical
      display; status,type → '[affirmed|disorder]'; canonical_id →
      '[VAL-COND-SNOMED-44054006]').
    - annotationFields validated 400 on unknown field names (the
      enum is enforced, unlike XA1's param-level gap).
    - Transport parity: POST body valueString binds identically.
    - Empty annotationFields = the default (documented empty-string
      convention).
    - Span alignment: every span's [start, end) slices the ORIGINAL
      input to exactly its text (verified on negated AND affirmed
      assertions).
    - Negation markers carry status (with includeNegated:
      '[negated|hypertension]' alongside '[affirmed|type 2
      diabetes]') — status is marker-addressable.
    - QC-305 held: format=annotated + XML negotiation → 406 with a
      fhir+xml OperationOutcome (annotated is non-FHIR JSON).
    - outputFormat (the CLI's flag name) is NOT a FHIR body param —
      sending it rides XA1's registered unknown-body-param gap
      (codes-format Bundle returns); noted here for the naming-
      asymmetry record, not a new finding.
"""

from __future__ import annotations

import pytest

TEXT = "No evidence of hypertension. Patient has type 2 diabetes."


@pytest.fixture(scope="module")
def warm_client(fhir_client):
    """The GLiNER model load is ~120-250s cold; the shared fhir_client
    fixture is module-scoped so probes within this suite amortize it
    (the FIRST extract probe pays the cold cost once)."""
    # Warm the pipeline with one tiny call.
    fhir_client.get(
        "/fhir/CodeSystem/$extract", params={"text": "diabetes"}
    )
    return fhir_client


class TestMarkerComposition:
    """Controls — marker composition per annotationFields."""

    def test_a10_default_marker(self, warm_client):
        r = warm_client.get(
            "/fhir/CodeSystem/$extract",
            params={"text": TEXT, "format": "annotated"},
        )
        assert r.status_code == 200
        assert "[type 2 diabetes|disorder]" in r.json()["annotated_text"]

    @pytest.mark.parametrize(
        "fields,expected",
        [
            ("text", "[type 2 diabetes]"),
            ("name", "[Type 2 Diabetes]"),
            ("status,type", "[affirmed|disorder]"),
            ("canonical_id", "[VAL-COND-SNOMED-44054006]"),
        ],
    )
    def test_a11_field_selection(
        self, warm_client, fields, expected
    ):
        r = warm_client.get(
            "/fhir/CodeSystem/$extract",
            params={
                "text": TEXT,
                "format": "annotated",
                "annotationFields": fields,
            },
        )
        assert r.status_code == 200
        assert expected in r.json()["annotated_text"]

    def test_a12_bogus_field_400(self, warm_client):
        r = warm_client.get(
            "/fhir/CodeSystem/$extract",
            params={
                "text": TEXT,
                "format": "annotated",
                "annotationFields": "bogus",
            },
        )
        assert r.status_code == 400

    def test_a13_empty_fields_default(self, warm_client):
        r = warm_client.get(
            "/fhir/CodeSystem/$extract",
            params={
                "text": TEXT,
                "format": "annotated",
                "annotationFields": "",
            },
        )
        assert "[type 2 diabetes|disorder]" in (
            r.json()["annotated_text"]
        )

    def test_a14_post_body_fields(self, warm_client):
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "text", "valueString": TEXT},
                {"name": "format", "valueCode": "annotated"},
                {"name": "annotationFields", "valueString": "name"},
            ],
        }
        r = warm_client.post(
            "/fhir/CodeSystem/$extract", json=body
        )
        assert r.status_code == 200
        assert "[Type 2 Diabetes]" in r.json()["annotated_text"]


class TestSpanAlignment:
    """Controls — spans index the original text exactly."""

    def test_a20_spans_slice_original(self, warm_client):
        r = warm_client.get(
            "/fhir/CodeSystem/$extract",
            params={
                "text": TEXT,
                "format": "annotated",
                "includeNegated": "true",
            },
        )
        spans = r.json()["spans"]
        assert spans, "both assertions surface under includeNegated"
        for s in spans:
            assert TEXT[s["start"]:s["end"]] == s["text"]

    def test_a21_negation_marker_status(self, warm_client):
        """With includeNegated, the negated assertion carries its
        status INTO the marker ([negated|hypertension])."""
        r = warm_client.get(
            "/fhir/CodeSystem/$extract",
            params={
                "text": TEXT,
                "format": "annotated",
                "includeNegated": "true",
                "annotationFields": "status,text",
            },
        )
        annotated = r.json()["annotated_text"]
        assert "[negated|hypertension]" in annotated
        assert "[affirmed|type 2 diabetes]" in annotated


class TestAnnotatedEnvelope:
    """Controls — the non-FHIR envelope + QC-305."""

    def test_a30_document_shape(self, warm_client):
        r = warm_client.get(
            "/fhir/CodeSystem/$extract",
            params={"text": TEXT, "format": "annotated"},
        )
        body = r.json()
        assert set(body) == {"annotated_text", "concepts", "spans"}
        assert "resourceType" not in body  # non-FHIR by design

    def test_a31_annotated_xml_406(self, warm_client):
        """QC-305 held: annotated + XML negotiation → 406 (annotated
        is a non-FHIR JSON document)."""
        r = warm_client.get(
            "/fhir/CodeSystem/$extract",
            params={
                "text": TEXT,
                "format": "annotated",
                "_format": "xml",
            },
        )
        assert r.status_code == 406
        assert "fhir+xml" in r.headers["content-type"]
