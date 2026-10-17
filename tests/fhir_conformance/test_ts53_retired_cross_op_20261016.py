"""TS-53 retired-code cross-op matrix (2026-10-16).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261016e).
FRESH EVIDENCE — live probes executed this run against a purpose-built
retired-code fixture; no prior-run results cited.

Scope: how each operation encodes a RETIRED code (SUPPRESS='O' atom
with a known replaced_by successor) — first cross-op matrix on the
retired dimension. TS-33 covered /resolve modes privately; CS05's d70
pinned the not-found encoding for one retired concept. This suite
walks the SAME retired code through lookup / validate-code / subsumes
(both argument shapes) / translate.

RT1 (LOW-MED, NEW) — the same retired code is simultaneously
    known-enough-to-compare and unknown, by argument shape.
    subsumes(1111111, 1111111) → 200 equivalent (the U2 equivalence
    short-circuit precedes code-in-system validation — documented
    design, R4 outcome semantics) while subsumes(1111111, 2222222) →
    400 'Code 1111111 is not known in code system RXNORM'. A client
    asking "does A equal itself?" gets a definitive answer; asking
    "does A relate to anything else?" is told A doesn't exist. Both
    behaviors are individually defensible; the retired-code dimension
    of the split is undocumented.
    Fix shape: either document the split (equivalence = syntactic,
    always answers; comparison = semantic, requires active concepts)
    or treat the equivalence short-circuit as validation-exempt in the
    OperationDefinitions/TerminologyCapabilities narrative (Y1-adjacent).

RT2 (LOW-MED, NEW) — retired-with-known-replacement miss carries NO
    inactive/replacement signal on ANY surface.
    lookup → 200 OperationOutcome not-found 'Code not found in the
    specified system.' (both transports) — identical bytes to a typo'd
    never-existed code. validate-code → 200 result=false
    'Code 1111111 is not valid...' — same. R4 §4.4.18 lookup prose:
    an inactive code SHOULD surface inactive=true (property); the CS-05
    d70 enhancement contract (inactive property + replacement pointer)
    remains unimplemented, and the fixture here shows the sharper case:
    the server HAS the replaced_by edge and still emits nothing.
    Fix shape: resolve the retirement on lookup/validate miss and
    surface the successor (display "inactive; replaced by New Drug
    (2222222)" or a property part) — or register the miss-encoding as
    deliberate under the QC-330-era stub doctrine.
    Pinned rt20-rt23.

Controls (fresh): active replacement code answers normally on every
    op (lookup name, validate TRUE); translate retired → 200
    result=false clean no-match (no crosswalk rows for a suppressed
    atom); validate Out echoes code+system on miss (QC era contract);
    VS validate-code in-set TRUE (gate machinery unaffected).
"""

from __future__ import annotations

import duckdb
import pytest

SNOMED_URI = "http://snomed.info/sct"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RETIRED = "1111111"      # SUPPRESS='O', replaced_by 2222222
REPLACEMENT = "2222222"  # active
T2DM = "44054006"
DM_ISA_URL = f"{SNOMED_URI}/73211009?fhir_vs=isa"


@pytest.fixture(scope="module")
def retired_client(tmp_path_factory):
    """FHIR facade over the shared conformance DB + a seeded retired
    pair (retired RXNORM atom with an active replacement + the
    replaced_by edge)."""
    pytest.importorskip("fastapi")
    from starlette.testclient import TestClient

    from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app

    from .conftest import _make_conformance_db

    db_path = tmp_path_factory.mktemp("ts53_retired") / "umls.duckdb"
    _make_conformance_db(db_path)
    con = duckdb.connect(str(db_path))
    con.execute(
        "INSERT INTO mrconso VALUES "
        "('1111111','PT','Old Drug','A1111111','O','RXNORM','C0000001')"
    )
    con.execute(
        "INSERT INTO mrconso VALUES "
        "('2222222','PT','New Drug','A2222222','N','RXNORM','C0000002')"
    )
    con.execute(
        "INSERT INTO mrrel VALUES "
        "('A1111111','A2222222','replaced_by','RB')"
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


def _outcome(body: dict):
    for p in body.get("parameter", []):
        if p.get("name") == "outcome":
            return p.get("valueCode")
    return None


def _result(body: dict):
    for p in body.get("parameter", []):
        if p.get("name") == "result":
            return p.get("valueBoolean")
    return None


class TestRT1RetiredKnownUnknownSplit:
    """RT1 — equivalence answers, comparison 400s."""

    def test_rt10_identical_retired_equivalent(self, retired_client):
        """subsumes(retired, retired) → 200 equivalent: the U2
        equivalence short-circuit treats the pair as syntactically
        identical before validation. Documented U2 design; pinned as
        the known-side of the split."""
        r = retired_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": RXNORM_URI,
                "codeA": RETIRED, "codeB": RETIRED,
            },
        )
        assert r.status_code == 200
        assert _outcome(r.json()) == "equivalent"

    def test_rt11_retired_vs_other_400(self, retired_client):
        """subsumes(retired, replacement) → 400 code-not-known: the
        unknown-side of the split (fresh probe on the retired pair)."""
        r = retired_client.get(
            "/fhir/CodeSystem/$subsumes",
            params={
                "system": RXNORM_URI,
                "codeA": RETIRED, "codeB": REPLACEMENT,
            },
        )
        assert r.status_code == 400
        assert RETIRED in r.json()["issue"][0]["diagnostics"]


class TestRT2NoReplacementSignal:
    """RT2 — miss identical to never-existed on every surface."""

    def test_rt20_lookup_retired_not_found(self, retired_client):
        """GET lookup retired → 200 not-found, byte-shape identical to
        a typo'd code (no inactive/replacement signal despite the
        server holding the replaced_by edge). Flip when the CS-05 d70
        enhancement contract lands (inactive property / successor
        pointer)."""
        r = retired_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": RXNORM_URI, "code": RETIRED},
        )
        assert r.status_code == 200
        issue = r.json()["issue"][0]
        assert issue["code"] == "not-found"
        assert REPLACEMENT not in r.text

    def test_rt21_lookup_retired_post_same(self, retired_client):
        """POST parity for rt20."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": RXNORM_URI},
                {"name": "code", "valueCode": RETIRED},
            ],
        }
        r = retired_client.post("/fhir/CodeSystem/$lookup", json=body)
        assert r.status_code == 200
        assert r.json()["issue"][0]["code"] == "not-found"

    def test_rt22_validate_retired_false_no_pointer(self, retired_client):
        """validate retired → 200 result=false 'not valid' — no
        inactive/replacement mention. Flip when retirement resolution
        surfaces the successor."""
        r = retired_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": RXNORM_URI, "code": RETIRED},
        )
        assert r.status_code == 200
        assert _result(r.json()) is False
        assert REPLACEMENT not in r.text

    def test_rt23_typo_identical_bytes(self, retired_client):
        """Premise control: a never-existed code produces the SAME
        not-found shape — the retired signal is indistinguishable from
        a typo by response content."""
        r1 = retired_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": RXNORM_URI, "code": RETIRED},
        )
        r2 = retired_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": RXNORM_URI, "code": "9999999"},
        )
        assert (
            r1.json()["issue"][0]["diagnostics"]
            == r2.json()["issue"][0]["diagnostics"]
        )


class TestTS53Controls:
    """Controls — active replacement + siblings behave normally."""

    def test_c10_replacement_lookup_ok(self, retired_client):
        r = retired_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": RXNORM_URI, "code": REPLACEMENT},
        )
        assert r.status_code == 200
        assert r.json().get("resourceType") == "Parameters"

    def test_c11_replacement_validate_true(self, retired_client):
        r = retired_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": RXNORM_URI, "code": REPLACEMENT},
        )
        assert r.status_code == 200
        assert _result(r.json()) is True

    def test_c12_translate_retired_clean_false(self, retired_client):
        """Translate retired → 200 result=false: no crosswalk rows for
        a suppressed atom — a CLEAN no-match, not an error (contrast
        with subsumes' 400 on the same code)."""
        r = retired_client.get(
            "/fhir/ConceptMap/$translate",
            params={
                "system": RXNORM_URI, "code": RETIRED,
                "targetSystem": SNOMED_URI,
            },
        )
        assert r.status_code == 200
        assert _result(r.json()) is False

    def test_c13_vs_validate_gate_unaffected(self, retired_client):
        """V1's url-scoping machinery unaffected by the retired pair:
        in-set code still TRUE."""
        r = retired_client.get(
            "/fhir/ValueSet/$validate-code",
            params={
                "url": DM_ISA_URL,
                "system": SNOMED_URI, "code": T2DM,
            },
        )
        assert r.status_code == 200
        assert _result(r.json()) is True
