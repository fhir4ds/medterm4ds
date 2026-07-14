"""EXPLORER iteration CS-01 — lateral-thinking probes for the CodeSystem
resource-structure surface (https://build.fhir.org/codesystem.html).

EXPLORER lens for CS-01:
  1. URI round-trip from $lookup response — call $lookup, then re-call $lookup
     with the canonical-system + canonical-code properties returned in the
     first response. Catches "lookup returns URI X, lookup only works with
     URI Y" drift. (TERMINOLOGIST methodology from TS-03.)
  2. POST body with canonical-system + canonical-code — send a Parameters
     body using the canonical URI from a prior lookup. Should resolve.
  3. Search param advertisements — all 5 advertised params (url/version/name/
     title/status) return Bundles; probe combined, special-char, very-long,
     and exact-URI-match forms.
  4. CodeSystem property types on $lookup — verify typed properties
     (`abstract` boolean) are correctly encoded.
  5. Unusual code values — very long codes (>1000 chars), codes with special
     chars, codes with embedded URIs, very short codes (1 char).
  6. Mixed-case system URIs — `HTTP://snomed.info/sct` (uppercase scheme),
     trailing slash, trailing dot.
  7. $lookup on all 8 supported systems — every system in
     SYSTEM_TO_FHIR_URI should resolve a known code via $lookup.
  8. Cross-reference canonical-system vs SYSTEM_TO_FHIR_URI — the
     canonical-system property returned by $lookup should be in
     SYSTEM_TO_FHIR_URI.values().

Production-surface note: the conformance fixture loads production
patient-friendly JSONs from /mnt/d/medterm4ds/reports/fhir4px (via the
unchanged MEDTERM4DS_FHIR4PX_BASELINE env var). This is the same
accidental-reproduction surface SKEPTIC/HISTORIAN leveraged for QA-043/QA-044.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers (mirrored from CS-01 HISTORIAN)
# ---------------------------------------------------------------------------

PF_BASELINE = Path("/mnt/d/medterm4ds/reports/fhir4px")
PF_SOURCES = ("snomedct_us", "rxnorm", "icd10cm", "icd10pcs", "lnc", "cpt", "hcpcs", "cvx")


def _pf_loaded() -> bool:
    if not PF_BASELINE.is_dir():
        return False
    return any((PF_BASELINE / f"patient_friendly_{s}.json").exists() for s in PF_SOURCES)


def _first_pf_entry_for_source(source_lower: str) -> tuple[str, dict] | None:
    path = PF_BASELINE / f"patient_friendly_{source_lower}.json"
    if not path.exists():
        return None
    try:
        with path.open() as f:
            data = json.load(f)
    except Exception:
        return None
    if isinstance(data, dict):
        for code, entry in data.items():
            if isinstance(entry, dict) and entry.get("canonical_system") and entry.get("canonical_code"):
                return code, entry
    return None


def _extract_property(parts: list[dict], prop_code: str) -> str | None:
    """Return valueString/valueUri/valueCode for a property part named `prop_code`."""
    for p in parts:
        if p.get("name") != "property":
            continue
        sub_parts = p.get("part", [])
        code_part = next((pt for pt in sub_parts if pt.get("name") == "code"), {})
        if code_part.get("valueCode") != prop_code:
            continue
        val_part = next((pt for pt in sub_parts if pt.get("name") == "value"), {})
        return (
            val_part.get("valueString")
            or val_part.get("valueUri")
            or val_part.get("valueCode")
        )
    return None


def _has_property(parts: list[dict], prop_code: str) -> bool:
    return _extract_property(parts, prop_code) is not None


# ---------------------------------------------------------------------------
# Probe class 1: URI round-trip from $lookup response
# (TERMINOLOGIST methodology from TS-03 generalized to CS-01 surface)
# ---------------------------------------------------------------------------


def test_e10_lookup_canonical_system_uri_is_resolvable(fhir_client):
    """URI round-trip probe (TERMINOLOGIST methodology from TS-03 generalized
    to CS-01): the canonical-system returned by $lookup MUST be a resolvable
    FHIR URI (recognized by `fhir_uri_to_system`).

    Catches "lookup returns URI X, lookup only works with URI Y" drift — i.e.,
    the canonical-system property advertises a URI that the same endpoint
    can't recognize. (QA-043 would have failed this had the raw "icd10" SAB
    label leaked through — the SAB string is not in `fhir_uri_to_system`'s
    map and would have produced a 400 from the second lookup's "Unrecognized
    system URI" path.)

    Per FHIR R4 §4.8.3.1 CodeSystem identification + §4.8.11 Concept Properties:
    Coding.system values MUST be canonical URIs. The CS-01 SKEPTIC QA-043
    fix ensures canonical-system IS a FHIR URI; this probe confirms that
    URI is recognized by the same endpoint's parser.

    Note: we don't assert the round-trip $lookup returns 200 + Parameters
    because the canonical-code emitted by patient-friendly JSON can be a
    *range code* (e.g., ICD-10-CM range `E08-E13`) that the seeded
    conformance DB doesn't contain. The cross-check we CAN make is that
    the canonical-system URI is parseable (not 400 "Unrecognized").
    """
    from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI, fhir_uri_to_system

    seeded = [
        ("http://snomed.info/sct", "73211009"),
        ("http://www.nlm.nih.gov/research/umls/rxnorm", "860975"),
        ("http://hl7.org/fhir/sid/icd-10-cm", "E11"),
    ]
    saw_canonical = False
    for system_uri, code in seeded:
        r1 = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system_uri, "code": code},
        )
        if r1.status_code != 200:
            continue
        body1 = r1.json()
        if body1.get("resourceType") != "Parameters":
            continue
        params1 = body1.get("parameter", [])
        canonical_system = _extract_property(params1, "canonical-system")
        canonical_code = _extract_property(params1, "canonical-code")
        if not canonical_system or not canonical_code:
            continue
        saw_canonical = True

        # The canonical-system MUST be in SYSTEM_TO_FHIR_URI (or its alias map).
        assert canonical_system in SYSTEM_TO_FHIR_URI.values(), (
            f"$lookup canonical-system={canonical_system!r} for {system_uri}/{code}; "
            f"NOT in SYSTEM_TO_FHIR_URI registry. Round-trip would 400."
        )
        # And it MUST be recognized by the parser used by the next $lookup.
        assert fhir_uri_to_system(canonical_system) is not None, (
            f"fhir_uri_to_system({canonical_system!r}) returned None — round-trip "
            f"$lookup would 400 'Unrecognized system URI'."
        )
        # Round-trip $lookup must NOT 400 "Unrecognized system URI" — the
        # canonical-system MUST be parseable. 404 (code not seeded) is OK;
        # 200 (round-trip succeeded) is the gold standard.
        r2 = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": canonical_system, "code": canonical_code},
        )
        assert r2.status_code != 400, (
            f"round-trip $lookup(canonical-system={canonical_system!r}) → 400; "
            f"the canonical-system URI is not parseable by the same endpoint. "
            f"Same drift class as QA-043."
        )
        # 200 + Parameters OR 404 OperationOutcome (code not seeded) — both
        # confirm the URI was recognized.
        body2 = r2.json()
        assert body2.get("resourceType") in ("Parameters", "OperationOutcome"), (
            f"round-trip body resourceType={body2.get('resourceType')!r}; "
            f"expected Parameters or OperationOutcome"
        )
    if not saw_canonical:
        pytest.skip(
            "No seeded code emitted a canonical-system/canonical-code pair — "
            "production PF JSONs may not be loaded."
        )


def test_e11_lookup_canonical_system_value_in_SYSTEM_TO_FHIR_URI(fhir_client):
    """Cross-reference: the canonical-system property returned by $lookup
    MUST be one of the 8 URIs advertised in SYSTEM_TO_FHIR_URI. Any value
    outside this set indicates the helper is producing drift (e.g., a raw
    SAB label per QA-043).

    Per GLOBAL_RULES.md "Single Source of Truth": SYSTEM_TO_FHIR_URI is the
    canonical registry. Every system value flowing through $lookup responses
    MUST resolve to a value in this map.
    """
    from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI

    seeded = [
        ("http://snomed.info/sct", "73211009"),
        ("http://www.nlm.nih.gov/research/umls/rxnorm", "860975"),
        ("http://hl7.org/fhir/sid/icd-10-cm", "E11"),
    ]
    canonical_uris = set(SYSTEM_TO_FHIR_URI.values())
    saw_canonical = False
    for system_uri, code in seeded:
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system_uri, "code": code},
        )
        if r.status_code != 200:
            continue
        body = r.json()
        cs_val = _extract_property(body.get("parameter", []), "canonical-system")
        if cs_val is None:
            continue
        saw_canonical = True
        assert cs_val in canonical_uris, (
            f"$lookup canonical-system = {cs_val!r} for {system_uri}/{code}; "
            f"NOT in SYSTEM_TO_FHIR_URI. Same drift class as QA-043."
        )
    if not saw_canonical:
        pytest.skip("No seeded code emitted canonical-system; PF JSONs may not be loaded.")


# ---------------------------------------------------------------------------
# Probe class 2: POST body with canonical-system + canonical-code
# (positive success-shape assertion per GLOBAL_RULES.md "Test-too-lenient")
# ---------------------------------------------------------------------------


def test_e20_post_lookup_with_canonical_system_uri(fhir_client):
    """POST $lookup with a Parameters body using the canonical URI. The
    POST handler should accept the body the same way GET does — extracting
    system/code from valueUri/valueCode parameters.

    Per FHIR R4 §4.8.10.1.1 (lookup operation), POST with Parameters body
    is the spec-documented alternative to GET with query params. The
    `_extract_coding_from_parameters` helper handles valueCoding; this
    probe confirms the simpler valueUri/valueCode path still works.
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": "http://snomed.info/sct"},
            {"name": "code", "valueCode": "73211009"},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    assert r.status_code == 200, f"POST $lookup → {r.status_code}; body={r.text[:300]}"
    payload = r.json()
    assert payload.get("resourceType") == "Parameters"
    code_param = next(
        (p for p in payload.get("parameter", []) if p.get("name") == "code"),
        {},
    )
    assert code_param.get("valueCode") == "73211009", (
        f"POST $lookup response code mismatch: {code_param}"
    )


def test_e21_post_lookup_with_canonical_pair_from_prior_lookup(fhir_client):
    """POST $lookup using the canonical-system + canonical-code from a prior
    GET $lookup response. This is the URI-round-trip methodology applied to
    the POST handler.

    The POST handler should accept the canonical URI pair exactly like the
    GET handler. If GET round-trips but POST doesn't (or vice versa), the
    two paths have asymmetric acceptance — a wire-format drift.
    """
    seeded = [
        ("http://snomed.info/sct", "73211009"),
        ("http://www.nlm.nih.gov/research/umls/rxnorm", "860975"),
    ]
    saw_canonical = False
    for system_uri, code in seeded:
        r1 = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system_uri, "code": code},
        )
        if r1.status_code != 200:
            continue
        body1 = r1.json()
        canonical_system = _extract_property(body1.get("parameter", []), "canonical-system")
        canonical_code = _extract_property(body1.get("parameter", []), "canonical-code")
        if not canonical_system or not canonical_code:
            continue
        saw_canonical = True

        body2 = {
            "resourceType": "Parameters",
            "parameter": [
                {"name": "system", "valueUri": canonical_system},
                {"name": "code", "valueCode": canonical_code},
            ],
        }
        r2 = fhir_client.post("/fhir/CodeSystem/$lookup", json=body2)
        assert r2.status_code == 200, (
            f"POST round-trip failed: canonical-system={canonical_system!r} "
            f"canonical-code={canonical_code!r} → {r2.status_code}; body={r2.text[:300]}"
        )
    if not saw_canonical:
        pytest.skip("No seeded code emitted canonical pair.")


# ---------------------------------------------------------------------------
# Probe class 3: SEARCH params — honest advertisement + edge cases
# Per AGENTS.md NOT A BUG Registry: empty Bundle is conformant for a
# non-persisting server. These probes confirm the param acceptance is
# structurally honest (every advertised param accepts every value shape)
# and the response is the conformant empty Bundle (NOT 4xx/5xx).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "param,value",
    [
        ("url", "http://snomed.info/sct"),
        ("url", "http://loinc.org"),
        ("version", "2024-09-01"),
        ("name", "SNOMEDCT"),
        ("title", "SNOMED Clinical Terms"),
        ("status", "active"),
        ("status", "draft"),
        ("status", "retired"),
        ("status", "unknown"),
    ],
)
def test_e30_search_codesystem_accepts_advertised_param(fhir_client, param, value):
    """Every advertised SEARCH param (url/version/name/title/status per
    §4.8.1.1) accepts a value and returns a 200 Bundle. The bundle is empty
    (medterm4ds doesn't persist resources — INTENDED per AGENTS.md), but
    the route exists, accepts the param, and returns the conformant shape.

    This is a positive success-shape assertion (200 + Bundle body), not a
    negative-only check, per GLOBAL_RULES.md "Test-too-lenient".
    """
    r = fhir_client.get(f"/fhir/CodeSystem", params={param: value})
    assert r.status_code == 200, f"GET /fhir/CodeSystem?{param}={value} → {r.status_code}"
    body = r.json()
    assert body.get("resourceType") == "Bundle", (
        f"expected Bundle, got {body.get('resourceType')!r}"
    )
    assert body.get("type") in {"searchset", "batch", "history"}, (
        f"Bundle.type={body.get('type')!r}; expected searchset"
    )


def test_e31_search_codesystem_combined_params(fhir_client):
    """Combined search params (e.g., url + version + status) must not 500.
    Per FHIR R4 §4.8.1.1, search params MAY be combined; medterm4ds accepts
    all combinations (returning an empty Bundle when no persisted resources
    match — INTENDED).
    """
    r = fhir_client.get(
        "/fhir/CodeSystem",
        params={
            "url": "http://snomed.info/sct",
            "version": "2024-09-01",
            "status": "active",
            "name": "SNOMEDCT",
            "title": "SNOMED Clinical Terms",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("resourceType") == "Bundle"
    assert body.get("total", 0) == 0  # No persisted resources


def test_e32_search_codesystem_long_url_no_500(fhir_client):
    """Very long url value (5K chars) must not 500. Per FHIR R4 search-param
    rules, a server SHOULD accept arbitrary string values for token/string
    params; rejecting them with 4xx is conformant but 5xx is a wire-format
    violation. CPU-waste / DoS surface (cf. QA-027 for $expand filter)."""
    long_url = "http://example.com/" + "x" * 5000
    r = fhir_client.get("/fhir/CodeSystem", params={"url": long_url})
    assert 200 <= r.status_code < 500, (
        f"long url → {r.status_code}; expected 2xx (empty Bundle) or 4xx, NOT 5xx"
    )
    if r.status_code == 200:
        body = r.json()
        assert body.get("resourceType") == "Bundle"


def test_e33_search_codesystem_url_special_chars(fhir_client):
    """URL with special characters (URL-encoded) must not 500. The conformance
    surface should accept any well-formed URL string and return either an
    empty Bundle (200) or a FHIR-shaped 4xx OperationOutcome — never a raw
    framework 500."""
    r = fhir_client.get(
        "/fhir/CodeSystem",
        params={"url": "http://example.com/CodeSystem/with spaces&special?chars=1"},
    )
    assert 200 <= r.status_code < 500
    if r.status_code == 200:
        body = r.json()
        assert body.get("resourceType") == "Bundle"


def test_e34_search_codesystem_exact_canonical_uri(fhir_client):
    """Search by an EXACT canonical URI from SYSTEM_TO_FHIR_URI. Even though
    medterm4ds doesn't persist CodeSystem resources, the route should accept
    the canonical URI without error. Returning an empty Bundle is honest
    advertisement (the route exists; no resources match).
    """
    from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI

    for uri in SYSTEM_TO_FHIR_URI.values():
        r = fhir_client.get("/fhir/CodeSystem", params={"url": uri})
        assert r.status_code == 200, f"GET /fhir/CodeSystem?url={uri} → {r.status_code}"
        body = r.json()
        assert body.get("resourceType") == "Bundle"


def test_e35_search_codesystem_honest_empty_bundle_shape(fhir_client):
    """Search response Bundle MUST be conformant: resourceType=Bundle,
    type=searchset, total=0, entry=[]. Empty Bundle is INTENDED per
    AGENTS.md (medterm4ds doesn't persist resources), but the SHAPE matters
    — clients depend on `entry[]` being present (even when empty) to iterate.
    """
    r = fhir_client.get("/fhir/CodeSystem", params={"url": "http://snomed.info/sct"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("resourceType") == "Bundle"
    assert body.get("type") == "searchset"
    assert body.get("total") == 0
    assert isinstance(body.get("entry"), list)
    assert body.get("entry") == []


# ---------------------------------------------------------------------------
# Probe class 4: CodeSystem property types
# Per FHIR R4 §4.8.11 concept properties use typed valueXxx — `abstract`
# is boolean, `display` is string. Verify typed encoding on $lookup.
# ---------------------------------------------------------------------------


def test_e40_lookup_abstract_property_uses_valueBoolean(fhir_client):
    """`abstract` is a FHIR R4 concept property of type boolean (§4.8.11).
    The $lookup response MUST encode it as `valueBoolean`, not valueString.
    This pins the FHIR type 'boolean' encoding on $lookup Out parameters.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": "73211009"},
    )
    assert r.status_code == 200
    body = r.json()
    abstract_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "abstract"),
        None,
    )
    assert abstract_param is not None, "missing top-level `abstract` parameter"
    assert "valueBoolean" in abstract_param, (
        f"abstract parameter must use valueBoolean; got keys={list(abstract_param.keys())}"
    )
    assert isinstance(abstract_param["valueBoolean"], bool)


def test_e41_lookup_system_property_uses_valueUri(fhir_client):
    """`system` is a URI per FHIR R4 §4.8.10.1.3 Out Parameters. The
    $lookup response MUST encode the top-level `system` parameter as
    `valueUri`, not valueString.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": "73211009"},
    )
    assert r.status_code == 200
    body = r.json()
    sys_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "system"),
        None,
    )
    assert sys_param is not None
    assert "valueUri" in sys_param, (
        f"system parameter must use valueUri; got keys={list(sys_param.keys())}"
    )


def test_e42_lookup_code_property_uses_valueCode(fhir_client):
    """`code` is FHIR type 'code' (§4.8.10.1.2 Out). The $lookup response
    MUST encode top-level `code` as `valueCode`.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": "73211009"},
    )
    assert r.status_code == 200
    body = r.json()
    code_param = next(
        (p for p in body.get("parameter", []) if p.get("name") == "code"),
        None,
    )
    assert code_param is not None
    assert "valueCode" in code_param


# ---------------------------------------------------------------------------
# Probe class 5: $lookup with `property` parameter — filter properties
# (FHIR R4 §4.8.10.1.4 — In parameter `property` filters Out properties)
# Per AGENTS.md NOT A BUG Registry: medterm4ds returns its full property
# set anyway; the param is accepted for spec-compatibility.
# ---------------------------------------------------------------------------


def test_e50_lookup_with_property_filter_accepted(fhir_client):
    """The `property` In parameter filters which custom properties to return.
    medterm4ds accepts it and returns all properties it has (INTENDED per
    AGENTS.md). The probe confirms the param doesn't 500 — spec-compatibility.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": "http://snomed.info/sct",
            "code": "73211009",
            "property": "cui",
        },
    )
    assert r.status_code == 200, f"property filter → {r.status_code}"


def test_e51_lookup_with_repeating_property_accepted(fhir_client):
    """Repeating `property` parameter (FHIR R4 §4.8.10.1.4 allows 0..*).
    The server SHOULD accept multiple `property` values and return all
    matching properties. Per AGENTS.md, medterm4ds returns all anyway —
    the probe confirms no 500 on a multi-valued param.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params=[
            ("system", "http://snomed.info/sct"),
            ("code", "73211009"),
            ("property", "cui"),
            ("property", "tty"),
        ],
    )
    assert r.status_code == 200, f"repeating property → {r.status_code}"


# ---------------------------------------------------------------------------
# Probe class 6: Unusual code values — boundary / adversarial inputs
# ---------------------------------------------------------------------------


def test_e60_lookup_one_char_code_no_500(fhir_client):
    """A 1-character code is unusual but spec-permitted. The server should
    return either 200 (if found) or 404 OperationOutcome (if not). NEVER 500.
    """
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": "X"},
    )
    assert r.status_code in (200, 404), f"1-char code → {r.status_code}"


def test_e61_lookup_long_code_no_500(fhir_client):
    """A very long code (>1000 chars). The server should return 404
    OperationOutcome (no such code). Never 500 — that would be a DoS /
    CPU-waste surface (cf. QA-027 for $expand filter)."""
    long_code = "A" * 1500
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": long_code},
    )
    # Expect 404 OperationOutcome; accept 200 (unlikely); reject 5xx.
    assert r.status_code in (200, 404), (
        f"long code → {r.status_code}; expected 2xx/4xx, NOT 5xx. body={r.text[:200]}"
    )


def test_e62_lookup_code_with_special_chars_no_500(fhir_client):
    """Code with special characters (URL-encoded). The server should return
    404 OperationOutcome (no such code). Never 500."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct", "code": "code with spaces & special?chars=1"},
    )
    assert r.status_code in (200, 404)


def test_e63_lookup_code_with_embedded_uri_no_500(fhir_client):
    """Code that looks like a URI (e.g., "http://example.com/code"). The
    server should treat the value as a literal code (not parse it as a
    system URI). Returns 404 OperationOutcome."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": "http://snomed.info/sct",
            "code": "http://example.com/some/code",
        },
    )
    assert r.status_code in (200, 404)


# ---------------------------------------------------------------------------
# Probe class 7: Mixed-case system URIs
# RFC 3986 §3.1: scheme is case-insensitive. The FHIR server SHOULD normalize
# or reject mixed-case scheme variants. A 5xx response is a wire-format
# violation; a FHIR-shaped 4xx is conformant.
# ---------------------------------------------------------------------------


def test_e70_lookup_uppercase_scheme_no_500(fhir_client):
    """Mixed-case system URI with uppercase scheme `HTTP://` — RFC 3986
    says scheme is case-insensitive. The server SHOULD normalize (200) or
    reject (FHIR-shaped 4xx). NEVER 5xx."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "HTTP://snomed.info/sct", "code": "73211009"},
    )
    assert r.status_code in (200, 400, 404), (
        f"HTTP:// → {r.status_code}; expected normalized 2xx or FHIR-shaped 4xx"
    )


def test_e71_lookup_trailing_slash_no_500(fhir_client):
    """System URI with trailing slash `http://snomed.info/sct/`. The
    fhir_uri_to_system helper has explicit trailing-slash stripping
    (`stripped = uri.rstrip("/")`). Probe confirms the wire path resolves
    via that branch."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://snomed.info/sct/", "code": "73211009"},
    )
    assert r.status_code == 200, (
        f"trailing slash → {r.status_code}; helper should strip trailing slash"
    )


def test_e72_lookup_via_alias_uri(fhir_client):
    """Lookup via a URI alias (e.g. urn:oid:2.16.840.1.113883.6.96 for
    SNOMED CT). The FHIR_URI_ALIASES map registers backwards-compat
    aliases. Probe confirms alias resolution succeeds for a known code."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": "urn:oid:2.16.840.1.113883.6.96",
            "code": "73211009",
        },
    )
    assert r.status_code == 200, f"alias urn:oid → {r.status_code}"


def test_e73_lookup_unknown_uri_returns_fhir_400(fhir_client):
    """Unknown system URI must return FHIR-shaped 400 OperationOutcome
    (not Starlette default / plain-text 500)."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={"system": "http://example.com/unknown", "code": "73211009"},
    )
    assert r.status_code == 400, f"unknown URI → {r.status_code}"
    body = r.json()
    assert body.get("resourceType") == "OperationOutcome"


# ---------------------------------------------------------------------------
# Probe class 8: $lookup on all 8 supported systems
# (every entry in SYSTEM_TO_FHIR_URI must accept a known code via $lookup)
# ---------------------------------------------------------------------------


def test_e80_lookup_on_all_advertised_systems(fhir_client):
    """For every system URI advertised in SYSTEM_TO_FHIR_URI, the $lookup
    route must exist, accept a known code (or return 404 OperationOutcome
    if the code isn't in the seeded DB), and never return Starlette's
    default 404/405. This is a positive success-shape assertion on the
    advertisement-vs-implementation cross-check.

    The conformance fixture seeds codes for SNOMED, RXNORM, ICD10CM. Other
    systems will return 404 OperationOutcome for arbitrary codes (no seeded
    data) — that's conformant; what matters is the route exists and returns
    FHIR-shaped responses.
    """
    from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI

    seeded_codes = {
        "SNOMEDCT_US": "73211009",
        "RXNORM": "860975",
        "ICD10CM": "E11",
    }
    for source, uri in SYSTEM_TO_FHIR_URI.items():
        code = seeded_codes.get(source, "0")
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": uri, "code": code},
        )
        assert r.status_code in (200, 404), (
            f"$lookup {source} ({uri}) → {r.status_code}; expected 200 (seeded) "
            f"or 404 OperationOutcome (not seeded). Body: {r.text[:200]}"
        )
        body = r.json()
        assert body.get("resourceType") in ("Parameters", "OperationOutcome")


def test_e81_capabilitystatement_advertises_all_8_systems(fhir_client):
    """The CapabilityStatement extension `capabilitystatement-supported-system`
    MUST advertise all 8 systems in SYSTEM_TO_FHIR_URI. Cross-check the
    advertisement (extension[]) against the canonical map.
    """
    from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI

    SUPPORTED_SYS_URL = "http://hl7.org/fhir/StructureDefinition/capabilitystatement-supported-system"
    r = fhir_client.get("/fhir/metadata")
    assert r.status_code == 200
    body = r.json()
    extensions = body.get("extension", [])
    advertised = {
        e.get("valueUri")
        for e in extensions
        if e.get("url") == SUPPORTED_SYS_URL
    }
    canonical = set(SYSTEM_TO_FHIR_URI.values())
    missing = canonical - advertised
    assert not missing, (
        f"CapabilityStatement extension missing advertised systems: {sorted(missing)}"
    )


# ---------------------------------------------------------------------------
# Probe class 9: SKEPTIC + HISTORIAN fix survival (regression guards)
# ---------------------------------------------------------------------------


def test_e90_qa043_canonical_system_no_raw_sab_label(fhir_client):
    """Regression guard for QA-043: the canonical-system property MUST be
    a FHIR URI (starts with http://, https://, or urn:). NEVER a raw SAB
    label like "icd10" or "snomedct_us".
    """
    seeded = [
        ("http://snomed.info/sct", "73211009"),
        ("http://www.nlm.nih.gov/research/umls/rxnorm", "860975"),
        ("http://hl7.org/fhir/sid/icd-10-cm", "E11"),
    ]
    raw_sab_labels = {"icd10", "icd10cm", "snomedct_us", "rxnorm", "lnc", "cpt", "hcpcs", "cvx", "icd10pcs"}
    saw_canonical = False
    for system_uri, code in seeded:
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system_uri, "code": code},
        )
        if r.status_code != 200:
            continue
        body = r.json()
        cs_val = _extract_property(body.get("parameter", []), "canonical-system")
        if cs_val is None:
            continue
        saw_canonical = True
        assert cs_val.startswith(("http://", "https://", "urn:")), (
            f"canonical-system={cs_val!r} — must be FHIR URI; raw SAB label leaked."
        )
        assert cs_val not in raw_sab_labels, (
            f"canonical-system={cs_val!r} — this is a raw SAB label. QA-043 regression."
        )
    if not saw_canonical:
        pytest.skip("No canonical-system emitted; PF JSONs may not be loaded.")


def test_e91_qa044_no_silent_ternary_in_source():
    """Regression guard for QA-044: the bare ternary
    `if fhir_uri else raw_sab` MUST be gone from fhir_api.py source; the
    fallback path MUST log at WARNING before emitting the raw value.
    """
    import inspect

    from medterm4ds.apps import fhir_api

    src = inspect.getsource(fhir_api)
    assert "if fhir_uri else raw_sab" not in src, (
        "QA-044 regression: silent ternary `if fhir_uri else raw_sab` "
        "is back in fhir_api.py source."
    )
    assert "sab_label_to_fhir_uri" in src
    assert "logger.warning" in src


# ---------------------------------------------------------------------------
# Probe class 10: Carries-forward confirmation
# CF-SKEPTIC-CS01-02 — match-type uses raw engine vocabulary
# CF-SKEPTIC-CS01-03 — fixture loads production JSONs
# (These are not bugs; EXPLORER re-confirms the carries-forward stand.)
# ---------------------------------------------------------------------------


def test_e92_cf_skeptic_cs01_02_match_type_raw_vocabulary_stands(fhir_client):
    """CF-SKEPTIC-CS01-02 (re-confirmed by HISTORIAN test_h03): the
    `match-type` custom property uses raw engine vocabulary
    (`broader`, `exact`, `same_cui`, etc.) NOT in the FHIR R4
    ConceptMapEquivalence enum. This probe documents the carry-forward
    still stands; decision deferred to TERMINOLOGIST.

    Same documentation shape as HISTORIAN's test_h03 — asserts the wire
    value IS the raw vocabulary (not a FHIR enum), confirming the drift.
    """
    if not _pf_loaded():
        pytest.skip("PF JSONs not loaded; CF-SKEPTIC-CS01-02 cannot be probed on wire.")
    seeded = [
        ("http://snomed.info/sct", "73211009"),
        ("http://www.nlm.nih.gov/research/umls/rxnorm", "860975"),
        ("http://hl7.org/fhir/sid/icd-10-cm", "E11"),
    ]
    # CR-014 (milestone-2 review): import the single source of truth.
    from medterm4ds.engines.fhir import FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    FHIR_R4_EQUIVALENCE = FHIR_R4_CONCEPT_MAP_EQUIVALENCE
    saw_match_type = False
    for system_uri, code in seeded:
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": system_uri, "code": code},
        )
        if r.status_code != 200:
            continue
        body = r.json()
        mt_val = _extract_property(body.get("parameter", []), "match-type")
        if mt_val is None:
            continue
        saw_match_type = True
        assert mt_val not in FHIR_R4_EQUIVALENCE, (
            f"match-type={mt_val!r} IS in FHIR R4 enum — CF-SKEPTIC-CS01-02 may be obsolete."
        )
    if not saw_match_type:
        pytest.skip("No match-type emitted on seeded codes.")


def test_e93_cf_skeptic_cs01_03_fixture_loads_production_jsons():
    """CF-SKEPTIC-CS01-03 (still open): the conformance fixture does NOT
    override MEDTERM4DS_FHIR4PX_BASELINE, so production patient-friendly
    JSONs at /mnt/d/medterm4ds/reports/fhir4px/ are loaded. This is
    beneficial for testing (allows wire probes against real PF data) but
    indicates a fixture-isolation gap.

    This probe documents the carry-forward still stands — production JSONs
    exist and would be loaded by an unconfigured fhir_client fixture.
    """
    if not _pf_loaded():
        pytest.skip("Production PF JSONs not present — CF-SKEPTIC-CS01-03 may be obsolete.")
    # If we reach here, the carry-forward still applies.
    # Document at least one source has data.
    sources_with_data = [
        s for s in PF_SOURCES
        if (PF_BASELINE / f"patient_friendly_{s}.json").exists()
    ]
    assert sources_with_data, "No PF JSONs found despite _pf_loaded() returning True"


# ---------------------------------------------------------------------------
# Probe class 11: $lookup with `property` parameter for parent/child
# hierarchy (FHIR R4 §4.8.11.1.2 — child/parent concept relationships)
# Per AGENTS.md: medterm4ds doesn't currently emit parent/child custom
# properties on $lookup; probe confirms the param is accepted even when
# the result is empty (no 500).
# ---------------------------------------------------------------------------


def test_e94_lookup_with_parent_property_filter_accepted(fhir_client):
    """`property=parent` filter on $lookup. Per FHIR R4 §4.8.11.1.2,
    `parent` is a valid CodeSystem.property.code for hierarchical code
    systems. medterm4ds doesn't currently emit `parent` as a custom
    property, but the param should be accepted without error."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": "http://snomed.info/sct",
            "code": "73211009",
            "property": "parent",
        },
    )
    assert r.status_code == 200


def test_e95_lookup_with_child_property_filter_accepted(fhir_client):
    """`property=child` filter on $lookup. Same as test_e94 for `child`."""
    r = fhir_client.get(
        "/fhir/CodeSystem/$lookup",
        params={
            "system": "http://snomed.info/sct",
            "code": "73211009",
            "property": "child",
        },
    )
    assert r.status_code == 200
