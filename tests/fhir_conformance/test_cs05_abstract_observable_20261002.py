"""CF-SKEPTIC-CS05-01/02 abstract+inactive observability suite (2026-10-02).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261002d).
FRESH EVIDENCE — every probe executed this run against a purpose-built
fixture; no prior-run results cited.

Background (carry-forward registry):
  CF-SKEPTIC-CS05-01 (LOW, FINDING CANDIDATE — DEFERRED):
  ``engines/fhir/responses.py:build_parameters_lookup`` hardcodes
  ``_param("abstract", False, "valueBoolean")`` regardless of the
  concept's actual abstractness. Per FHIR R4 $lookup Out ``abstract``:
  "True if this code is abstract" (boolean 1..1). The drift is real but
  the shared fixture couldn't exercise it — the reproduction shape the
  CF demanded: a concept whose atoms are ALL non-preferred-terms, which
  is how UMLS represents abstract SNOMED hierarchy nodes (no PT atom).

  CF-SKEPTIC-CS05-02 (LOW — DEFERRED): ``inactive`` property never
  emitted; spec says servers SHOULD surface ``inactive=true`` for
  deprecated concepts. Related but distinct — pinned here only as a
  shape check (no inactive concept is returned at all in active_only
  mode; see d70/d71).

Fixture (private to this suite, seeded SNOMEDCT_US):
  15632000 "Galloyl" — an abstract SNOMED-style hierarchy node:
    mrconso carries TTY='HT' (hierarchical term) + TTY='SY' atoms but NO
    'PT' atom. The lookup SQL ranks PT > MH > LN > else; with no PT the
    best atom is the HT row — the shape a real abstract node produces.
  99900001 "Deprecated concept X" — SUPPRESS='O' (obsolete): invisible
    to active_only lookups (the SUPPRESS='N' filter).

Spec citations:
  $lookup abstract: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
    Out param ``abstract``: "True if this code is abstract" (1..1 boolean).
  concept-properties inactive: https://hl7.org/fhir/R4/concept-properties.html
    "inactive: True if this concept is no longer valid/should not be used."
"""

from __future__ import annotations

import pytest


SNOMED_URI = "http://snomed.info/sct"


@pytest.fixture(scope="module")
def abstract_client(tmp_path_factory):
    pytest.importorskip("fastapi")
    from starlette.testclient import TestClient

    from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app
    from .conftest import _make_conformance_db

    db_path = tmp_path_factory.mktemp("cs05_abstract") / "umls.duckdb"
    _make_conformance_db(db_path)

    import duckdb

    con = duckdb.connect(str(db_path))
    # Abstract-node shape: HT (hierarchy term) + SY atoms, NO PT atom.
    con.executemany(
        "INSERT INTO mrconso VALUES (?, ?, ?, ?, 'N', 'SNOMEDCT_US', 'C15632000')",
        [
            ("15632000", "HT", "Galloyl", "A15632000"),
            ("15632000", "SY", "Galloyl (substance)", "A15632001"),
        ],
    )
    # Obsolete concept: SUPPRESS='O' — excluded by active_only lookups.
    con.executemany(
        "INSERT INTO mrconso VALUES (?, ?, ?, ?, 'O', 'SNOMEDCT_US', 'C99900001')",
        [("99900001", "PT", "Deprecated concept X", "A99900001")],
    )
    con.close()

    settings = FhirApiSettings(
        db_path=db_path,
        memory_profile="low",
        search_index_dir=str(tmp_path_factory.mktemp("no_index")),
        prepare_cache=False,
    )
    app = create_fhir_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _lookup(client, code):
    r = client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": SNOMED_URI, "code": code},
    )
    return r


class TestAbstractNodeLookup:
    """CF-SKEPTIC-CS05-01: the abstract-node reproduction shape."""

    def test_a10_abstract_node_resolves(self, abstract_client):
        """The abstract node IS resolvable via $lookup (best atom = HT row
        in the absence of PT) — the precondition for exercising the
        hardcoded abstract=False drift."""
        r = _lookup(abstract_client, "15632000")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["resourceType"] == "Parameters"
        display = next(
            p["valueString"] for p in body["parameter"]
            if p["name"] == "display"
        )
        assert display == "Galloyl"

    def test_a20_abstract_param_present_and_boolean(self, abstract_client):
        """$lookup Out `abstract` is present (1..1) and a boolean — the
        wire-shape contract holds."""
        r = _lookup(abstract_client, "15632000")
        body = r.json()
        entries = [p for p in body["parameter"] if p["name"] == "abstract"]
        assert len(entries) == 1, "abstract Out param must be exactly 1..1"
        assert isinstance(entries[0].get("valueBoolean"), bool)

    def test_a30_abstract_hardcoded_false_drift_observed(self, abstract_client):
        """THE DRIFT, now observable: an abstract-shaped concept (no PT
        atom — the UMLS representation of abstract hierarchy nodes) gets
        ``abstract=false`` from the hardcoded builder value.

        Per FHIR R4 $lookup: abstract = "True if this code is abstract".
        The fixture concept is abstract-shaped; a correct implementation
        deriving abstractness from the atom data would answer true.

        Carry-forward-as-probe pin: when the fix lands (derive from
        atom/TTY data per the CF's documented fix shape), FLIP this
        assertion to ``is True``.
        """
        r = _lookup(abstract_client, "15632000")
        body = r.json()
        abstract = next(
            p["valueBoolean"] for p in body["parameter"]
            if p["name"] == "abstract"
        )
        assert abstract is False, (
            "abstract=True observed for the abstract-shaped node — the "
            "derivation fix appears to have landed; flip this pin and "
            "update AGENTS.md CF-SKEPTIC-CS05-01 to RESOLVED."
        )

    def test_a40_concrete_concept_abstract_false_correct(self, abstract_client):
        """Control: a concrete concept (PT atom present, from the shared
        fixture) correctly reports abstract=false — the hardcoded value
        is only wrong for abstract nodes, not universally."""
        r = _lookup(abstract_client, "44054006")
        assert r.status_code == 200
        body = r.json()
        abstract = next(
            p["valueBoolean"] for p in body["parameter"]
            if p["name"] == "abstract"
        )
        assert abstract is False


class TestInactiveConceptLookup:
    """CF-SKEPTIC-CS05-02: the inactive/obsolete concept shape."""

    def test_d70_obsolete_concept_not_found_in_active_only(self, abstract_client):
        """SUPPRESS='O' concept: $lookup (active_only) returns the
        API's not-found shape (200 + OperationOutcome not-found — the
        documented $lookup miss encoding). The CF notes servers SHOULD
        surface ``inactive=true`` instead of pure not-found; that
        enhancement remains deferred. Pin the current shape so the
        enhancement landing changes it loudly: if a Parameters resource
        comes back, it MUST carry the ``inactive`` property."""
        r = _lookup(abstract_client, "99900001")
        body = r.json()
        if body.get("resourceType") == "Parameters":
            assert any(
                p["name"] == "property" and p.get("part") and any(
                    part.get("valueCode") == "inactive"
                    for part in p["part"]
                )
                for p in body["parameter"]
            ), (
                "Obsolete concept now resolves WITHOUT the inactive "
                "property — CF-SKEPTIC-CS05-02 enhancement half-landed; "
                "tighten this probe."
            )
        else:
            assert body["resourceType"] == "OperationOutcome", body
            assert body["issue"][0]["code"] == "not-found"

    def test_d71_no_inactive_property_on_active_concepts(self, abstract_client):
        """Control: active concepts MUST NOT carry an ``inactive``
        property (absence is conformant — only deprecated concepts
        SHOULD have it)."""
        r = _lookup(abstract_client, "44054006")
        body = r.json()
        for p in body["parameter"]:
            if p["name"] == "property" and p.get("part"):
                codes = [
                    part.get("valueCode") for part in p["part"]
                    if part.get("name") == "code"
                ]
                assert "inactive" not in codes
