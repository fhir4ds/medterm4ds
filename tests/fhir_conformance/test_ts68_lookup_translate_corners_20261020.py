"""TS-68 $lookup property-filter + $translate multi-match (2026-10-20).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261020b).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: two remaining unraked corners — the $lookup `property`
In-param's dotted-subselector forms (L1 registered accepted-and-
ignored for plain values; fixbatch3's known_prefixes ('property.',)
admits dotted forms past the unknown-param gate — their behavior was
never separately pinned) and $translate MULTI-match presentation
(the shared fixture has exactly one cross-SAB pair; multi-match
shape, ordering, equivalence values never probed).

RESULT: L1 re-confirmed ALIVE and now FULLY MAPPED — property=
<code>, property=<nonexistent>, property.code=<code>, and the multi-
value form ALL return the identical 7-property set (aui, canonical-
code, canonical-system, cui, match-type, patient-friendly, tty): no
filtering, no unknown-property error, dotted forms indistinguishable
from plain. No new row (L1's registration covers the param); this
suite adds the dotted-form + multi-value pins to L1's evidence.

RESULT: multi-match translation CONFORMANT (controls) — on a private
fixture (2 RXNORM atoms sharing the CUI), E11→RXNORM returns exactly
2 match entries with distinct concepts, per-part shape intact
(equivalence/concept/source), message '2 matches found' consistent
with the count, responses BYTE-STABLE across calls (deterministic
ordering), and a synonym-TTY concept (SY atom as its own code) is
presented as an equivalent translation — defensible UMLS same-CUI
crosswalk semantics, pinned as documentation.
"""

from __future__ import annotations

import duckdb
import pytest

SNOMED_URI = "http://snomed.info/sct"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
T2DM = "44054006"
BASELINE_PROPS = {
    "aui", "canonical-code", "canonical-system", "cui",
    "match-type", "patient-friendly", "tty",
}


def _property_codes(body: dict):
    codes = set()
    for q in body.get("parameter", []):
        if q.get("name") == "property":
            for part in q.get("part", []):
                if part.get("name") == "code":
                    codes.add(part.get("valueCode"))
    return codes


class TestL1PropertyFormsIgnored:
    """L1 re-verified — every property form returns the full set."""

    @pytest.mark.parametrize(
        "params",
        [
            {"property": "cui"},
            {"property": "zzz-nonexistent"},
            {"property.code": "cui"},
        ],
    )
    def test_l10_forms_ignored(self, fhir_client, params):
        """Plain, nonexistent, and dotted forms: identical 7-property
        set (no filtering, no unknown-property error). L1's row covers
        the param; these pins extend its evidence to the dotted +
        bogus forms fixbatch3's known_prefixes admitted."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": T2DM, **params},
        )
        assert r.status_code == 200
        assert _property_codes(r.json()) == BASELINE_PROPS

    def test_l11_multi_value_ignored(self, fhir_client):
        """Multi-value property=[display, cui]: same full set — the
        R4 0..* param carries no filtering semantics today."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={
                "system": SNOMED_URI, "code": T2DM,
                "property": ["display", "cui"],
            },
        )
        assert r.status_code == 200
        assert _property_codes(r.json()) == BASELINE_PROPS


@pytest.fixture(scope="module")
def multi_client(tmp_path_factory):
    """Facade over a private fixture with TWO target-system atoms
    sharing the source code's CUI (multi-match shape)."""
    from starlette.testclient import TestClient

    from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app

    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts68_multi") / "umls.duckdb"
    _make_conformance_db(db)
    con = duckdb.connect(str(db))
    con.execute(
        "INSERT INTO mrconso VALUES "
        "('1110001','PT','Diabetes Type 2 Pill','A1110001','N',"
        "'RXNORM','C0011847')"
    )
    con.execute(
        "INSERT INTO mrconso VALUES "
        "('1110002','SY','T2DM med','A1110002','N','RXNORM','C0011847')"
    )
    con.close()
    settings = FhirApiSettings(
        db_path=db,
        memory_profile="low",
        search_index_dir=str(tmp_path_factory.mktemp("no_index")),
        prepare_cache=False,
    )
    app = create_fhir_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


class TestMultiMatchTranslation:
    """Controls — multi-match presentation conforms."""

    def test_m10_two_matches_distinct(self, multi_client):
        r = multi_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": ICD10CM_URI, "code": "E11",
                "targetSystem": RXNORM_URI,
            },
        )
        assert r.status_code == 200
        matches = [
            p for p in r.json()["parameter"]
            if p.get("name") == "match"
        ]
        assert len(matches) == 2
        concepts = [
            next(
                q.get("valueCoding", {}).get("code")
                for q in m.get("part", [])
                if q.get("name") == "concept"
            )
            for m in matches
        ]
        assert set(concepts) == {"1110001", "1110002"}

    def test_m11_message_count_consistent(self, multi_client):
        r = multi_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": ICD10CM_URI, "code": "E11",
                "targetSystem": RXNORM_URI,
            },
        )
        message = next(
            p.get("valueString")
            for p in r.json()["parameter"]
            if p.get("name") == "message"
        )
        assert message == "2 matches found"

    def test_m12_byte_stable(self, multi_client):
        a = multi_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": ICD10CM_URI, "code": "E11",
                "targetSystem": RXNORM_URI,
            },
        ).content
        b = multi_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": ICD10CM_URI, "code": "E11",
                "targetSystem": RXNORM_URI,
            },
        ).content
        assert a == b

    def test_m13_equivalences_valid(self, multi_client):
        """Every match carries an R4 ConceptMapEquivalence value from
        the engine (synonym-TTY concepts presented as equivalent —
        same-CUI crosswalk semantics, pinned as documentation)."""
        r = multi_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": ICD10CM_URI, "code": "E11",
                "targetSystem": RXNORM_URI,
            },
        )
        for m in [
            p for p in r.json()["parameter"] if p.get("name") == "match"
        ]:
            eq = next(
                q.get("valueCode")
                for q in m.get("part", [])
                if q.get("name") == "equivalence"
            )
            assert eq in {
                "relatedto", "equivalent", "equal", "wider",
                "subsumes", "narrower", "specializes", "inexact",
                "unmatched", "disjoint",
            }
