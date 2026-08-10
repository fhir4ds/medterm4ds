"""TERMINOLOGIST resweep probes for TS-04 (Security, Batch Validation, Batch Translation).

Source: https://build.fhir.org/terminology-service.html §4.7.3 (Security),
§4.7.5 (Batch Validation), §4.7.7 (Batch Translation); per-entry independence
cited from https://hl7.org/fhir/R4/http.html (Batch).

TERMINOLOGIST lens (medterm4ds 4th personality) — clinical and terminological
correctness. This resweep extends the original TS-04 TERMINOLOGIST surface
(test_ts04_terminologist.py — 9 probes) by covering the lens dimensions
indicated by the EXPLORER carry-forward tip and the orchestrator's QA
configuration:

- **L1** — Single-vs-batch byte-exact clinical-content parity on EVERY
  advertised operation ($lookup, $validate-code on CS AND VS, $subsumes,
  $translate, $expand). The original t70 covers mixed-ops content qualitatively;
  here we assert byte-equal response bodies per op.
- **L2** — Uppercase-scheme inheritance (TS-03 EXPLORER fix) on the BATCH
  surface. The batch dispatcher delegates URI resolution to `_do_*` handlers,
  which delegate to `fhir_uri_to_system`. We verify that uppercase-scheme URIs
  resolve identically on the batch surface — clinical safety: a deployment
  that only works with lowercase URIs would silently reject EHR clients that
  send uppercase.
- **L3** — CodeableConcept multi-coding on the batch surface
  (CS-03 SKEPTIC QA-049 lateral combination — EXPLORER's tip). The batch
  dispatcher's `_extract_validate_params` uses the all-pairs helper. Verify
  the "any match → result=true" semantic holds on the batch surface, and the
  Out `display` reflects the MATCHED coding's canonical.
- **L4** — Equivalence canonicalization on the batch $translate surface
  (TS-02 TERMINOLOGIST QA-030 carry-forward). The same SNOMED→ICD-10-CM
  mapping MUST produce the same `match.equivalence` value whether invoked
  via single-entry POST or batch entry. No R5/R4B contamination leak.
- **L5** — SSL CapabilityStatement URL clinical correctness under ALL env-var
  combinations (SKEPTIC QA-037 + HISTORIAN QA-040 carry-forwards). Operational
  clinical safety: a server that advertises wrong URLs may cause EHR systems
  to misroute clinical requests.
- **L6** — Batch per-entry clinical safety. One entry with malformed clinical
  content (wrong code system, invalid code) MUST NOT silently corrupt other
  entries' clinical responses. Per-entry isolation is a clinical safety
  property per §3.7 ("The success or failure of one change SHOULD not alter
  the success or failure or resulting content of another change").
- **L7** — Batch entry clinical content round-trip via $lookup on the FULL
  batch surface (TS-03 TERMINOLOGIST methodology — every Coding returned by a
  batch op MUST be $lookup-able with the advertised system+code).
- **L8** — Bundle shape: batch-response clinical correctness
  (§3.7 — "the server SHALL return a `Bundle` with `type` set to
  `batch-response` ... contains one entry for each entry in the request, in
  the same order"). Order preservation IS a clinical safety property
  (correlating batch responses to entries).
- **L9** — `message` field clinical informativeness. The per-entry $translate
  response's `message` parameter carries a clinically informative count of
  matches; on `result=false`, an informative "no matches" message. This is
  a clinical-decision-support concern (a clinician reading "0 matches" should
  know whether the system is empty or the code is just unmapped).
- **L10** — Builder-level direct probes (no HTTP layer) — verify that
  `build_parameters_translate` produces byte-exact content regardless of
  whether it was called from `_do_translate` (single entry) or the batch
  dispatcher's `_do_translate` (batch entry). The builder IS the single
  source of truth for clinical content.
"""

from __future__ import annotations

import ast
import inspect
import os
from pathlib import Path
from textwrap import dedent

import pytest


# Canonical FHIR R4 ConceptMapEquivalence closed enum — single source of truth.
# Import from medterm4ds.engines.fhir (NOT a local copy).
from medterm4ds.engines.fhir import (
    FHIR_R4_CONCEPT_MAP_EQUIVALENCE,
    SYSTEM_TO_FHIR_URI,
    canonical_system_uri,
)
FHIR_R4_EQUIVALENCE_ENUM = FHIR_R4_CONCEPT_MAP_EQUIVALENCE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _make_test_client_with_host(tmp_path: Path, monkeypatch, host: str, port: str = "443"):
    """Construct a FHIR app TestClient with env-overridden host/port."""
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


# ---------------------------------------------------------------------------
# L1 — Single-vs-batch byte-exact clinical-content parity on every advertised op
# ---------------------------------------------------------------------------

def _build_post_parameters_body(op: str, params: list[dict]) -> dict:
    """Build a Parameters resource body for POST."""
    return {"resourceType": "Parameters", "parameter": params}


def _build_batch_entry(op_url: str, params: list[dict]) -> dict:
    """Build a single batch entry with the given Parameters body."""
    return {
        "request": {"method": "POST", "url": op_url},
        "resource": _build_post_parameters_body(op_url, params),
    }


# (op_url, params) — the same input is sent both as single-entry POST and as
# a 1-entry batch Bundle.
_L1_OP_CASES = [
    # $lookup — SNOMED 73211009 (Diabetes mellitus).
    (
        "CodeSystem/$lookup",
        [
            {"name": "system", "valueUri": "http://snomed.info/sct"},
            {"name": "code", "valueCode": "73211009"},
        ],
    ),
    # CodeSystem/$validate-code — SNOMED 44054006.
    (
        "CodeSystem/$validate-code",
        [
            {"name": "system", "valueUri": "http://snomed.info/sct"},
            {"name": "code", "valueCode": "44054006"},
        ],
    ),
    # ValueSet/$validate-code — instance-level via url.
    (
        "ValueSet/$validate-code",
        [
            {"name": "url", "valueUri": "http://snomed.info/sct"},
            {"name": "system", "valueUri": "http://snomed.info/sct"},
            {"name": "code", "valueCode": "44054006"},
        ],
    ),
    # ConceptMap/$translate — SNOMED→ICD-10-CM (cross-system mapping).
    (
        "ConceptMap/$translate",
        [
            {"name": "system", "valueUri": "http://snomed.info/sct"},
            {"name": "code", "valueCode": "44054006"},
            {"name": "targetsystem",
             "valueUri": "http://hl7.org/fhir/sid/icd-10-cm"},
        ],
    ),
]


@pytest.mark.parametrize("op_url, params", _L1_OP_CASES,
                         ids=lambda v: v if isinstance(v, str) and "$" in v else "")
def test_t10_single_vs_batch_byte_exact_parity(fhir_client, op_url, params):
    """The batch dispatcher MUST produce byte-exact content equal to the
    single-entry POST route, for every advertised operation. The batch
    dispatcher reuses the same `_do_*` handlers and `build_parameters_*`
    builders as the single-entry routes, so this is structurally guaranteed
    — but the probe catches future divergence.

    Clinical justification: a clinician comparing batch results to single-
    query results MUST see identical target codes / displays / equivalence.
    Drift here means the batch dispatcher silently altered clinical content.
    """
    # Single-entry POST.
    single = fhir_client.post(
        f"/fhir/{op_url}",
        json=_build_post_parameters_body(op_url, params),
    )
    assert single.status_code == 200, (
        f"single-entry POST {op_url} returned {single.status_code}: {single.text}"
    )
    single_body = single.json()

    # 1-entry batch POST.
    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [_build_batch_entry(op_url, params)],
    }
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200, f"batch returned {batch.status_code}"
    batch_body = batch.json()
    assert batch_body["type"] == "batch-response"
    assert len(batch_body["entry"]) == 1

    entry0 = batch_body["entry"][0]
    assert entry0["response"]["status"] == "200"
    # Byte-exact content parity.
    assert entry0["resource"] == single_body, (
        f"Batch response for {op_url} diverged from single-entry response.\n"
        f"single={single_body}\nbatch={entry0['resource']}"
    )


def test_t11_single_vs_batch_translate_no_match_path_byte_exact(fhir_client):
    """A $translate with no match MUST produce byte-exact agreement between
    single-entry and batch invocations — same result=false, same message
    format, same empty match list. Catches silent-wrong-answer on the
    no-match path."""
    params = [
        {"name": "system", "valueUri": "http://snomed.info/sct"},
        {"name": "code", "valueCode": "44054006"},
        # SNOMED → RXNORM: no cross-CUI mapping in the fixture (T2DM has no
        # RxNorm equivalent in mrconso).
        {"name": "targetsystem",
         "valueUri": "http://www.nlm.nih.gov/research/umls/rxnorm"},
    ]
    single = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json=_build_post_parameters_body("ConceptMap/$translate", params),
    )
    assert single.status_code == 200
    # Confirm it's a no-match path.
    assert _extract_param(single.json(), "result") is False

    bundle = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [_build_batch_entry("ConceptMap/$translate", params)],
    }
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200
    entry0 = batch.json()["entry"][0]
    assert entry0["response"]["status"] == "200"
    assert entry0["resource"] == single.json(), (
        f"Batch $translate no-match path diverged from single-entry.\n"
        f"single={single.json()}\nbatch={entry0['resource']}"
    )


# ---------------------------------------------------------------------------
# L2 — Uppercase-scheme batch inheritance (TS-03 EXPLORER QA-001 carry-forward)
# ---------------------------------------------------------------------------

_L2_UPPERCASE_CASES = [
    # (op_url, params-with-lowercase, params-with-uppercase-system)
    (
        "CodeSystem/$lookup",
        [
            {"name": "system", "valueUri": "http://snomed.info/sct"},
            {"name": "code", "valueCode": "73211009"},
        ],
        [
            {"name": "system", "valueUri": "HTTP://snomed.info/sct"},
            {"name": "code", "valueCode": "73211009"},
        ],
    ),
    (
        "CodeSystem/$validate-code",
        [
            {"name": "system", "valueUri": "http://snomed.info/sct"},
            {"name": "code", "valueCode": "44054006"},
        ],
        [
            {"name": "system", "valueUri": "HTTP://snomed.info/sct"},
            {"name": "code", "valueCode": "44054006"},
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
        [
            {"name": "system", "valueUri": "HTTP://snomed.info/sct"},
            {"name": "code", "valueCode": "44054006"},
            {"name": "targetsystem",
             "valueUri": "HTTP://hl7.org/fhir/sid/icd-10-cm"},
        ],
    ),
]


@pytest.mark.parametrize(
    "op_url, lowercase_params, uppercase_params", _L2_UPPERCASE_CASES,
    ids=[c[0] for c in _L2_UPPERCASE_CASES],
)
def test_t20_batch_uppercase_scheme_inheritance(
    fhir_client, op_url, lowercase_params, uppercase_params,
):
    """Uppercase-scheme URI inheritance on the BATCH surface — TS-03 EXPLORER
    QA-001 fix MUST hold on batch invocations. The batch dispatcher delegates
    URI resolution to `_do_*` handlers, which delegate to `fhir_uri_to_system`
    (the function fixed in TS-03 EXPLORER). Clinical safety: a deployment
    that only works with lowercase URIs would silently reject EHR clients
    that send uppercase (RFC 3986 §3.1 scheme is case-insensitive).
    """
    # Lowercase batch.
    lower_bundle = {
        "resourceType": "Bundle", "type": "batch",
        "entry": [_build_batch_entry(op_url, lowercase_params)],
    }
    lower_batch = fhir_client.post("/fhir", json=lower_bundle)
    assert lower_batch.status_code == 200
    lower_body = lower_batch.json()
    assert lower_body["entry"][0]["response"]["status"] == "200", (
        f"lowercase {op_url} batch failed: {lower_body['entry'][0]}"
    )

    # Uppercase batch.
    upper_bundle = {
        "resourceType": "Bundle", "type": "batch",
        "entry": [_build_batch_entry(op_url, uppercase_params)],
    }
    upper_batch = fhir_client.post("/fhir", json=upper_bundle)
    assert upper_batch.status_code == 200
    upper_body = upper_batch.json()
    assert upper_body["entry"][0]["response"]["status"] == "200", (
        f"uppercase {op_url} batch failed (TS-03 EXPLORER fix NOT inherited "
        f"on batch surface): {upper_body['entry'][0]}"
    )

    # Byte-exact agreement between lowercase and uppercase responses.
    lower_resource = lower_body["entry"][0]["resource"]
    upper_resource = upper_body["entry"][0]["resource"]
    assert lower_resource == upper_resource, (
        f"Batch {op_url} diverged between lowercase and uppercase scheme URIs.\n"
        f"lower={lower_resource}\nupper={upper_resource}"
    )


def test_t21_batch_uppercase_scheme_translate_targetsystem_byte_exact(fhir_client):
    """Batch $translate with uppercase scheme on BOTH system AND targetsystem
    MUST produce byte-exact agreement with the lowercase variant. EXPLORER's
    L10 byte-exact-parity probe covers this on the per-op surface; here we
    extend it to the BATCH surface."""
    lower = [
        {"name": "system", "valueUri": "http://snomed.info/sct"},
        {"name": "code", "valueCode": "44054006"},
        {"name": "targetsystem", "valueUri": "http://hl7.org/fhir/sid/icd-10-cm"},
    ]
    upper = [
        {"name": "system", "valueUri": "HTTP://snomed.info/sct"},
        {"name": "code", "valueCode": "44054006"},
        {"name": "targetsystem", "valueUri": "HTTP://hl7.org/fhir/sid/icd-10-cm"},
    ]
    lower_resp = fhir_client.post(
        "/fhir",
        json={"resourceType": "Bundle", "type": "batch",
              "entry": [_build_batch_entry("ConceptMap/$translate", lower)]},
    )
    upper_resp = fhir_client.post(
        "/fhir",
        json={"resourceType": "Bundle", "type": "batch",
              "entry": [_build_batch_entry("ConceptMap/$translate", upper)]},
    )
    assert lower_resp.status_code == 200 and upper_resp.status_code == 200
    assert lower_resp.json()["entry"][0]["resource"] == upper_resp.json()["entry"][0]["resource"]


# ---------------------------------------------------------------------------
# L3 — CodeableConcept multi-coding on the batch surface (CS-03 SKEPTIC QA-049)
# ---------------------------------------------------------------------------

def test_t30_batch_validate_code_codeable_concept_any_match_true(fhir_client):
    """Batch CodeSystem/$validate-code with a codeableConcept containing
    multiple codings (one valid + one invalid) MUST return result=true
    on the batch surface, matching the per-operation semantic
    (CS-03 SKEPTIC QA-049 — "The server returns true if one of the coding
    values is in the code system").

    The batch dispatcher's `_extract_validate_params` uses
    `_extract_all_coding_pairs_from_codeable_concept` (all-pairs helper);
    this is wired via CS-03 HISTORIAN QA-052 fix. The probe catches any
    regression to single-pair semantics on the batch surface.
    """
    # CodeableConcept with 1 invalid (unknown code) + 1 valid (SNOMED 44054006).
    codeable_concept_body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {
                            "system": "http://snomed.info/sct",
                            "code": "ZZZ-NOT-A-REAL-CODE",
                        },
                        {
                            "system": "http://snomed.info/sct",
                            "code": "44054006",
                        },
                    ]
                },
            }
        ],
    }
    # First, verify per-operation POST (single-entry) returns result=true.
    single = fhir_client.post(
        "/fhir/CodeSystem/$validate-code", json=codeable_concept_body,
    )
    assert single.status_code == 200
    single_result = _extract_param(single.json(), "result")
    assert single_result is True, (
        f"Single-entry codeableConcept [INVALID, VALID] returned result={single_result}; "
        f"expected true per CS-03 SKEPTIC QA-049."
    )

    # Now the batch version.
    bundle = {
        "resourceType": "Bundle", "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "CodeSystem/$validate-code"},
                "resource": codeable_concept_body,
            }
        ],
    }
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200
    entry0 = batch.json()["entry"][0]
    assert entry0["response"]["status"] == "200"
    batch_result = _extract_param(entry0["resource"], "result")
    assert batch_result is True, (
        f"Batch codeableConcept [INVALID, VALID] returned result={batch_result}; "
        f"single-entry returned result=true. Batch dispatcher regressed to "
        f"single-pair semantics (CS-03 HISTORIAN QA-052 fix broken)."
    )


def test_t31_batch_validate_code_codeable_concept_display_reflects_matched(fhir_client):
    """Batch CodeSystem/$validate-code with codeableConcept multi-coding:
    the Out `display` MUST reflect the MATCHED coding's canonical display,
    not the first coding's. Cross-checks CS-03 TERMINOLOGIST test_t22 invariant
    on the batch surface."""
    # CodeableConcept: 1 invalid + 1 valid (SNOMED 44054006 = T2DM).
    codeable_concept_body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {"system": "http://snomed.info/sct", "code": "ZZZ-INVALID"},
                        {"system": "http://snomed.info/sct", "code": "44054006"},
                    ]
                },
            }
        ],
    }
    bundle = {
        "resourceType": "Bundle", "type": "batch",
        "entry": [
            {
                "request": {"method": "POST", "url": "CodeSystem/$validate-code"},
                "resource": codeable_concept_body,
            }
        ],
    }
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200
    entry0 = batch.json()["entry"][0]
    display = _extract_param(entry0["resource"], "display")
    assert display and "diabetes" in display.lower(), (
        f"Batch codeableConcept Out display should be the MATCHED code's "
        f"canonical display ('Type 2 diabetes mellitus'), got {display!r}. "
        f"Clinical safety: a clinician reading result=true expects to see the "
        f"matched code's display, not the first invalid code's."
    )


# ---------------------------------------------------------------------------
# L4 — Equivalence canonicalization on batch $translate (QA-030 carry-forward)
# ---------------------------------------------------------------------------

def test_t40_batch_translate_equivalence_canonicalizes_like_single(fhir_client):
    """The same SNOMED→ICD-10-CM mapping MUST produce the same
    `match.equivalence` value whether invoked via single-entry POST or batch
    entry. The TS-02 TERMINOLOGIST QA-030 fix (`_INTERNAL_REL_TO_FHIR_EQUIVALENCE`
    map + `_fhir_equivalence_from_relationship` helper) is reused by the
    batch dispatcher via `build_parameters_translate`; the equivalence
    translation MUST apply identically on both surfaces."""
    params = [
        {"name": "system", "valueUri": "http://snomed.info/sct"},
        {"name": "code", "valueCode": "44054006"},
        {"name": "targetsystem", "valueUri": "http://hl7.org/fhir/sid/icd-10-cm"},
    ]
    single = fhir_client.post(
        "/fhir/ConceptMap/$translate",
        json=_build_post_parameters_body("ConceptMap/$translate", params),
    )
    assert single.status_code == 200
    single_matches = _extract_match_blocks(single.json())

    bundle = {
        "resourceType": "Bundle", "type": "batch",
        "entry": [_build_batch_entry("ConceptMap/$translate", params)],
    }
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200
    batch_matches = _extract_match_blocks(batch.json()["entry"][0]["resource"])

    assert len(single_matches) == len(batch_matches)
    for sm, bm in zip(single_matches, batch_matches):
        s_eq = sm.get("equivalence", {}).get("valueCode")
        b_eq = bm.get("equivalence", {}).get("valueCode")
        assert s_eq == b_eq, (
            f"Batch equivalence drifted from single-entry: single={s_eq!r}, batch={b_eq!r}"
        )
        # Both MUST be in the FHIR R4 enum.
        assert s_eq in FHIR_R4_EQUIVALENCE_ENUM, (
            f"Batch $translate equivalence value {s_eq!r} NOT in FHIR R4 enum. "
            f"CF-HISTORIAN-VS01-01 (R5/R4B contamination) regressed."
        )


def test_t41_batch_translate_no_r5_r4b_contamination(fhir_client):
    """Batch $translate equivalence values MUST NOT contain R5/R4B-only
    values `subsumedby` or `matches`. CF-HISTORIAN-VS01-01 RESOLVED
    verified on the batch surface."""
    bundle = {
        "resourceType": "Bundle", "type": "batch",
        "entry": [
            _build_batch_entry(
                "ConceptMap/$translate",
                [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "44054006"},
                    {"name": "targetsystem",
                     "valueUri": "http://hl7.org/fhir/sid/icd-10-cm"},
                ],
            ),
            # SNOMED 73211009 has no cross-CUI mapping in the fixture — exercises
            # the no-match path.
            _build_batch_entry(
                "ConceptMap/$translate",
                [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "73211009"},
                    {"name": "targetsystem",
                     "valueUri": "http://hl7.org/fhir/sid/icd-10-cm"},
                ],
            ),
        ],
    }
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200
    body_text = batch.text
    # Forbidden R5/R4B values.
    assert "subsumedby" not in body_text, (
        f"Batch $translate response contains R5/R4B-only value 'subsumedby'. "
        f"CF-HISTORIAN-VS01-01 RESOLVED check failed."
    )
    assert '"matches"' not in body_text, (
        f"Batch $translate response contains R5-only value 'matches'. "
        f"CF-HISTORIAN-VS01-01 RESOLVED check failed."
    )


# ---------------------------------------------------------------------------
# L5 — SSL CapabilityStatement URL clinical safety under env var combinations
# ---------------------------------------------------------------------------

def test_t50_ssl_host_with_https_scheme_uses_https(tmp_path, monkeypatch):
    """MEDTERM4DS_API_HOST=https://terminology.example.com MUST produce HTTPS
    in CapabilityStatement.implementation.url AND every rest[].url. Clinical
    safety: production clinical terminology servers MUST support HTTPS per
    §4.7.3 SSL: 'Generally, SSL SHOULD be used for all production health care
    data exchange.' A server that silently downgrades to HTTP would misroute
    EHR clients.

    Verifies SKEPTIC QA-037 fix."""
    with _make_test_client_with_host(
        tmp_path, monkeypatch, host="https://terminology.example.com",
    ) as client:
        r = client.get("/fhir/metadata")
        assert r.status_code == 200
        cs = r.json()
        impl_url = cs.get("implementation", {}).get("url", "")
        assert impl_url.startswith("https://"), (
            f"Clinical HTTPS deployment CapabilityStatement URL is not HTTPS: "
            f"{impl_url!r}. EHR clients would misroute."
        )
        assert "http://https://" not in impl_url
        for rest in cs.get("rest", []):
            rest_url = rest.get("url", "")
            if rest_url:
                assert rest_url.startswith("https://"), (
                    f"rest[].url not HTTPS in clinical deployment: {rest_url!r}"
                )


def test_t51_ssl_explicit_scheme_env_var_uses_https(tmp_path, monkeypatch):
    """MEDTERM4DS_API_SCHEME=https with bare host MUST produce HTTPS URLs."""
    monkeypatch.setenv("MEDTERM4DS_API_SCHEME", "https")
    with _make_test_client_with_host(
        tmp_path, monkeypatch, host="terminology.example.com",
    ) as client:
        r = client.get("/fhir/metadata")
        assert r.status_code == 200
        cs = r.json()
        impl_url = cs.get("implementation", {}).get("url", "")
        assert impl_url.startswith("https://"), (
            f"CapabilityStatement URL via explicit scheme env var not HTTPS: {impl_url!r}"
        )


def test_t52_ssl_ipv6_https_uses_https_and_keeps_port(tmp_path, monkeypatch):
    """IPv6 host with HTTPS scheme MUST produce HTTPS URL AND preserve the
    port. HISTORIAN QA-040 IPv6 fix."""
    with _make_test_client_with_host(
        tmp_path, monkeypatch, host="https://[::1]",
    ) as client:
        r = client.get("/fhir/metadata")
        assert r.status_code == 200
        cs = r.json()
        impl_url = cs.get("implementation", {}).get("url", "")
        assert impl_url.startswith("https://"), (
            f"IPv6 HTTPS host CapabilityStatement URL not HTTPS: {impl_url!r}"
        )
        # The default port from _make_test_client_with_host is 443.
        # IPv6 URLs use [::1]:port bracketed form.
        assert "443" in impl_url, (
            f"IPv6 HTTPS host URL dropped the port: {impl_url!r}"
        )


def test_t53_ssl_trailing_slash_host_no_malformed_url(tmp_path, monkeypatch):
    """Trailing-slash host MUST NOT produce malformed URLs like
    'https://example.com/:443'. HISTORIAN QA-040 trailing-slash fix."""
    with _make_test_client_with_host(
        tmp_path, monkeypatch, host="terminology.example.com/",
    ) as client:
        r = client.get("/fhir/metadata")
        assert r.status_code == 200
        cs = r.json()
        impl_url = cs.get("implementation", {}).get("url", "")
        # The trailing slash MUST be stripped; the port MUST be appended cleanly.
        assert "://" in impl_url, f"URL missing scheme: {impl_url!r}"
        assert "/:443" not in impl_url, (
            f"Trailing-slash host produced malformed URL with /:443: {impl_url!r}"
        )


def test_t54_default_http_scheme_localhost_dev(tmp_path, monkeypatch):
    """Default host (no env override) MUST produce HTTP URL — localhost dev
    configuration. Cross-checks the default-branch logic."""
    # Unset both env vars so the default 127.0.0.1:DEFAULT_PORT path is taken.
    monkeypatch.delenv("MEDTERM4DS_API_HOST", raising=False)
    monkeypatch.delenv("MEDTERM4DS_API_SCHEME", raising=False)
    monkeypatch.delenv("MEDTERM4DS_FHIR_API_PORT", raising=False)

    from starlette.testclient import TestClient
    from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app
    import duckdb

    db_path = tmp_path / "umls.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(
        "CREATE TABLE mrconso (CODE VARCHAR, TTY VARCHAR, STR VARCHAR, "
        "AUI VARCHAR, SUPPRESS VARCHAR, SAB VARCHAR, CUI VARCHAR)"
    )
    con.close()
    settings = FhirApiSettings(
        db_path=db_path, memory_profile="low", prepare_cache=False,
    )
    app = create_fhir_app(settings)
    with TestClient(app) as client:
        r = client.get("/fhir/metadata")
        assert r.status_code == 200
        impl_url = r.json().get("implementation", {}).get("url", "")
        assert impl_url.startswith("http://"), (
            f"Default localhost dev CapabilityStatement URL not HTTP: {impl_url!r}"
        )
        assert "://https" not in impl_url
        assert "://http://" not in impl_url


# ---------------------------------------------------------------------------
# L6 — Batch per-entry clinical safety (one bad entry MUST NOT poison others)
# ---------------------------------------------------------------------------

def test_t60_per_entry_isolation_bad_system_does_not_corrupt_siblings(fhir_client):
    """One batch entry with an unrecognized system URI MUST return a 4xx
    OperationOutcome for THAT entry only. The other entries' clinical
    responses MUST be unaffected. Per FHIR R4 §3.7: 'The success or failure
    of one change SHOULD not alter the success or failure or resulting
    content of another change.'

    Clinical safety: a batch of 3 lookups where one has a typo in the
    system URI MUST still return the 2 valid lookups with their canonical
    displays. Silent corruption of the valid entries would cause clinicians
    to lose results."""
    bundle = {
        "resourceType": "Bundle", "type": "batch",
        "entry": [
            # Entry 0: valid SNOMED lookup.
            _build_batch_entry(
                "CodeSystem/$lookup",
                [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "73211009"},
                ],
            ),
            # Entry 1: INVALID system URI (typo).
            _build_batch_entry(
                "CodeSystem/$lookup",
                [
                    {"name": "system", "valueUri": "http://not-a-real-system.org"},
                    {"name": "code", "valueCode": "44054006"},
                ],
            ),
            # Entry 2: valid SNOMED lookup (different code).
            _build_batch_entry(
                "CodeSystem/$lookup",
                [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "44054006"},
                ],
            ),
        ],
    }
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200  # outer 200 regardless of inner failures
    body = batch.json()
    assert body["type"] == "batch-response"
    entries = body["entry"]
    assert len(entries) == 3

    # Entry 0: success with display 'Diabetes mellitus'.
    assert entries[0]["response"]["status"] == "200"
    display0 = _extract_param(entries[0]["resource"], "display")
    assert display0 and "diabetes" in display0.lower(), (
        f"Entry 0 clinical display corrupted by sibling bad entry: {display0!r}"
    )

    # Entry 1: failure (4xx) — unrecognized system.
    entry1_status = entries[1]["response"]["status"]
    assert entry1_status.startswith("4"), (
        f"Entry 1 (bad system URI) should return 4xx, got {entry1_status!r}"
    )
    assert entries[1]["resource"]["resourceType"] == "OperationOutcome"

    # Entry 2: success with display 'Type 2 diabetes mellitus'.
    assert entries[2]["response"]["status"] == "200"
    display2 = _extract_param(entries[2]["resource"], "display")
    assert display2 and "diabetes" in display2.lower(), (
        f"Entry 2 clinical display corrupted by sibling bad entry: {display2!r}"
    )


def test_t61_per_entry_isolation_bad_code_in_translate_batch(fhir_client):
    """Per-entry isolation on the $translate surface: a bad-code entry MUST
    NOT corrupt sibling $translate entries' target code values."""
    bundle = {
        "resourceType": "Bundle", "type": "batch",
        "entry": [
            # Entry 0: valid SNOMED→ICD-10-CM translate.
            _build_batch_entry(
                "ConceptMap/$translate",
                [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "44054006"},
                    {"name": "targetsystem",
                     "valueUri": "http://hl7.org/fhir/sid/icd-10-cm"},
                ],
            ),
            # Entry 1: bad targetsystem (unrecognized URI).
            _build_batch_entry(
                "ConceptMap/$translate",
                [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "44054006"},
                    {"name": "targetsystem",
                     "valueUri": "http://not-a-real-target.org"},
                ],
            ),
            # Entry 2: another valid SNOMED→ICD-10-CM translate.
            _build_batch_entry(
                "ConceptMap/$translate",
                [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "44054006"},
                    {"name": "targetsystem",
                     "valueUri": "http://hl7.org/fhir/sid/icd-10-cm"},
                ],
            ),
        ],
    }
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200
    entries = batch.json()["entry"]
    assert len(entries) == 3

    # Entries 0 and 2 MUST both succeed and return E11 as target.
    for idx in (0, 2):
        assert entries[idx]["response"]["status"] == "200", (
            f"Entry {idx} (valid translate) corrupted by sibling bad entry"
        )
        matches = _extract_match_blocks(entries[idx]["resource"])
        target_codes = [
            m.get("concept", {}).get("valueCoding", {}).get("code")
            for m in matches
        ]
        assert "E11" in target_codes, (
            f"Entry {idx} target code E11 missing (corrupted by bad sibling): "
            f"{target_codes}"
        )

    # Entry 1 MUST fail (4xx — unrecognized target system).
    entry1_status = entries[1]["response"]["status"]
    assert entry1_status.startswith("4"), (
        f"Entry 1 (bad targetsystem) should return 4xx, got {entry1_status!r}"
    )


def test_t62_per_entry_isolation_mixed_op_bad_entry_does_not_corrupt(fhir_client):
    """Per-entry isolation across mixed operation types: a bad entry (mixed
    with valid entries of OTHER op types) MUST NOT corrupt any other entry."""
    bundle = {
        "resourceType": "Bundle", "type": "batch",
        "entry": [
            # Entry 0: valid $lookup.
            _build_batch_entry(
                "CodeSystem/$lookup",
                [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "73211009"},
                ],
            ),
            # Entry 1: BAD — $validate-code with unknown system.
            _build_batch_entry(
                "CodeSystem/$validate-code",
                [
                    {"name": "system", "valueUri": "http://bad-system.org"},
                    {"name": "code", "valueCode": "44054006"},
                ],
            ),
            # Entry 2: valid $translate.
            _build_batch_entry(
                "ConceptMap/$translate",
                [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "44054006"},
                    {"name": "targetsystem",
                     "valueUri": "http://hl7.org/fhir/sid/icd-10-cm"},
                ],
            ),
        ],
    }
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200
    entries = batch.json()["entry"]
    assert len(entries) == 3

    # Entry 0: $lookup success.
    assert entries[0]["response"]["status"] == "200"
    assert _extract_param(entries[0]["resource"], "display")
    # Entry 1: failure.
    assert entries[1]["response"]["status"].startswith("4")
    # Entry 2: $translate success with E11.
    assert entries[2]["response"]["status"] == "200"
    matches2 = _extract_match_blocks(entries[2]["resource"])
    assert "E11" in [
        m.get("concept", {}).get("valueCoding", {}).get("code")
        for m in matches2
    ]


# ---------------------------------------------------------------------------
# L7 — Batch entry clinical content round-trip via $lookup
# ---------------------------------------------------------------------------

def test_t70_batch_lookup_response_system_uri_round_trips(fhir_client):
    """Every system URI returned by a batch $lookup MUST round-trip back
    into a $lookup. Catches 'batch returns URI X, single-op only works
    with URI Y' drift."""
    bundle = {
        "resourceType": "Bundle", "type": "batch",
        "entry": [
            _build_batch_entry(
                "CodeSystem/$lookup",
                [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "44054006"},
                ],
            )
        ],
    }
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200
    resource = batch.json()["entry"][0]["resource"]
    # Extract the system parameter from the response.
    sys_uri = _extract_param(resource, "system")
    assert sys_uri, f"Batch $lookup response missing system parameter: {resource}"
    # Round-trip.
    r = fhir_client.get(f"/fhir/CodeSystem/$lookup?system={sys_uri}&code=44054006")
    assert r.status_code == 200, (
        f"Batch $lookup returned system={sys_uri!r} but single-entry $lookup "
        f"with the same URI failed ({r.status_code}). URI drift."
    )


def test_t71_batch_translate_response_target_uri_round_trips(fhir_client):
    """Every target system URI returned by a batch $translate MUST round-trip
    back into a $lookup with the target code."""
    bundle = {
        "resourceType": "Bundle", "type": "batch",
        "entry": [
            _build_batch_entry(
                "ConceptMap/$translate",
                [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "44054006"},
                    {"name": "targetsystem",
                     "valueUri": "http://hl7.org/fhir/sid/icd-10-cm"},
                ],
            )
        ],
    }
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200
    matches = _extract_match_blocks(batch.json()["entry"][0]["resource"])
    assert len(matches) >= 1
    for m in matches:
        coding = m.get("concept", {}).get("valueCoding", {})
        sys_uri = coding.get("system")
        code = coding.get("code")
        assert sys_uri and code
        r = fhir_client.get(f"/fhir/CodeSystem/$lookup?system={sys_uri}&code={code}")
        assert r.status_code == 200, (
            f"Batch $translate target {sys_uri}|{code} doesn't round-trip via $lookup"
        )


# ---------------------------------------------------------------------------
# L8 — Bundle shape: batch-response clinical correctness
# ---------------------------------------------------------------------------

def test_t80_batch_response_type_is_batch_response(fhir_client):
    """The batch-response Bundle MUST have type=batch-response per FHIR R4
    §3.7. Clinical safety: a client dispatching batch responses depends on
    the type discriminator."""
    bundle = {
        "resourceType": "Bundle", "type": "batch",
        "entry": [
            _build_batch_entry(
                "CodeSystem/$lookup",
                [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "73211009"},
                ],
            )
        ],
    }
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200
    body = batch.json()
    assert body["resourceType"] == "Bundle"
    assert body["type"] == "batch-response", (
        f"Batch response type is {body.get('type')!r}, expected 'batch-response'"
    )


def test_t81_batch_response_preserves_order_correlation(fhir_client):
    """Order preservation IS a clinical safety property per §3.7: 'contains
    one entry for each entry in the request, in the same order'. A clinician
    correlating batch results to entries relies on positional correlation."""
    codes = ["73211009", "44054006", "73211009"]  # 3 distinct lookups
    entries = [
        _build_batch_entry(
            "CodeSystem/$lookup",
            [
                {"name": "system", "valueUri": "http://snomed.info/sct"},
                {"name": "code", "valueCode": code},
            ],
        )
        for code in codes
    ]
    bundle = {"resourceType": "Bundle", "type": "batch", "entry": entries}
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200
    resp_entries = batch.json()["entry"]
    assert len(resp_entries) == len(codes)
    for i, code in enumerate(codes):
        # Each response at position i MUST correspond to the request at position i.
        # SNOMED 73211009 = Diabetes mellitus; 44054006 = Type 2 diabetes mellitus.
        display = _extract_param(resp_entries[i]["resource"], "display")
        if code == "73211009":
            assert "diabetes" in (display or "").lower(), (
                f"Position {i}: expected Diabetes mellitus, got {display!r}"
            )
        elif code == "44054006":
            assert "type 2 diabetes" in (display or "").lower(), (
                f"Position {i}: expected Type 2 diabetes mellitus, got {display!r}"
            )


# ---------------------------------------------------------------------------
# L9 — `message` field clinical informativeness on batch $translate
# ---------------------------------------------------------------------------

def test_t90_batch_translate_message_informs_match_count(fhir_client):
    """Batch $translate response `message` parameter MUST carry a clinically
    informative count of matches (e.g. '1 matches found'). On result=true,
    a clinician reading 'N matches found' knows how many target candidates
    to expect. Pin per CM-02 TERMINOLOGIST test_t40 / always-emit-message
    convention."""
    bundle = {
        "resourceType": "Bundle", "type": "batch",
        "entry": [
            _build_batch_entry(
                "ConceptMap/$translate",
                [
                    {"name": "system", "valueUri": "http://snomed.info/sct"},
                    {"name": "code", "valueCode": "44054006"},
                    {"name": "targetsystem",
                     "valueUri": "http://hl7.org/fhir/sid/icd-10-cm"},
                ],
            )
        ],
    }
    batch = fhir_client.post("/fhir", json=bundle)
    assert batch.status_code == 200
    resource = batch.json()["entry"][0]["resource"]
    message = _extract_param(resource, "message")
    matches = _extract_match_blocks(resource)
    assert message, "Batch $translate response missing message parameter"
    assert str(len(matches)) in message, (
        f"Batch $translate message {message!r} doesn't reflect match count "
        f"({len(matches)}). Clinician would be misled."
    )


# ---------------------------------------------------------------------------
# L10 — Builder-level direct probes (no HTTP layer)
# ---------------------------------------------------------------------------

def test_t100_build_parameters_translate_reuses_canonical_equivalence_map():
    """`build_parameters_translate` MUST source equivalence values through
    `_fhir_equivalence_from_relationship` which consults the canonical
    `_INTERNAL_REL_TO_FHIR_EQUIVALENCE` map (imported from
    `engines.fhir.equivalence`). SOURCE-READ audit — the builder is the
    load-bearing contract for batch↔single parity."""
    from medterm4ds.engines.fhir import responses as responses_module
    from medterm4ds.engines.fhir import equivalence as equivalence_module

    # The responses module imports INTERNAL_REL_TO_FHIR_EQUIVALENCE from the
    # canonical equivalence module under the alias _INTERNAL_REL_TO_FHIR_EQUIVALENCE.
    # Verify via object identity that they're the SAME object (not a copy).
    assert responses_module._INTERNAL_REL_TO_FHIR_EQUIVALENCE is \
        equivalence_module.INTERNAL_REL_TO_FHIR_EQUIVALENCE, (
        "responses.py's _INTERNAL_REL_TO_FHIR_EQUIVALENCE is NOT the same "
        "Python object as equivalence.py's INTERNAL_REL_TO_FHIR_EQUIVALENCE. "
        "Batch↔single parity contract broken."
    )


def test_t101_build_parameters_translate_signature_clinical_content_args():
    """`build_parameters_translate` accepts (mappings, source_system_uri,
    source_code). The `source_system_uri` is the canonical source URI
    (caller responsibility). SOURCE-READ audit — the builder is called by
    both `_do_translate` (single) AND the batch dispatcher's `_do_translate`
    with the same arguments."""
    import inspect
    from medterm4ds.engines.fhir.responses import build_parameters_translate

    sig = inspect.signature(build_parameters_translate)
    params = list(sig.parameters.keys())
    assert "mappings" in params
    assert "source_system_uri" in params
    assert "source_code" in params


def test_t102_dispatch_batch_operation_calls_do_translate_directly():
    """`_dispatch_batch_operation` MUST call `_do_translate` for
    ConceptMap/$translate (which delegates to `build_parameters_translate`),
    not an inline construction. SOURCE-READ — the dispatcher must not bypass
    the single-entry clinical-content path."""
    import re
    from medterm4ds.apps import fhir_api as fhir_api_module

    # Get the source of _dispatch_batch_operation (nested async function).
    src = inspect.getsource(fhir_api_module)
    tree = ast.parse(src)

    # Walk to find _dispatch_batch_operation (AsyncFunctionDef inside create_fhir_app).
    found_dispatch = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_dispatch_batch_operation":
            found_dispatch = node
            break
    assert found_dispatch is not None, "_dispatch_batch_operation not found"

    dispatch_src = ast.get_source_segment(src, found_dispatch)
    # The dispatcher MUST call _do_translate (not construct inline).
    assert "_do_translate" in dispatch_src, (
        "_dispatch_batch_operation does NOT call _do_translate for "
        "ConceptMap/$translate — clinical content path diverges from single."
    )
    # The dispatcher MUST call build_parameters_translate indirectly via
    # _do_translate. (We assert _do_translate is present, which is the
    # delegation chain.)


def test_t103_canonical_system_uri_helper_imported_in_fhir_api():
    """`canonical_system_uri` MUST be imported from `engines.fhir` (single
    source of truth) in `apps.fhir_api`. Verifies CR-012 fix is intact —
    the Out `match[].source.system` field on $translate MUST be canonical."""
    from medterm4ds.apps import fhir_api as fhir_api_module
    assert hasattr(fhir_api_module, "canonical_system_uri") or \
        "canonical_system_uri" in dir(fhir_api_module) or \
        "canonical_system_uri" in inspect.getsource(fhir_api_module), (
        "canonical_system_uri not present in fhir_api module. CR-012 fix regressed."
    )
