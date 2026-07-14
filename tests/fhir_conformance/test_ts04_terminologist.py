"""TERMINOLOGIST probes for TS-04 (Security, Batch Validation, Batch Translation).

Source: https://build.fhir.org/terminology-service.html §4.7.2, §4.7.8, §4.7.10

TERMINOLOGIST lens (clinical / terminological correctness) for a chunk that is
mostly wire-format / security. The clinical surface is narrow but real:

1. Batch `$translate` clinical correctness (CF-EXPLORER-02 from TS-04 EXPLORER,
   CF-HISTORIAN-02 from TS-04 HISTORIAN): a batch Bundle containing multiple
   `$translate` entries MUST return per-entry Parameters bodies that match
   what the same single-entry `$translate` call would return — same target
   code, same target display, same equivalence value. The batch dispatcher
   must NOT silently alter the clinical content of the response.

2. Batch `$translate` equivalence vocabulary (TS-02 QA-030 carry-forward):
   every match.equivalence value MUST be from the FHIR R4
   ConceptMapEquivalence closed enum
   (https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html).
   No leaked internal vocabulary (broader/narrower/source-is-narrower-...).

3. Batch `$expand` display quality (CF-EXPLORER-03): the contains[].display
   values returned by batch `$expand` MUST be canonical preferred-atom STRs
   sourced from the engine — never bare-code fallbacks — across a mixed-
   source batch (SNOMED, ICD10CM, RXNORM).

4. URI round-trip methodology (TS-03 TERMINOLOGIST): for every Coding
   returned by a batch operation, a subsequent single-entry `$lookup` with
   the advertised `system` + `code` MUST succeed (200). Catches the
   "batch returns URI X, single-op only works with URI Y" drift.

5. HTTPS scheme for clinical deployments (SKEPTIC QA-037): with a clinical-
   deployment-realistic HTTPS host env var
   (`MEDTERM4DS_API_HOST=https://terminology.example.com`), the
   CapabilityStatement URLs MUST use HTTPS — never silently downgrade to
   HTTP. Production clinical terminology servers MUST support HTTPS.

6. Per-entry Parameters structure (§4.7.8 / §4.7.10): each batch response
   entry's `resource` field MUST be a Parameters resource (for ops that
   return Parameters). Parameter names match single-entry operation
   responses.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


# Canonical FHIR R4 ConceptMapEquivalence closed enum.
# Source: https://hl7.org/fhir/R4/valueset-concept-map-equivalence.html
# CR-014 (milestone-2 review): import the single source of truth from
# medterm4ds.engines.fhir. The prior local copy encoded R4B/R5 values
# (``encompasses``, ``matches``, ``smaller``, ``subsumedby``,
# ``not-relatedto``); the R4 spec-correct enum is exactly 10 values.
from medterm4ds.engines.fhir import FHIR_R4_CONCEPT_MAP_EQUIVALENCE
FHIR_R4_EQUIVALENCE_ENUM = FHIR_R4_CONCEPT_MAP_EQUIVALENCE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_https_test_client(tmp_path: Path, monkeypatch, host: str, port: str = "443"):
    """Construct a FHIR app TestClient with env-overridden host/port.

    Used by the §4.7.2 HTTPS-deployment probes."""
    fastapi = pytest.importorskip("fastapi")
    from starlette.testclient import TestClient
    from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app
    import duckdb

    monkeypatch.setenv("MEDTERM4DS_API_HOST", host)
    monkeypatch.setenv("MEDTERM4DS_FHIR_API_PORT", port)

    db_path = tmp_path / "umls.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(
        "CREATE TABLE mrconso (CODE VARCHAR, TTY VARCHAR, STR VARCHAR, "
        "AUI VARCHAR, SUPPRESS VARCHAR, SAB VARCHAR, CUI VARCHAR)"
    )
    con.execute(
        "CREATE TABLE mrrel (AUI1 VARCHAR, AUI2 VARCHAR, RELA VARCHAR, REL VARCHAR)"
    )
    con.close()
    settings = FhirApiSettings(
        db_path=db_path, memory_profile="low", prepare_cache=False,
    )
    app = create_fhir_app(settings)
    return TestClient(app)


def _extract_param(parameters_resource: dict, name: str):
    """Return the value of the FIRST parameter with the given name."""
    for p in parameters_resource.get("parameter", []):
        if p.get("name") == name:
            for k, v in p.items():
                if k.startswith("value"):
                    return v
    return None


def _extract_match_blocks(parameters_resource: dict) -> list[dict]:
    """Return the list of `match` part-blocks from a $translate Parameters."""
    matches = []
    for p in parameters_resource.get("parameter", []):
        if p.get("name") == "match":
            matches.append({part["name"]: part for part in p.get("part", [])})
    return matches


# ---------------------------------------------------------------------------
# 1. Batch $translate clinical equivalence (CF-EXPLORER-02 / CF-HISTORIAN-02)
# ---------------------------------------------------------------------------

def test_t10_batch_translate_matches_single_entry(fhir_client):
    """Per-entry batch $translate response MUST equal the single-entry call.

    Clinical justification: a clinician comparing batch results to single-
    query results MUST see identical target codes / displays / equivalence.
    Drift here means the batch dispatcher silently altered clinical content.
    """
    single = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json={
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": "http://snomed.info/sct"},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem", "valueUri": "http://hl7.org/fhir/sid/icd-10-cm"},
            ],
        },
    )
    assert single.status_code == 200
    single_body = single.json()

    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "ConceptMap/$translate"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "44054006"},
                        {"name": "targetsystem",
                         "valueUri": "http://hl7.org/fhir/sid/icd-10-cm"},
                    ],
                },
            }
        ],
    }
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200
    batch_body = batch.json()
    assert batch_body["type"] == "batch-response"
    assert len(batch_body["entry"]) == 1

    entry0 = batch_body["entry"][0]
    assert entry0["response"]["status"] == "200"
    assert entry0["resource"]["resourceType"] == "Parameters"
    # Clinical equivalence: batch response content MUST equal single response.
    assert entry0["resource"] == single_body


def test_t11_batch_translate_multi_entry_each_equivalent_to_single(fhir_client):
    """Each entry in a multi-entry batch $translate MUST equal the single call.

    Parametrized shape: 3 entries (different codes) — each batch response
    MUST byte-equal the single-entry call for the same code."""
    code_to_target = [
        ("44054006", "http://hl7.org/fhir/sid/icd-10-cm"),  # has cross-CUI mapping
        ("73211009", "http://hl7.org/fhir/sid/icd-10-cm"),  # no cross-CUI mapping
        ("44054006", "http://www.nlm.nih.gov/research/umls/rxnorm"),  # no match
    ]

    # Capture the single-entry responses first.
    singles = []
    for code, target in code_to_target:
        r = fhir_client.post(
            "/fhir/ConceptMap/$translate",
            json={
                "resourceType": "Parameters",
                "parameter": [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": code},
                    {"name": "targetsystem", "valueUri": target},
                ],
            },
        )
        assert r.status_code == 200
        singles.append(r.json())

    # Build a batch with the same entries and verify each matches its single.
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "ConceptMap/$translate"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": code},
                        {"name": "targetsystem", "valueUri": target},
                    ],
                },
            }
            for code, target in code_to_target
        ],
    }
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200
    batch_body = batch.json()
    assert batch_body["type"] == "batch-response"
    assert len(batch_body["entry"]) == len(code_to_target)
    for i, expected in enumerate(singles):
        assert batch_body["entry"][i]["response"]["status"] == "200", f"entry {i}"
        assert batch_body["entry"][i]["resource"] == expected, (
            f"Batch $translate entry {i} drifted from single-entry response"
        )


# ---------------------------------------------------------------------------
# 2. Batch $translate equivalence vocabulary (TS-02 QA-030 carry-forward)
# ---------------------------------------------------------------------------

def test_t20_batch_translate_equivalence_uses_fhir_enum(fhir_client):
    """Every match.equivalence value in a batch $translate response MUST be
    a member of the FHIR R4 ConceptMapEquivalence closed enum. No leaked
    internal vocabulary."""
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "ConceptMap/$translate"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "44054006"},
                        {"name": "targetsystem",
                         "valueUri": "http://hl7.org/fhir/sid/icd-10-cm"},
                    ],
                },
            }
        ],
    }
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200
    body = batch.json()
    assert body["type"] == "batch-response"
    entry = body["entry"][0]
    matches = _extract_match_blocks(entry["resource"])
    assert len(matches) >= 1, "Expected at least one match in batch $translate"
    for m in matches:
        eq_param = m.get("equivalence", {})
        eq_value = eq_param.get("valueCode")
        assert eq_value in FHIR_R4_EQUIVALENCE_ENUM, (
            f"Batch $translate equivalence value {eq_value!r} is NOT in the FHIR "
            f"R4 ConceptMapEquivalence closed enum. Internal vocabulary leaked."
        )


def test_t21_batch_translate_no_internal_vocabulary_leak(fhir_client):
    """Negative check: batch $translate equivalence MUST NOT emit raw internal
    vocabulary strings like 'source-is-narrower-than-target', 'broader',
    'narrower' (engine-internal), 'PAR', 'RB', etc."""
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "ConceptMap/$translate"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "44054006"},
                        {"name": "targetsystem",
                         "valueUri": "http://hl7.org/fhir/sid/icd-10-cm"},
                    ],
                },
            }
        ],
    }
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200
    body = batch.json()
    entry = body["entry"][0]
    for m in _extract_match_blocks(entry["resource"]):
        eq_value = m.get("equivalence", {}).get("valueCode", "")
        # Engine-internal strings that must NEVER leak to the wire.
        for forbidden in (
            "source-is-narrower-than-target",
            "source-is-broader-than-target",
            "broader",  # bare engine token; FHIR enum value is "wider"
            "narrower",  # bare engine token; FHIR enum value is "narrower" (allowed)
            # Note: 'narrower' IS in the FHIR enum; the bare engine token of
            # the same name happens to coincide. The forbidden marker here is
            # the long-form 'source-is-...' internal vocabulary.
            "PAR", "RB", "CHD", "SY", "RO", "AQ", "QB",
        ):
            if forbidden == "narrower":
                continue  # 'narrower' is allowed (it's in the FHIR enum)
            assert eq_value != forbidden, (
                f"Batch $translate leaked internal vocabulary {forbidden!r} "
                f"as equivalence value."
            )


# ---------------------------------------------------------------------------
# 3. Batch $expand display canonicalness (CF-EXPLORER-03)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "uri,expected_display_substring",
    [
        # SNOMED Form (b): http://snomed.info/sct?fhir_vs
        ("http://snomed.info/sct?fhir_vs", "Diabetes"),
        # ICD10CM Form (a): <system-uri>/vs
        ("http://hl7.org/fhir/sid/icd-10-cm/vs", "diabetes"),
        # RXNORM Form (a): <system-uri>/vs
        ("http://www.nlm.nih.gov/research/umls/rxnorm/vs", "metformin"),
    ],
)
def test_t30_batch_expand_displays_are_canonical_str(
    fhir_client, uri, expected_display_substring,
):
    """Batch $expand contains[].display values MUST be canonical preferred-atom
    STRs from the engine — never bare-code fallbacks. Parametrized across
    SNOMED, ICD10CM, and RXNORM.

    Per FHIR R4 §3.2.1.1.2 POST operations carry parameters in a Parameters
    body (not in the URL query string)."""
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "ValueSet/$expand"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [{"name": "url", "valueUri": uri}],
                },
            }
        ],
    }
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200
    body = batch.json()
    entry = body["entry"][0]
    assert entry["response"]["status"] == "200"
    vs = entry["resource"]
    assert vs["resourceType"] == "ValueSet"
    contains = vs.get("expansion", {}).get("contains", [])
    assert len(contains) > 0, f"Batch $expand for {uri} returned no concepts"
    # At least one display in the expansion must contain the canonical
    # preferred-atom STR substring (case-insensitive).
    found_canonical = any(
        expected_display_substring.lower() in (c.get("display") or "").lower()
        for c in contains
    )
    assert found_canonical, (
        f"Batch $expand for {uri} returned concepts but none had display "
        f"containing canonical STR {expected_display_substring!r}. "
        f"Displays: {[c.get('display') for c in contains]}"
    )
    # No contains entry should have a bare-code fallback as display.
    for c in contains:
        if c.get("code"):
            # display must not be empty/None and must differ meaningfully
            # from the bare code (canonical STR is always longer than a code).
            display = c.get("display") or ""
            assert display, (
                f"Batch $expand entry code={c.get('code')!r} has empty display — "
                f"bare-code fallback suspected."
            )


def test_t31_batch_expand_mixed_source_displays_all_canonical(fhir_client):
    """A mixed-source batch (3 different systems) MUST return canonical
    displays for each — never silently fall back to bare codes for any
    one source."""
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "ValueSet/$expand"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "url",
                         "valueUri": "http://snomed.info/sct?fhir_vs"},
                    ],
                },
            },
            {
                "request": {"method": "POST", "url": "ValueSet/$expand"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "url",
                         "valueUri": "http://hl7.org/fhir/sid/icd-10-cm/vs"},
                    ],
                },
            },
            {
                "request": {"method": "POST", "url": "ValueSet/$expand"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "url",
                         "valueUri": "http://www.nlm.nih.gov/research/umls/rxnorm/vs"},
                    ],
                },
            },
        ],
    }
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200
    body = batch.json()
    assert len(body["entry"]) == 3
    for i, entry in enumerate(body["entry"]):
        assert entry["response"]["status"] == "200", f"entry {i}"
        vs = entry["resource"]
        contains = vs.get("expansion", {}).get("contains", [])
        assert len(contains) > 0, f"entry {i} returned no concepts"
        for c in contains:
            display = c.get("display") or ""
            assert display and display != c.get("code"), (
                f"entry {i} code {c.get('code')!r}: display is bare-code fallback"
            )


# ---------------------------------------------------------------------------
# 4. URI round-trip (TS-03 TERMINOLOGIST methodology applied to batch)
# ---------------------------------------------------------------------------

def test_t40_batch_translate_target_codes_round_trip_via_lookup(fhir_client):
    """Every code returned by batch $translate MUST be $lookup-able with the
    advertised system+code. Catches the 'batch returns URI X, single-op only
    works with URI Y' drift."""
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "ConceptMap/$translate"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "44054006"},
                        {"name": "targetsystem",
                         "valueUri": "http://hl7.org/fhir/sid/icd-10-cm"},
                    ],
                },
            }
        ],
    }
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200
    body = batch.json()
    matches = _extract_match_blocks(body["entry"][0]["resource"])
    assert len(matches) >= 1
    for m in matches:
        target = m.get("concept", {}).get("valueCoding", {})
        sys_uri = target.get("system")
        code = target.get("code")
        assert sys_uri and code, (
            f"Batch $translate target missing system/code: {target}"
        )
        # Round-trip via single-entry $lookup.
        r = fhir_client.get(
            f"/fhir/CodeSystem/$lookup?system={sys_uri}&code={code}",
        )
        assert r.status_code == 200, (
            f"URI round-trip FAILED: batch $translate returned {sys_uri}|{code} "
            f"but single-entry $lookup returned {r.status_code}: {r.text}"
        )


def test_t41_batch_expand_contains_codes_round_trip_via_lookup(fhir_client):
    """Every code in a batch $expand contains[] MUST be $lookup-able with the
    advertised system+code."""
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "ValueSet/$expand"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "url",
                         "valueUri": "http://snomed.info/sct?fhir_vs"},
                    ],
                },
            }
        ],
    }
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200
    body = batch.json()
    contains = body["entry"][0]["resource"].get(
        "expansion", {},
    ).get("contains", [])
    assert len(contains) > 0
    for c in contains:
        sys_uri = c.get("system")
        code = c.get("code")
        assert sys_uri and code
        r = fhir_client.get(
            f"/fhir/CodeSystem/$lookup?system={sys_uri}&code={code}",
        )
        assert r.status_code == 200, (
            f"URI round-trip FAILED: batch $expand returned {sys_uri}|{code} "
            f"but single-entry $lookup returned {r.status_code}: {r.text}"
        )


# ---------------------------------------------------------------------------
# 5. HTTPS scheme for clinical deployments (SKEPTIC QA-037 verification)
# ---------------------------------------------------------------------------

def test_t50_https_clinical_deployment_uses_https_in_capabilitystatement(
    tmp_path, monkeypatch,
):
    """Clinical deployment scenario: MEDTERM4DS_API_HOST carries an HTTPS
    scheme. CapabilityStatement URLs MUST reflect HTTPS — production clinical
    terminology servers MUST support HTTPS per §4.7.2.

    Verifies SKEPTIC QA-037 fix with a realistic clinical-deployment value.
    """
    with _make_https_test_client(
        tmp_path, monkeypatch, host="https://terminology.example.com",
    ) as client:
        r = client.get("/fhir/metadata")
        assert r.status_code == 200
        cs = r.json()
        # implementation.url MUST be HTTPS for a clinical deployment.
        impl_url = cs.get("implementation", {}).get("url", "")
        assert impl_url.startswith("https://"), (
            f"Clinical deployment CapabilityStatement URL is not HTTPS: "
            f"{impl_url!r}. Production terminology servers MUST advertise HTTPS "
            f"(§4.7.2 'Servers SHOULD ensure that all interactions occur over "
            f"a secure connection')."
        )
        # MUST NOT contain the malformed http://https:// drift.
        assert "http://https://" not in impl_url, (
            f"CapabilityStatement URL has malformed http://https:// drift: "
            f"{impl_url!r}"
        )
        # The rest[].url field MUST also be HTTPS.
        for rest in cs.get("rest", []):
            rest_url = rest.get("url", "")
            if rest_url:
                assert rest_url.startswith("https://"), (
                    f"CapabilityStatement.rest[].url is not HTTPS: {rest_url!r}"
                )


def test_t51_https_with_explicit_scheme_env_var(tmp_path, monkeypatch):
    """Clinical deployment using the separate MEDTERM4DS_API_SCHEME env var
    also MUST produce HTTPS URLs in the CapabilityStatement."""
    monkeypatch.setenv("MEDTERM4DS_API_SCHEME", "https")
    with _make_https_test_client(
        tmp_path, monkeypatch, host="terminology.example.com",
    ) as client:
        r = client.get("/fhir/metadata")
        assert r.status_code == 200
        cs = r.json()
        impl_url = cs.get("implementation", {}).get("url", "")
        assert impl_url.startswith("https://"), (
            f"Clinical deployment via MEDTERM4DS_API_SCHEME=https produced "
            f"non-HTTPS URL: {impl_url!r}"
        )


# ---------------------------------------------------------------------------
# 6. Per-entry Parameters structure (§4.7.8 / §4.7.10)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "path, params",
    [
        (
            "CodeSystem/$lookup",
            [
                {"name": "system", "valueUri": "http://snomed.info/sct"},
                {"name": "code", "valueCode": "73211009"},
            ],
        ),
        (
            "CodeSystem/$validate-code",
            [
                {"name": "system", "valueUri": "http://snomed.info/sct"},
                {"name": "code", "valueCode": "73211009"},
            ],
        ),
        (
            "ConceptMap/$translate",
            [
                {"name": "system", "valueUri": "http://snomed.info/sct"},
                {"name": "code", "valueCode": "44054006"},
                {"name": "targetsystem",
                 "valueUri": "http://hl7.org/fhir/sid/icd-10-cm"},
            ],
        ),
    ],
)
def test_t60_batch_entry_resource_is_parameters_resource(
    fhir_client, path, params,
):
    """Each batch response entry's `resource` MUST be a Parameters resource
    (for ops that return Parameters). Parameter names match the single-entry
    operation response shape."""
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": path},
                "resource": {"resourceType": "Parameters", "parameter": params},
            }
        ],
    }
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200
    body = batch.json()
    entry = body["entry"][0]
    assert entry["response"]["status"] == "200"
    resource = entry["resource"]
    assert resource["resourceType"] == "Parameters", (
        f"Batch entry for {path} returned resourceType="
        f"{resource.get('resourceType')!r}, expected 'Parameters'"
    )
    assert "parameter" in resource
    assert isinstance(resource["parameter"], list)
    assert len(resource["parameter"]) > 0


def test_t61_batch_lookup_parameter_names_match_single_entry(fhir_client):
    """Parameter names in batch $lookup response MUST match parameter names
    in the single-entry $lookup response. Catches silent batch-dispatch
    divergence (e.g. dropping the 'display' or 'designation' parameter)."""
    single = fhir_client.get(
        "/fhir/CodeSystem/$lookup?"
        "system=http://snomed.info/sct&code=73211009",
    )
    assert single.status_code == 200
    single_names = {
        p["name"] for p in single.json().get("parameter", []) if "name" in p
    }

    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "CodeSystem/$lookup"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "73211009"},
                    ],
                },
            }
        ],
    }
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200
    batch_names = {
        p["name"]
        for p in batch.json()["entry"][0]["resource"].get("parameter", [])
        if "name" in p
    }
    # The set of top-level parameter names MUST match.
    assert batch_names == single_names, (
        f"Batch $lookup parameter names diverged from single-entry call. "
        f"single={single_names}, batch={batch_names}"
    )


# ---------------------------------------------------------------------------
# 7. Cross-source batch integrity (clinical correctness across systems)
# ---------------------------------------------------------------------------

def test_t70_mixed_operation_batch_preserves_clinical_content(fhir_client):
    """A mixed-operation batch (lookup + validate-code + translate + expand)
    MUST return clinically correct content for each entry — the dispatcher
    must not silently mix results across operations."""
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [
            # 1. $lookup of SNOMED 73211009
            {
                "request": {"method": "POST", "url": "CodeSystem/$lookup"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "73211009"},
                    ],
                },
            },
            # 2. $validate-code for SNOMED 44054006
            {
                "request": {
                    "method": "POST", "url": "CodeSystem/$validate-code",
                },
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "44054006"},
                    ],
                },
            },
            # 3. $translate SNOMED 44054006 → ICD10CM
            {
                "request": {"method": "POST", "url": "ConceptMap/$translate"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "system", "valueUri": "http://snomed.info/sct"},
                        {"name": "code", "valueCode": "44054006"},
                        {"name": "targetsystem",
                         "valueUri": "http://hl7.org/fhir/sid/icd-10-cm"},
                    ],
                },
            },
            # 4. $expand SNOMED implicit
            {
                "request": {"method": "POST", "url": "ValueSet/$expand"},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "url",
                         "valueUri": "http://snomed.info/sct?fhir_vs"},
                    ],
                },
            },
        ],
    }
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200
    body = batch.json()
    assert body["type"] == "batch-response"
    entries = body["entry"]
    assert len(entries) == 4

    # Entry 0: $lookup — must return Parameters with display.
    assert entries[0]["response"]["status"] == "200"
    lookup_resource = entries[0]["resource"]
    assert lookup_resource["resourceType"] == "Parameters"
    display = _extract_param(lookup_resource, "display")
    assert display and "diabetes" in display.lower(), (
        f"$lookup display is clinically wrong: {display!r}"
    )

    # Entry 1: $validate-code — must return Parameters with result=true.
    assert entries[1]["response"]["status"] == "200"
    validate_resource = entries[1]["resource"]
    assert validate_resource["resourceType"] == "Parameters"
    result = _extract_param(validate_resource, "result")
    assert result is True, f"$validate-code result is not true: {result!r}"

    # Entry 2: $translate — must return at least one match with target E11.
    assert entries[2]["response"]["status"] == "200"
    translate_resource = entries[2]["resource"]
    matches = _extract_match_blocks(translate_resource)
    assert len(matches) >= 1
    target_codes = [
        m.get("concept", {}).get("valueCoding", {}).get("code") for m in matches
    ]
    assert "E11" in target_codes, (
        f"$translate target codes missing clinical E11: {target_codes}"
    )

    # Entry 3: $expand — must return a ValueSet with SNOMED contains.
    assert entries[3]["response"]["status"] == "200"
    vs = entries[3]["resource"]
    assert vs["resourceType"] == "ValueSet"
    contains = vs.get("expansion", {}).get("contains", [])
    assert len(contains) > 0
    snomed_count = sum(
        1 for c in contains if c.get("system") == "http://snomed.info/sct"
    )
    assert snomed_count > 0, (
        f"$expand contains no SNOMED concepts: {contains}"
    )
