"""CS-06 CodeSystem $validate-code In-parameter matrix (2026-10-07).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261007d).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: R4 §4.8.18 declares the $validate-code In-params: code, version,
date, coding, codeableConcept, display, system, systemVersion,
inferSystem, abstract, displayLanguage. This suite maps which bind,
which are silently dropped, and which accepted values are unvalidated —
the CS-side mirror of VS-03's EA/EB (iteration 20261007c).

H1 (MEDIUM, NEW) — version-selection params silently ignored.
    version=9.9 / systemVersion=9.9 / version=2024 all return 200
    result=true byte-identical to the unparameterized request. A
    client pinning a historical code system version believes it
    received a versioned verdict; it received today's. Same family as
    N2 ($translate version semantics, TS-15) and EA (VS-03). Per R4
    §4.8.18 these params exist to select the version; server has one
    version and must either honor or reject (§4.9.2 work-or-error).
    Fix shape: accept the CURRENT version identifier (if any) and 400
    on others, or 400 on ALL version values naming single-version
    serving. Flip pins h10-h12.

H2 (LOW-MED, NEW) — abstract-use validation absent.
    abstract=true on a CONCRETE code (44054006) returns result=true.
    Per R4 §4.8.18 ``abstract`` ("if this is true, the code is being
    validated for abstract use") a concrete code validated for
    abstract use must return false (the spec's Out 'message' for this
    case names the abstract-use failure). Sibling of the CS-05
    hardcoded abstract=false Out gap (a30 family), reversed polarity.
    Fix shape: when abstract=true and the resolved code is concrete,
    result=false + message; when the code IS abstract and abstract is
    false/absent, result=false per R4. Flip pin h20.

H3 (LOW, NEW) — inferSystem ignored, transport-asymmetric.
    R4 §4.8.18 inferSystem (0..1 boolean): GET code+inferSystem=true
    (no system) → 422 (param undeclared — FastAPI unknown-Query drop,
    M1 family); POST body code+inferSystem=true → 400 'system and
    code are required' (param PARSED then dropped, then the
    required-check fires). Neither transport honors inference.
    Fix shape: implement (SCTID-range inference is feasible) or 400
    with 'inferSystem not supported'. Flip pins h30/h31.

H4 (LOW, NEW) — date accepts non-dates.
    date=garbage → 200 both transports (GET param and POST
    valueDateTime), no dateTime validation. EB family (VS-03). Flip
    pins h40/h41.

Controls (fresh): display exact/wrong semantics incl. mismatch
    message quoting the client display; coding POST binding;
    codeableConcept any-coding-match; unknown system 400; unknown
    code miss-encoding (200 result=false + message per the documented
    miss contract).
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
T2DM = "44054006"
T2DM_DISPLAY = "Type 2 diabetes mellitus"


def _param(params_json: dict, name: str):
    for p in params_json.get("parameter", []):
        if p.get("name") == name:
            return next(
                (v for k, v in p.items() if k.startswith("value")), None
            )
    return None


class TestH1VersionParamsIgnored:
    """H1 — version/systemVersion/date silently ignored."""

    @pytest.mark.parametrize(
        "param,value",
        [
            ("version", "9.9"),
            ("version", "2024"),
            ("systemVersion", "9.9"),
        ],
    )
    def test_h10_version_param_silent_true(
        self, fhir_client, param, value
    ):
        """Version-selection params 200 result=true regardless of
        value — no version semantics. Flip when versioned verdicts (or
        explicit 400 single-version rejection) land."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI, "code": T2DM, param: value,
            },
        )
        # c-fixbatch2 (H1 fix): version-selection params rejected —
        # single-version server, no silent historical-pin.
        assert r.status_code == 400
        assert param in str(r.json()["issue"][0]["diagnostics"])

    def test_h11_version_values_indistinguishable(self, fhir_client):
        """version=2024 vs version=9.9: byte-identical verdicts — the
        param cannot be observed to do anything. Flip when observable
        semantics land."""
        r1 = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": SNOMED_URI, "code": T2DM, "version": "2024"},
        )
        r2 = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": SNOMED_URI, "code": T2DM, "version": "9.9"},
        )
        # c-fixbatch2 (H1 fix): both versions now fail loudly and
        # identically (single-version server).
        assert r1.status_code == 400
        assert r2.status_code == 400

    def test_h12_post_version_body_silent(self, fhir_client):
        """POST Parameters version=9.9 body — same silent acceptance.
        Flip when honored/400."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "coding", "valueCoding": {
                    "system": SNOMED_URI, "code": T2DM,
                }},
                {"name": "version", "valueString": "9.9"},
            ],
        }
        r = fhir_client.post(
            "/fhir/CodeSystem/$validate-code", json=body
        )
        # c-fixbatch2 (H1 fix): body version rejected with parity.
        assert r.status_code == 400


class TestH2AbstractUse:
    """H2 — abstract=true on concrete code returns TRUE."""

    def test_h20_abstract_true_concrete_code(self, fhir_client):
        """R4 §4.8.18: abstract=true means the code is validated for
        ABSTRACT use; a concrete code must NOT validate for abstract
        use. Current: result=true (param ignored). Flip when abstract
        semantics land (result=false + message)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI, "code": T2DM, "abstract": "true",
            },
        )
        assert r.status_code == 200
        assert _param(r.json(), "result") is True  # flip to False


class TestH3InferSystem:
    """H3 — inferSystem ignored; transport-asymmetric failure."""

    def test_h30_get_infer_system_rejected(self, fhir_client):
        """GET: inferSystem is NOT a CodeSystem/$validate-code param
        (R4 §4.8.21.2 declares it only on ValueSet) — presence is
        rejected 400 naming the ValueSet surface (was 422 unknown-Query
        drop)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI, "code": T2DM,
                "inferSystem": "true",
            },
        )
        assert r.status_code == 400
        assert "ValueSet" in str(r.json()["issue"][0]["diagnostics"])

    def test_h31_post_infer_system_rejected(self, fhir_client):
        """POST: inferSystem presence rejected 400 on the CodeSystem
        surface (was parsed-then-dropped → misleading 'system and code
        are required')."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "code", "valueCode": T2DM},
                {"name": "inferSystem", "valueBoolean": True},
            ],
        }
        r = fhir_client.post(
            "/fhir/CodeSystem/$validate-code", json=body
        )
        assert r.status_code == 400
        assert "ValueSet" in str(r.json()["issue"][0]["diagnostics"])

    def test_h32_vs_infer_system_honored(self, fhir_client):
        """H3 positive side: inferSystem IS declared on
        ValueSet/$validate-code (R4 §4.9.18) — SCTID-shape inference
        fills a missing system there."""
        r = fhir_client.get(
            "/fhir/ValueSet/$validate-code",
            params={
                "url": f"{SNOMED_URI}/73211009?fhir_vs=isa",
                "code": T2DM, "inferSystem": "true",
            },
        )
        assert r.status_code == 200
        assert _param(r.json(), "result") is True


class TestH4DateUnvalidated:
    """H4 — date accepts non-dates (EB family)."""

    def test_h40_get_bogus_date(self, fhir_client):
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI, "code": T2DM, "date": "garbage",
            },
        )
        # c-fixbatch2 (H4 fix): dateTime shape validation.
        assert r.status_code == 400

    def test_h41_post_bogus_date(self, fhir_client):
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "coding", "valueCoding": {
                    "system": SNOMED_URI, "code": T2DM,
                }},
                {"name": "date", "valueDateTime": "garbage"},
            ],
        }
        r = fhir_client.post(
            "/fhir/CodeSystem/$validate-code", json=body
        )
        # c-fixbatch2 (H4 fix): body valueDateTime shape validation.
        assert r.status_code == 400


class TestCS06Controls:
    """Controls — the BOUND contract, re-verified fresh."""

    def test_c10_display_exact(self, fhir_client):
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI, "code": T2DM,
                "display": T2DM_DISPLAY,
            },
        )
        assert r.status_code == 200
        assert _param(r.json(), "result") is True

    def test_c11_display_wrong_false_with_message(self, fhir_client):
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": SNOMED_URI, "code": T2DM,
                "display": "Completely Wrong",
            },
        )
        assert r.status_code == 200
        assert _param(r.json(), "result") is False
        assert "incorrect" in _param(r.json(), "message")

    def test_c12_post_coding_binds(self, fhir_client):
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "coding", "valueCoding": {
                    "system": SNOMED_URI, "code": T2DM,
                    "display": T2DM_DISPLAY,
                }},
            ],
        }
        r = fhir_client.post(
            "/fhir/CodeSystem/$validate-code", json=body
        )
        assert r.status_code == 200
        assert _param(r.json(), "result") is True

    def test_c13_post_codeable_concept_any_coding(self, fhir_client):
        """R4 codeableConcept: any coding in the concept may satisfy —
        [bogus, good] validates true (V1-fix gate parity: each coding
        is resolved, the concept is true if ANY coding is)."""
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "codeableConcept", "valueCodeableConcept": {
                    "coding": [
                        {"system": SNOMED_URI, "code": "99999999"},
                        {"system": SNOMED_URI, "code": T2DM},
                    ],
                }},
            ],
        }
        r = fhir_client.post(
            "/fhir/CodeSystem/$validate-code", json=body
        )
        assert r.status_code == 200
        assert _param(r.json(), "result") is True

    def test_c14_unknown_system_400(self, fhir_client):
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={
                "system": "http://nowhere.org", "code": "X",
            },
        )
        assert r.status_code == 400

    def test_c15_unknown_code_miss_encoded(self, fhir_client):
        """Unknown code: 200 + result=false + message (the documented
        miss contract, not a 404)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$validate-code",
            params={"system": SNOMED_URI, "code": "99999999"},
        )
        assert r.status_code == 200
        assert _param(r.json(), "result") is False
        assert "not valid" in _param(r.json(), "message")
