"""TS-105 athena bridge identity-vocabulary equivalence end-to-end (2026-10-30).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261030c).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the AJ1 identity-vocabulary equivalence rule (NATIVE_IDENTITY_VOCABS:
ICD10CM/ATC/NDC/HCPCS... — code → own concept_id is an IDENTITY map, so
equivalence must be 'equivalent' regardless of the standard_concept flag,
which the real download leaves NULL for ICD10CM) has only ever been probed
at the REGISTRY level (ts88 aj11 asserts set membership) and via the export
converter on real data. The FHIR $translate SEAM path
(_do_translate_athena_bridge in fhir_api.py) with an Athena DB actually
CARRYING identity-vocab rows was never exercised — the mini fixture has
only SNOMED + RxNorm rows.

Probes (fresh, mini-Athena DB extended with identity-vocab rows):
  I1. ICD10CM code (standard_concept NULL — the REAL download's shape)
      → $translate url=urn:medterm4ds:athena-conceptmap → match with
      equivalence='equivalent' (the AJ1 rule through the live seam).
  I2. same for ATC (the other AJ1 census vocab).
  I3. RxNorm Extension row (non-standard, SAME FHIR URI as RxNorm,
      explicitly excluded by the bridge's vocab filter) → identity rule
      must NOT apply — the exclusion holds end-to-end.
  I4. deprecated SNOMED row (invalid_reason D + S) → 'unmatched'
      per the bridge's precedence, re-verified through the seam.
  I5. ICD10PCS identity row → 'equivalent'.
  I6. unknown code in ICD10CM → EMPTY match list (200, no match) —
      the no-row branch through the seam.

Controls: direct-route translate happy path on the SNOMED row still
'equivalent' (c1); unknown target still 400 (c2).

RESULT: ZERO FINDINGS — controls-only (18th clean surface). 8/8
fresh probes PASS: ICD10CM/ATC NULL-standard rows → 'equivalent'
through the live seam (AJ1 holds end-to-end); ICD10PCS S-row also
equivalent; RxNorm Extension correctly EXCLUDED (empty match, not
identity); deprecated SNOMED → 'unmatched'; unknown ICD10CM code →
empty match list; controls green. The equivalence surface the ts88
registry-membership probe could not reach is now pinned.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10PCS_URI = "http://hl7.org/fhir/sid/icd-10-pcs"
ATC_URI = "http://www.whocc.no/atc"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
ACM = "urn:medterm4ds:athena-conceptmap"
TGT = "urn:athena:concept-id"


@pytest.fixture(scope="module")
def bridge_client(athena_mini_dir, tmp_path_factory):
    """Mini Athena fixture EXTENDED with identity-vocab rows (the real
    download's shapes: ICD10CM/ATC standard_concept NULL; an RxNorm
    Extension row that must stay excluded)."""
    import duckdb
    from starlette.testclient import TestClient

    from medterm4ds.apps.fhir_api import (
        FhirApiSettings,
        create_fhir_app,
    )

    # extend the mini fixture's CONCEPT.csv in a scratch copy
    scratch = tmp_path_factory.mktemp("athena-idv")
    concept = athena_mini_dir / "CONCEPT.csv"
    rows = concept.read_text(encoding="utf-8")
    rows += (
        # ICD10CM E11.9 — standard_concept NULL (real-download shape)
        "30001\tType 2 diabetes mellitus without complications\tCondition\t"
        "ICD10CM\tICD10CM code\t\tE119\t19700101\t20991231\t\n"
        # ATC A10BA02 — standard_concept NULL (real-download shape)
        "30002\tMETFORMIN\tDrug\tATC\tATC level 5\t\tA10BA02\t"
        "19700101\t20991231\t\n"
        # ICD10PCS identity row — standard S (some rows do carry S)
        "30003\tResection of upper arm skin\tProcedure\tICD10PCS\t"
        "ICD10PCS code\tS\t0DT00ZZ\t19700101\t20991231\t\n"
        # RxNorm Extension row — SAME FHIR URI as RxNorm; the bridge's
        # vocab filter excludes it explicitly (RxNorm Extension != RxNorm)
        "30004\tmetformin 500 MG [DSC]\tDrug\tRxNorm Extension\t"
        "Dose Form\t\t6809ext\t19700101\t20231231\t\n"
    )
    (scratch / "CONCEPT.csv").write_text(rows, encoding="utf-8")
    # copy the other CSVs unchanged
    for name in ("CONCEPT_ANCESTOR.csv", "CONCEPT_RELATIONSHIP.csv", "VOCABULARY.csv"):
        (scratch / name).write_text(
            (athena_mini_dir / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    db_path = scratch / "umls.duckdb"
    from .conftest import _make_conformance_db

    _make_conformance_db(db_path)  # UMLS side (app startup needs it)
    con = duckdb.connect(str(db_path))
    from medterm4ds.services.athena import ingest_athena

    ingest_athena(
        con,
        scratch,
        vocabularies=(
            "SNOMED",
            "RxNorm",
            "RxNorm Extension",
            "LOINC",
            "ICD10CM",
            "ICD10PCS",
            "ATC",
        ),
        accept_licenses=True,
        licenses_accepted_at="ts105",
    )
    con.close()

    settings = FhirApiSettings(
        db_path=db_path,
        memory_profile="low",
        search_index_dir=str(scratch / "no_index"),
        prepare_cache=False,
    )
    app = create_fhir_app(settings)
    with TestClient(app) as client:
        yield client


def _translate(client, system: str, code: str):
    return client.get(
        "/fhir/ConceptMap/$translate",
        params={"url": ACM, "system": system, "code": code, "targetSystem": TGT},
    )


def _equivalences(payload: dict) -> list[str]:
    out = []
    for p in payload.get("parameter", []):
        if p.get("name") == "match":
            for part in p.get("part", []):
                if part.get("name") == "equivalence":
                    v = part.get("valueCode")
                    out.append(v)
    return out


class TestIdentityVocabEquivalence:
    def test_i1_icd10cm_null_standard_equivalent(self, bridge_client):
        """I1: the REAL-download shape (S=NULL) → 'equivalent' via AJ1."""
        r = _translate(bridge_client, ICD10CM_URI, "E119")
        assert r.status_code == 200, r.text[:200]
        eqs = _equivalences(r.json())
        assert eqs == ["equivalent"], f"E119 must be identity-equivalent, got {eqs}"

    def test_i2_atc_null_standard_equivalent(self, bridge_client):
        """I2: ATC identity row, same NULL-S shape."""
        r = _translate(bridge_client, ATC_URI, "A10BA02")
        assert r.status_code == 200
        eqs = _equivalences(r.json())
        assert eqs == ["equivalent"], f"A10BA02 must be identity-equivalent, got {eqs}"

    def test_i3_rxnorm_extension_excluded(self, bridge_client):
        """I3: RxNorm Extension shares the RxNorm FHIR URI but is NOT
        in the bridge's vocab filter — its codes must not resolve
        (empty match), NOT ride the identity rule."""
        r = _translate(bridge_client, RXNORM_URI, "6809ext")
        assert r.status_code == 200
        eqs = _equivalences(r.json())
        assert eqs == [], f"RxNorm Extension row must not match, got {eqs}"

    def test_i4_deprecated_snomed_unmatched(self, bridge_client):
        """I4: deprecated S row → 'unmatched' precedence through the seam."""
        r = _translate(bridge_client, SNOMED_URI, "99999001")
        assert r.status_code == 200
        eqs = _equivalences(r.json())
        assert eqs == ["unmatched"], f"deprecated row must be unmatched, got {eqs}"

    def test_i5_icd10pcs_with_s_equivalent(self, bridge_client):
        """I5: identity vocab WITH S flag — also 'equivalent' (both
        branches of the AJ1 rule agree)."""
        r = _translate(bridge_client, ICD10PCS_URI, "0DT00ZZ")
        assert r.status_code == 200
        eqs = _equivalences(r.json())
        assert eqs == ["equivalent"], f"0DT00ZZ must be equivalent, got {eqs}"

    def test_i6_unknown_icd10cm_empty(self, bridge_client):
        """I6: unknown code in an identity vocab → empty match list."""
        r = _translate(bridge_client, ICD10CM_URI, "ZZZ42")
        assert r.status_code == 200
        eqs = _equivalences(r.json())
        assert eqs == [], f"unknown code must yield no match, got {eqs}"


class TestBridgeControls:
    def test_c1_snomed_standard_equivalent(self, bridge_client):
        """Control: the pre-AJ1 happy path still holds."""
        r = _translate(bridge_client, SNOMED_URI, "44054006")
        assert r.status_code == 200
        assert _equivalences(r.json()) == ["equivalent"]

    def test_c2_wrong_target_400(self, bridge_client):
        """Control: non-athena target still 400."""
        r = bridge_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "url": ACM,
                "system": SNOMED_URI,
                "code": "44054006",
                "targetSystem": "http://example.org/other",
            },
        )
        assert r.status_code == 400
