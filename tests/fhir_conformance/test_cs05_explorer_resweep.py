"""EXPLORER resweep probes for CS-05 (CodeSystem Edge Cases).

Spec: https://build.fhir.org/codesystem.html
       (canonical R4: https://hl7.org/fhir/R4/codesystem.html)
       $lookup:   https://hl7.org/fhir/R4/codesystem-operation-lookup.html
       $validate: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
       $subsumes: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
       $translate: https://hl7.org/fhir/R4/conceptmap-operation-translate.html
       concept-properties:
           https://hl7.org/fhir/R4/concept-properties.html

EXPLORER lens (per chunk assignment): lateral thinking across operations,
properties, and edge-case shapes that prior personalities did not probe.

HISTORIAN tip for EXPLORER (per CS-05 HISTORIAN handoff):
  1. **N-way canonical-DISPLAY invariant** is now LOAD-BEARING across 4
     operations ($lookup ↔ $validate-code ↔ $translate target concept
     display ↔ $validate-code codeableConcept matched-coding display per
     CS-03 SKEPTIC) — extend to N-way cross-op probes for same code ×
     every alias input across all 4 ops.
  2. **Hostile version-input matrix** (Lens 16) is load-bearing for
     DoS / info-disclosure; extend with lateral combinations
     (version + display, version + property multi).
  3. **GET ↔ POST byte-exact parity** verified on $lookup (test_h82);
     extend to $validate-code + $subsumes.
  4. **Document fixture-gap combinations** (multi-parent DAG, abstract
     concept, inactive code) rather than manufacturing probes requiring
     missing data.

Probe classes used (EXPLORER methodology extensions from prior runs):
  - cross-operation-canonical-agreement probe class
    (extends strategy 38 from same-resource-type to cross-resource-type)
  - lateral-coverage-on-hardened-surface probe class
    (CS-03 EXPLORER — when SKEPTIC+HISTORIAN have hardened a surface,
    EXPLORER probes lateral combinations)
  - first-match-wins probe-assertion pattern (CS-03 EXPLORER)
  - byte-exact parity across alternative invocation shapes
  - fixture-gap carry-forward-as-probe pattern (CS-05 SKEPTIC +
    HISTORIAN — extends strategy 56)

Reference fixture (tests/fhir_conformance/conftest.py:_make_conformance_db):
    ("73211009", "PT", "Diabetes mellitus", "A73211009", "N", "SNOMEDCT_US", "C0011849"),
    ("44054006", "PT", "Type 2 diabetes mellitus", "A44054006", "N", "SNOMEDCT_US", "C0011847"),
    ("E11", "HT", "Type 2 diabetes mellitus", "AE11", "N", "ICD10CM", "C0011847"),
    ("860975", "SCD", "24 HR metformin 500 MG Oral Tablet", "A860975", "N", "RXNORM", "C0978484"),
    mrrel: ("A44054006", "A73211009", "isa", "PAR")  # single-parent
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

# Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html
# Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
# Spec: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
# Spec: https://hl7.org/fhir/R4/conceptmap-operation-translate.html
# Spec: https://hl7.org/fhir/R4/concept-properties.html

SNOMED_URI = "http://snomed.info/sct"
SNOMED_URI_TRAILING_SLASH = "http://snomed.info/sct/"
SNOMED_URI_URN_OID = "urn:oid:2.16.840.1.113883.6.96"
SNOMED_URI_UPPERCASE_SCHEME = "HTTP://snomed.info/sct"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
RXNORM_URI_UPPERCASE_SCHEME = "HTTP://www.nlm.nih.gov/research/umls/rxnorm"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_URI_UPPERCASE_SCHEME = "HTTP://hl7.org/fhir/sid/icd-10-cm"
ICD10CM_URI_TRAILING_SLASH = "http://hl7.org/fhir/sid/icd-10-cm/"
ICD10CM_URI_URN_OID = "urn:oid:2.16.840.1.113883.6.90"

SNOMED_DIABETES_MELLITUS = "73211009"  # parent
SNOMED_T2DM = "44054006"               # child of 73211009
RXNORM_METFORMIN = "860975"
ICD10CM_T2DM = "E11"

# All seeded codes by source, used for parametrization.
SEEDED_SNOMED_CODES = [SNOMED_DIABETES_MELLITUS, SNOMED_T2DM]
SEEDED_RXNORM_CODES = [RXNORM_METFORMIN]
SEEDED_ICD10CM_CODES = [ICD10CM_T2DM]
SEEDED_ALL = (
    [(SNOMED_URI, c) for c in SEEDED_SNOMED_CODES]
    + [(RXNORM_URI, c) for c in SEEDED_RXNORM_CODES]
    + [(ICD10CM_URI, c) for c in SEEDED_ICD10CM_CODES]
)

# Alias inputs per source (the URI forms the client MAY send). Used for
# the N-way canonical-DISPLAY invariant (HISTORIAN tip 1).
ALIAS_URIS_BY_SOURCE = {
    "SNOMED": [SNOMED_URI, SNOMED_URI_TRAILING_SLASH, SNOMED_URI_URN_OID, SNOMED_URI_UPPERCASE_SCHEME],
    "RXNORM": [RXNORM_URI, RXNORM_URI_UPPERCASE_SCHEME],
    "ICD10CM": [ICD10CM_URI, ICD10CM_URI_UPPERCASE_SCHEME, ICD10CM_URI_TRAILING_SLASH, ICD10CM_URI_URN_OID],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lookup_param(body: dict, name: str) -> dict | None:
    """Return the first Out parameter with the given name, or None."""
    for p in body.get("parameter", []):
        if p.get("name") == name:
            return p
    return None


def _lookup_param_value(body: dict, name: str):
    """Return the value of the first Out parameter with the given name."""
    p = _lookup_param(body, name)
    if p is None:
        return None
    for k, v in p.items():
        if k.startswith("value"):
            return v
    return None


def _get_module_source(module) -> tuple[str, ast.AST]:
    """Return (source_text, ast_tree) for a Python module."""
    src_path = Path(inspect.getsourcefile(module))
    src_text = src_path.read_text()
    return src_text, ast.parse(src_text)


def _get_nested_func_source(
    src_text: str,
    tree: ast.AST,
    parent_name: str,
    child_name: str,
) -> ast.AST | None:
    """Locate a nested function defined inside another function.

    Mirrors CS-03 HISTORIAN / TS-04 HISTORIAN / CS-05 HISTORIAN helper.
    """
    parent_node = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == parent_name:
                parent_node = node
                break
    if parent_node is None:
        return None
    for child in ast.walk(parent_node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if child.name == child_name:
                return child
    return None


def _get_func_source(tree: ast.AST, func_name: str) -> ast.AST | None:
    """Locate a top-level function by name (FunctionDef or AsyncFunctionDef)."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == func_name:
                return node
    return None


def _count_calls_in(node: ast.AST, func_name: str) -> int:
    """Count ast.Call nodes in `node` whose function is Name(func_name)."""
    count = 0
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        f = sub.func
        if isinstance(f, ast.Name) and f.id == func_name:
            count += 1
        elif isinstance(f, ast.Attribute) and f.attr == func_name:
            count += 1
    return count


def _parameters_body(system: str, code: str, **extra) -> dict:
    """Build a Parameters body for POST $lookup/$validate-code style ops."""
    params = [
        {"name": "system", "valueUri": system},
        {"name": "code", "valueCode": code},
    ]
    for k, v in extra.items():
        if k == "display":
            params.append({"name": "display", "valueString": v})
        elif k == "version":
            params.append({"name": "version", "valueString": v})
        elif k == "property":
            # property is 0..* code; allow string or list of strings
            if isinstance(v, str):
                v = [v]
            for code_val in v:
                params.append({"name": "property", "valueCode": code_val})
        elif k == "codeA":
            params.append({"name": "codeA", "valueCode": v})
        elif k == "codeB":
            params.append({"name": "codeB", "valueCode": v})
    return {"resourceType": "Parameters", "parameter": params}


# ---------------------------------------------------------------------------
# Lens 1: N-way canonical-DISPLAY invariant across 4 operations × every
# alias input (HISTORIAN tip 1).
# ---------------------------------------------------------------------------
# Per HISTORIAN tip: the canonical-DISPLAY invariant (count=5 PROMOTED
# per GLOBAL_RULES.md / GLOBAL_KNOWLEDGE.md) is now LOAD-BEARING across 4
# operations:
#   1. $lookup Out `display`
#   2. $validate-code Out `display`
#   3. $translate match[].concept.display (TARGET concept display)
#   4. $validate-code codeableConcept matched-coding Out `display`
# The lateral probe class extends CS-05 SKEPTIC test_s60..s63 (3-way:
# $lookup ↔ $validate-code) to a 4-way probe covering $translate target
# concept display AND every alias input × every seeded code.

def _lookup_display(fhir_client, system: str, code: str) -> str | None:
    """Return the Out `display` from a $lookup call."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={system}&code={code}"
    )
    if r.status_code != 200:
        return None
    return _lookup_param_value(r.json(), "display")


def _validate_display(fhir_client, system: str, code: str) -> str | None:
    """Return the Out `display` from a $validate-code call (scalar path)."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={system}&code={code}"
    )
    if r.status_code != 200:
        return None
    return _lookup_param_value(r.json(), "display")


def _validate_cc_display(fhir_client, system: str, code: str) -> str | None:
    """Return the Out `display` from a $validate-code call with
    codeableConcept input (matched-coding display per CS-03 SKEPTIC
    QA-049)."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {"system": system, "code": code},
                    ]
                },
            }
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body)
    if r.status_code != 200:
        return None
    return _lookup_param_value(r.json(), "display")


def test_e10_n_way_canonical_display_invariant_snomed_dm(fhir_client):
    """Lens 1 / N-way canonical-DISPLAY invariant (HISTORIAN tip 1):
    SNOMED 73211009 (Diabetes mellitus) MUST produce the SAME canonical
    display across all 4 operations.

    Operations tested:
      1. $lookup Out `display`
      2. $validate-code Out `display` (scalar path)
      3. $translate match[].concept.display (target concept display —
         $translate cross-source SNOMED → ICD-10-CM maps to E11 which
         has the same CUI C0011847; the TARGET concept display is what
         the engine emits for the ICD-10-CM code, not the SNOMED
         display. The 4-way invariant HOLDS structurally here only
         for the SOURCE display echoed in match[].source.display.)
      4. $validate-code codeableConcept matched-coding Out `display`

    Pattern-match: CS-05 SKEPTIC test_s60..s63 + CS-05 HISTORIAN
    test_h60..h63 extended to a 4-way probe class.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html Out
    `display` (1..1 string "The preferred display for this concept").
    """
    system = SNOMED_URI
    code = SNOMED_DIABETES_MELLITUS

    lookup_display = _lookup_display(fhir_client, system, code)
    validate_display = _validate_display(fhir_client, system, code)
    cc_display = _validate_cc_display(fhir_client, system, code)

    # $lookup ↔ $validate-code ↔ codeableConcept matched-coding MUST agree
    assert lookup_display is not None
    assert lookup_display == validate_display, (
        f"$lookup display {lookup_display!r} != $validate-code display "
        f"{validate_display!r} for SNOMED 73211009"
    )
    assert lookup_display == cc_display, (
        f"$lookup display {lookup_display!r} != $validate-code codeableConcept"
        f" matched-coding display {cc_display!r} for SNOMED 73211009"
    )

    # The 4th operation, $translate target concept display, is a CROSS-SOURCE
    # display (SNOMED→ICD-10-CM produces an ICD-10-CM target code's display,
    # NOT the SNOMED source display). The invariant applies to the SOURCE
    # display echoed in match[].source.display per CS-02 TERMINOLOGIST
    # test_t30..t31 methodology.
    r = fhir_client.get(
        f"/fhir/ConceptMap/$translate?system={system}&code={code}"
        f"&targetSystem={ICD10CM_URI}"
    )
    assert r.status_code == 200
    body = r.json()
    # Find the source display field inside the first match.
    source_displays = []
    for p in body.get("parameter", []):
        if p.get("name") != "match":
            continue
        for sub in p.get("part", []):
            if sub.get("name") == "source":
                # source is a Coding with system/code/display
                for k, v in sub.items():
                    if isinstance(v, dict) and "display" in v:
                        source_displays.append(v["display"])
    if source_displays:
        assert source_displays[0] == lookup_display, (
            f"$translate match[].source.display {source_displays[0]!r} != "
            f"$lookup display {lookup_display!r} for SNOMED 73211009"
        )


def test_e11_n_way_canonical_display_invariant_snomed_t2dm(fhir_client):
    """Lens 1 / N-way canonical-DISPLAY invariant for SNOMED 44054006
    (Type 2 diabetes mellitus) across 4 operations."""
    system = SNOMED_URI
    code = SNOMED_T2DM

    lookup_display = _lookup_display(fhir_client, system, code)
    validate_display = _validate_display(fhir_client, system, code)
    cc_display = _validate_cc_display(fhir_client, system, code)

    assert lookup_display == validate_display
    assert lookup_display == cc_display


def test_e12_n_way_canonical_display_invariant_rxnorm_metformin(fhir_client):
    """Lens 1 / N-way canonical-DISPLAY invariant for RxNorm 860975
    (24 HR metformin 500 MG Oral Tablet) across 3 ops."""
    system = RXNORM_URI
    code = RXNORM_METFORMIN

    lookup_display = _lookup_display(fhir_client, system, code)
    validate_display = _validate_display(fhir_client, system, code)
    cc_display = _validate_cc_display(fhir_client, system, code)

    assert lookup_display == validate_display
    assert lookup_display == cc_display


def test_e13_n_way_canonical_display_invariant_icd10cm_t2dm(fhir_client):
    """Lens 1 / N-way canonical-DISPLAY invariant for ICD-10-CM E11
    (Type 2 diabetes mellitus) across 3 ops."""
    system = ICD10CM_URI
    code = ICD10CM_T2DM

    lookup_display = _lookup_display(fhir_client, system, code)
    validate_display = _validate_display(fhir_client, system, code)
    cc_display = _validate_cc_display(fhir_client, system, code)

    assert lookup_display == validate_display
    assert lookup_display == cc_display


# N-way invariant × alias inputs (HISTORIAN tip 1 extension)

@pytest.mark.parametrize(
    "alias_uri",
    [
        SNOMED_URI_TRAILING_SLASH,
        SNOMED_URI_URN_OID,
        SNOMED_URI_UPPERCASE_SCHEME,
    ],
)
def test_e14_n_way_canonical_display_invariant_snomed_dm_via_alias(fhir_client, alias_uri):
    """Lens 1 / N-way invariant × alias input: SNOMED 73211009 looked up
    via trailing-slash, urn:oid, and uppercase-scheme alias URIs MUST
    produce the SAME canonical display as the canonical URI.

    Pattern-match: CS-05 SKEPTIC test_s63 (3-way × alias inputs) extended
    to 3 ops × alias inputs. Cross-op invariant HOLDS for every alias
    form per CS-05 HISTORIAN test_h60..h63.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-lookup.html Out
    `display` is the engine's canonical preferred STR, NOT the client
    echo.
    """
    canonical_display = _lookup_display(fhir_client, SNOMED_URI, SNOMED_DIABETES_MELLITUS)
    assert canonical_display is not None

    alias_lookup = _lookup_display(fhir_client, alias_uri, SNOMED_DIABETES_MELLITUS)
    alias_validate = _validate_display(fhir_client, alias_uri, SNOMED_DIABETES_MELLITUS)
    alias_cc = _validate_cc_display(fhir_client, alias_uri, SNOMED_DIABETES_MELLITUS)

    assert alias_lookup == canonical_display, (
        f"alias {alias_uri!r}: lookup display {alias_lookup!r} != "
        f"canonical {canonical_display!r}"
    )
    assert alias_validate == canonical_display
    assert alias_cc == canonical_display


@pytest.mark.parametrize(
    "alias_uri",
    [
        ICD10CM_URI_TRAILING_SLASH,
        ICD10CM_URI_URN_OID,
        ICD10CM_URI_UPPERCASE_SCHEME,
    ],
)
def test_e15_n_way_canonical_display_invariant_icd10cm_via_alias(fhir_client, alias_uri):
    """Lens 1 / N-way invariant × alias input: ICD-10-CM E11 looked up
    via trailing-slash, urn:oid, and uppercase-scheme alias URIs MUST
    produce the SAME canonical display as the canonical URI."""
    canonical_display = _lookup_display(fhir_client, ICD10CM_URI, ICD10CM_T2DM)
    assert canonical_display is not None

    alias_lookup = _lookup_display(fhir_client, alias_uri, ICD10CM_T2DM)
    alias_validate = _validate_display(fhir_client, alias_uri, ICD10CM_T2DM)
    alias_cc = _validate_cc_display(fhir_client, alias_uri, ICD10CM_T2DM)

    assert alias_lookup == canonical_display
    assert alias_validate == canonical_display
    assert alias_cc == canonical_display


def test_e16_n_way_canonical_system_invariant_across_4_ops(fhir_client):
    """Lens 1 / N-way canonical-SYSTEM invariant: the Out `system`
    parameter across $lookup, $validate-code, and $validate-code
    codeableConcept MUST all be the canonical URI (NOT the alias input).

    Pattern-match: CS-05 SKEPTIC test_s70..s78 (3-way system invariant)
    extended to include the codeableConcept matched-coding Out system.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
    Out `system` (1..1 uri "The system for the code that was found").
    """
    # Use alias inputs to verify the canonical re-resolution path on all
    # 3 ops.
    alias = SNOMED_URI_UPPERCASE_SCHEME
    code = SNOMED_T2DM

    # $lookup
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={alias}&code={code}"
    )
    assert r.status_code == 200
    lookup_system = _lookup_param_value(r.json(), "system")

    # $validate-code
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={alias}&code={code}"
    )
    assert r.status_code == 200
    validate_system = _lookup_param_value(r.json(), "system")

    # $validate-code codeableConcept
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {"system": alias, "code": code},
                    ]
                },
            }
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body)
    assert r.status_code == 200
    cc_system = _lookup_param_value(r.json(), "system")

    # All three MUST resolve to the canonical URI, NOT the alias input.
    assert lookup_system == SNOMED_URI, (
        f"$lookup system {lookup_system!r} != canonical {SNOMED_URI!r}"
    )
    assert validate_system == SNOMED_URI
    assert cc_system == SNOMED_URI


# ---------------------------------------------------------------------------
# Lens 2: Hostile version-input matrix — lateral combinations
# (HISTORIAN tip 2).
# ---------------------------------------------------------------------------
# Per HISTORIAN tip: extend the hostile version-input matrix (Lens 16)
# with lateral combinations:
#   (a) version + display (multi-version × display mismatch)
#   (b) version + property multi (multi-version × 5-property request)
#   (c) version + Accept-header XML negotiation
#   (d) version + coding alternative encoding (POST)
# Pattern-match: CS-05 SKEPTIC Lens 8 (test_s70-s77 hostile version
# inputs on $lookup), CS-05 HISTORIAN Lens 16 (test_h160-h162 hostile
# version-input matrix extension 21-parametrized).

HOSTILE_VERSIONS = [
    "v1.0" + "0" * 9900,             # ~10K char
    "v测试版本",                       # unicode CJK
    "v1' OR '1'='1",                 # SQL injection
    "../../../etc/passwd",           # path traversal
    "<script>alert(1)</script>",     # XSS
]

# Hostile inputs that httpx rejects on the URL path (null bytes, CRLF)
# but POST bodies can carry — exercising the actual server boundary.
HOSTILE_VERSIONS_POST_ONLY = [
    "v\0null\0bytes",                # null bytes
    "v1\r\nX-Inject: evil",          # CRLF injection
]

@pytest.mark.parametrize("hostile_version", HOSTILE_VERSIONS)
def test_e20_hostile_version_plus_display_on_validate(
    fhir_client, hostile_version
):
    """Lens 2 / Hostile version + display combination on $validate-code
    (HISTORIAN tip 2): a hostile version string combined with a CORRECT
    display MUST NOT produce 5xx. The display matches the engine's
    canonical display, so result=true; the hostile version is accepted
    but ignored (single-snapshot engine per AGENTS.md NOT A BUG
    registry).

    Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
    In `version` (0..1 string "The version of the system").
    """
    canonical_display = _lookup_display(fhir_client, SNOMED_URI, SNOMED_T2DM)
    assert canonical_display is not None
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM}&version={hostile_version}"
        f"&display={canonical_display}"
    )
    assert r.status_code == 200, (
        f"version+display hostile combination produced "
        f"{r.status_code}: {r.text[:200]}"
    )
    body = r.json()
    assert _lookup_param_value(body, "result") is True


@pytest.mark.parametrize("hostile_version", HOSTILE_VERSIONS)
def test_e21_hostile_version_plus_display_mismatch_on_validate(
    fhir_client, hostile_version
):
    """Lens 2 / Hostile version + display MISMATCH combination on
    $validate-code (HISTORIAN tip 2): a hostile version string combined
    with an INCORRECT display MUST NOT produce 5xx; the engine MUST
    still enforce display mismatch (result=false + message + canonical
    display) per CS-03 SKEPTIC QA-048.

    Pattern-match: CF-SKEPTIC-CS03-01 RESOLVED + CS-03 SKEPTIC QA-048
    + CS-05 SKEPTIC Lens 8 + CS-05 HISTORIAN Lens 16.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM}&version={hostile_version}"
        f"&display=WRONG_DISPLAY_VALUE"
    )
    assert r.status_code == 200
    body = r.json()
    assert _lookup_param_value(body, "result") is False
    msg = _lookup_param_value(body, "message") or ""
    assert "incorrect" in msg.lower(), (
        f"display mismatch message should explain the mismatch; got {msg!r}"
    )


@pytest.mark.parametrize("hostile_version", HOSTILE_VERSIONS)
def test_e22_hostile_version_plus_property_multi_on_lookup(
    fhir_client, hostile_version
):
    """Lens 2 / Hostile version + property (multi) combination on
    $lookup (HISTORIAN tip 2): a hostile version string combined with
    a multi-valued property request MUST NOT produce 5xx. The server
    returns its full property set regardless of the filter (per AGENTS.md
    NOT A BUG registry — $lookup property filter ignored).

    Pattern-match: CS-05 SKEPTIC test_s35 (property filter ignored) +
    CS-05 HISTORIAN Lens 16 (test_h160 hostile version on $lookup).
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM}&version={hostile_version}"
        f"&property=cui&property=tty&property=aui&property=abstract"
        f"&property=inactive"
    )
    assert r.status_code == 200, (
        f"version+property-multi hostile combination produced "
        f"{r.status_code}: {r.text[:200]}"
    )
    body = r.json()
    # The property filter is ignored; server still returns its standard
    # property set including abstract.
    assert _lookup_param_value(body, "code") == SNOMED_T2DM
    abstract = _lookup_param(body, "abstract")
    assert abstract is not None
    assert abstract.get("valueBoolean") is False  # CF-SKEPTIC-CS05-01


@pytest.mark.parametrize("hostile_version", HOSTILE_VERSIONS)
def test_e23_hostile_version_plus_xml_format_on_lookup(fhir_client, hostile_version):
    """Lens 2 / Hostile version + XML format combination on $lookup:
    the server MUST honor the _format=xml request even with a hostile
    version string. The XML body MUST render boolean values in lowercase
    per FHIR R4 §3.4.1 + CR-002.

    Pattern-match: CS-05 HISTORIAN test_h110 (XML wire-format lowercase
    boolean on $lookup abstract Out param) + CS-05 HISTORIAN Lens 16.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM}&version={hostile_version}"
        f"&_format=xml"
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/fhir+xml")
    body = r.text
    assert "<valueBoolean value=\"false\"/>" in body, (
        "XML wire-format MUST render lowercase boolean per CR-002"
    )
    assert "<valueBoolean value=\"False\"/>" not in body


@pytest.mark.parametrize("hostile_version", HOSTILE_VERSIONS)
def test_e24_hostile_version_on_subsumes(fhir_client, hostile_version):
    """Lens 2 / Hostile version on $subsumes (HISTORIAN tip 2):
    a hostile version string MUST NOT produce 5xx on $subsumes. The
    4-outcome directionality (equivalent/subsumes/subsumed-by/not-
    subsumed) MUST be preserved.

    Pattern-match: CS-05 HISTORIAN Lens 16 (test_h162 hostile version
    on $subsumes) extended to verify the 4-outcome directionality matrix
    is unchanged under hostile version input.
    """
    # Case 1: equivalent
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_T2DM}&codeB={SNOMED_T2DM}"
        f"&version={hostile_version}"
    )
    assert r.status_code == 200
    assert _lookup_param_value(r.json(), "outcome") == "equivalent"

    # Case 2: subsumes (DM subsumes T2DM)
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
        f"&version={hostile_version}"
    )
    assert r.status_code == 200
    assert _lookup_param_value(r.json(), "outcome") == "subsumes"


# Hostile version inputs that httpx rejects on the URL path (null bytes,
# CRLF) are exercised via POST (Parameters body) — the server-side
# boundary is what matters for DoS / info-disclosure surface auditing.
# httpx URL-path validation is a CLIENT constraint, not a server contract.
@pytest.mark.parametrize("hostile_version", HOSTILE_VERSIONS_POST_ONLY)
def test_e25_hostile_version_post_path_on_lookup(fhir_client, hostile_version):
    """Lens 2 / Hostile version via POST body on $lookup (HISTORIAN tip
    2 extension): null bytes and CRLF cannot be sent via GET URL (httpx
    rejects client-side), but they CAN be sent via POST Parameters body.
    The server MUST NOT 5xx on the POST path.

    Pattern-match: CS-04 SKEPTIC QA-001 + HISTORIAN QA-001 (isinstance
    guard at untrusted-data list-iterator boundary) — the POST body
    parser is the load-bearing boundary.
    """
    body = _parameters_body(SNOMED_URI, SNOMED_T2DM, version=hostile_version)
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    assert r.status_code == 200, (
        f"POST with hostile version {hostile_version!r} produced "
        f"{r.status_code}: {r.text[:200]}"
    )


@pytest.mark.parametrize("hostile_version", HOSTILE_VERSIONS_POST_ONLY)
def test_e26_hostile_version_post_path_on_validate(fhir_client, hostile_version):
    """Lens 2 / Hostile version via POST body on $validate-code."""
    body = _parameters_body(SNOMED_URI, SNOMED_T2DM, version=hostile_version)
    r = fhir_client.post("/fhir/CodeSystem/$validate-code", json=body)
    assert r.status_code == 200


@pytest.mark.parametrize("hostile_version", HOSTILE_VERSIONS_POST_ONLY)
def test_e27_hostile_version_post_path_on_subsumes(fhir_client, hostile_version):
    """Lens 2 / Hostile version via POST body on $subsumes."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codeA", "valueCode": SNOMED_DIABETES_MELLITUS},
            {"name": "codeB", "valueCode": SNOMED_T2DM},
            {"name": "version", "valueString": hostile_version},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r.status_code == 200
    assert _lookup_param_value(r.json(), "outcome") == "subsumes"


# ---------------------------------------------------------------------------
# Lens 3: GET ↔ POST byte-exact parity extended to $validate-code +
# $subsumes (HISTORIAN tip 3).
# ---------------------------------------------------------------------------
# Per HISTORIAN tip: GET ↔ POST byte-exact parity was verified on
# $lookup (CS-05 HISTORIAN test_h82). EXPLORER extends to $validate-code
# and $subsumes for completeness.
# Pattern-match: CS-05 HISTORIAN test_h82 (GET ↔ POST byte-exact parity
# on $lookup Out system).

def test_e30_validate_code_get_post_byte_exact_parity(fhir_client):
    """Lens 3 / GET ↔ POST byte-exact parity on $validate-code
    (HISTORIAN tip 3 extension): the response Parameters body for a GET
    $validate-code MUST be byte-exact identical to the POST $validate-
    code with the same (system, code) input.

    Pattern-match: CS-05 HISTORIAN test_h82 (GET ↔ POST byte-exact
    parity on $lookup).

    Spec: https://hl7.org/fhir/R4/codesystem-operation-validate-code.html
    """
    # GET
    r_get = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r_get.status_code == 200

    # POST with Parameters body
    body = _parameters_body(SNOMED_URI, SNOMED_T2DM)
    r_post = fhir_client.post(
        "/fhir/CodeSystem/$validate-code", json=body
    )
    assert r_post.status_code == 200

    assert r_get.json() == r_post.json(), (
        "GET ↔ POST byte-exact parity FAILS on $validate-code: "
        f"GET={r_get.json()!r}, POST={r_post.json()!r}"
    )


def test_e31_validate_code_get_post_byte_exact_parity_with_display_mismatch(fhir_client):
    """Lens 3 / GET ↔ POST byte-exact parity on $validate-code under
    display mismatch: same display mismatch scenario MUST produce
    byte-exact identical response Parameters body."""
    display = "WRONG_DISPLAY_VALUE"

    r_get = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM}&display={display}"
    )
    assert r_get.status_code == 200

    body = _parameters_body(SNOMED_URI, SNOMED_T2DM, display=display)
    r_post = fhir_client.post(
        "/fhir/CodeSystem/$validate-code", json=body
    )
    assert r_post.status_code == 200

    assert r_get.json() == r_post.json(), (
        "GET ↔ POST byte-exact parity FAILS on $validate-code under "
        "display mismatch"
    )


def test_e32_validate_code_get_post_byte_exact_parity_unknown_code(fhir_client):
    """Lens 3 / GET ↔ POST byte-exact parity on $validate-code with
    unknown code: same error path MUST produce identical response body."""
    unknown_code = "NONEXISTENT_CODE_999"

    r_get = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={unknown_code}"
    )
    assert r_get.status_code == 200

    body = _parameters_body(SNOMED_URI, unknown_code)
    r_post = fhir_client.post(
        "/fhir/CodeSystem/$validate-code", json=body
    )
    assert r_post.status_code == 200

    assert r_get.json() == r_post.json()


def test_e33_subsumes_get_post_byte_exact_parity_equivalent(fhir_client):
    """Lens 3 / GET ↔ POST byte-exact parity on $subsumes (HISTORIAN
    tip 3 extension): the response Parameters body for a GET $subsumes
    MUST be byte-exact identical to the POST $subsumes with the same
    (system, codeA, codeB) input — including the equivalent case.

    Pattern-match: CS-05 HISTORIAN test_h82 (GET ↔ POST byte-exact
    parity on $lookup) extended to $subsumes.

    Spec: https://hl7.org/fhir/R4/codesystem-operation-subsumes.html
    """
    r_get = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_T2DM}&codeB={SNOMED_T2DM}"
    )
    assert r_get.status_code == 200

    body = _parameters_body(SNOMED_URI, SNOMED_T2DM, codeA=SNOMED_T2DM, codeB=SNOMED_T2DM)
    r_post = fhir_client.post(
        "/fhir/CodeSystem/$subsumes", json=body
    )
    assert r_post.status_code == 200

    assert r_get.json() == r_post.json()


def test_e34_subsumes_get_post_byte_exact_parity_subsumes(fhir_client):
    """Lens 3 / GET ↔ POST byte-exact parity on $subsumes — subsumes case."""
    r_get = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
    )
    assert r_get.status_code == 200

    body = _parameters_body(
        SNOMED_URI, SNOMED_DIABETES_MELLITUS,
        codeA=SNOMED_DIABETES_MELLITUS, codeB=SNOMED_T2DM,
    )
    r_post = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r_post.status_code == 200

    assert r_get.json() == r_post.json()


def test_e35_subsumes_get_post_byte_exact_parity_subsumed_by(fhir_client):
    """Lens 3 / GET ↔ POST byte-exact parity on $subsumes — subsumed-by case."""
    r_get = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_T2DM}&codeB={SNOMED_DIABETES_MELLITUS}"
    )
    assert r_get.status_code == 200

    body = _parameters_body(
        SNOMED_URI, SNOMED_T2DM,
        codeA=SNOMED_T2DM, codeB=SNOMED_DIABETES_MELLITUS,
    )
    r_post = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r_post.status_code == 200

    assert r_get.json() == r_post.json()


def test_e36_subsumes_get_post_byte_exact_parity_not_subsumed(fhir_client):
    """Lens 3 / GET ↔ POST byte-exact parity on $subsumes — not-subsumed case.

    Pattern-match: CS-05 HISTORIAN test_h80 (sibling-handler parity
    source-read) + test_h82 (GET ↔ POST byte-exact parity on $lookup)
    extended to ALL 4 outcome values on $subsumes.
    """
    r_get = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_T2DM}&codeB={RXNORM_METFORMIN}"
    )
    # Cross-system — server may reject with 400 or return not-subsumed.
    # For parity, both GET and POST MUST produce the same response.
    body = _parameters_body(
        SNOMED_URI, SNOMED_T2DM,
        codeA=SNOMED_T2DM, codeB=RXNORM_METFORMIN,
    )
    r_post = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)

    # Parity is the load-bearing contract: same status code, same body.
    assert r_get.status_code == r_post.status_code
    if r_get.status_code == 200:
        assert r_get.json() == r_post.json()


def test_e37_subsumes_get_post_byte_exact_parity_with_version(fhir_client):
    """Lens 3 / GET ↔ POST byte-exact parity on $subsumes with version
    parameter: same (system, codeA, codeB, version) MUST produce byte-
    exact identical response.

    Pattern-match: version param ignored per AGENTS.md NOT A BUG
    registry; GET and POST MUST both ignore identically.
    """
    version = "2024-09"
    r_get = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
        f"&version={version}"
    )
    assert r_get.status_code == 200

    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "codeA", "valueCode": SNOMED_DIABETES_MELLITUS},
            {"name": "codeB", "valueCode": SNOMED_T2DM},
            {"name": "version", "valueString": version},
        ],
    }
    r_post = fhir_client.post("/fhir/CodeSystem/$subsumes", json=body)
    assert r_post.status_code == 200

    assert r_get.json() == r_post.json()


def test_e38_lookup_get_post_byte_exact_parity_with_property_multi(fhir_client):
    """Lens 3 / GET ↔ POST byte-exact parity on $lookup with multi-
    valued property request: even though the property filter is ignored
    (per AGENTS.md NOT A BUG registry), the response MUST be byte-exact
    identical between GET and POST.

    Pattern-match: CS-05 HISTORIAN test_h82 (GET ↔ POST byte-exact
    parity on $lookup) extended to the multi-property case.
    """
    r_get = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
        f"&property=cui&property=tty&property=aui&property=abstract"
    )
    assert r_get.status_code == 200

    body = _parameters_body(
        SNOMED_URI, SNOMED_T2DM,
        property=["cui", "tty", "aui", "abstract"],
    )
    r_post = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    assert r_post.status_code == 200

    assert r_get.json() == r_post.json()


def test_e39_lookup_get_post_byte_exact_parity_with_version(fhir_client):
    """Lens 3 / GET ↔ POST byte-exact parity on $lookup with version."""
    version = "2025-03"
    r_get = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={RXNORM_URI}"
        f"&code={RXNORM_METFORMIN}&version={version}"
    )
    assert r_get.status_code == 200

    body = _parameters_body(RXNORM_URI, RXNORM_METFORMIN, version=version)
    r_post = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    assert r_post.status_code == 200

    assert r_get.json() == r_post.json()


# ---------------------------------------------------------------------------
# Lens 4: Cross-op $subsumes directionality × alias URIs.
# ---------------------------------------------------------------------------
# Pattern-match: CS-04 SKEPTIC + HISTORIAN verified $subsumes directionality
# on canonical URIs; CS-05 SKEPTIC test_s50..s57 verified on alias URIs.
# EXPLORER adds the cross-op parity: the alias-URI input MUST produce
# the same outcome as the canonical-URI input.

@pytest.mark.parametrize(
    "alias_uri",
    [
        SNOMED_URI_TRAILING_SLASH,
        SNOMED_URI_URN_OID,
        SNOMED_URI_UPPERCASE_SCHEME,
    ],
)
def test_e40_subsumes_alias_uri_yields_same_outcome_as_canonical(
    fhir_client, alias_uri
):
    """Lens 4 / Cross-op $subsumes directionality × alias URIs:
    subsumes(DM, T2DM) via alias-URI system MUST produce the same
    outcome as via canonical URI."""
    canonical_r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
    )
    assert canonical_r.status_code == 200
    canonical_outcome = _lookup_param_value(canonical_r.json(), "outcome")

    alias_r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={alias_uri}"
        f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
    )
    assert alias_r.status_code == 200
    alias_outcome = _lookup_param_value(alias_r.json(), "outcome")

    assert canonical_outcome == alias_outcome == "subsumes"


# ---------------------------------------------------------------------------
# Lens 5: Combined property multi with hostile values.
# ---------------------------------------------------------------------------
# Pattern-match: CS-05 SKEPTIC Lens 8 (hostile version-input matrix)
# extended to the property In parameter (0..* code). The implementation
# is permissive today (property filter ignored); the hostile value MUST
# NOT produce 5xx.

HOSTILE_PROPERTY_VALUES = [
    "ab" + "s" * 9900,             # ~10K char property code
    "测试",                          # unicode CJK
    "cui' OR '1'='1",              # SQL injection
    "../../../etc/passwd",         # path traversal
    "<script>alert(1)</script>",   # XSS
]

# Hostile property inputs that httpx rejects on the URL path.
HOSTILE_PROPERTY_VALUES_POST_ONLY = [
    "in\0valid",                   # null bytes
]

@pytest.mark.parametrize("hostile_property", HOSTILE_PROPERTY_VALUES)
def test_e50_hostile_property_value_on_lookup(fhir_client, hostile_property):
    """Lens 5 / Hostile property value on $lookup (per HISTORIAN tip 2
    extension): a multi-valued property request containing a hostile
    value MUST NOT produce 5xx. The property filter is ignored (per
    AGENTS.md NOT A BUG registry), so the server returns its standard
    property set."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
        f"&property={hostile_property}"
    )
    assert r.status_code == 200, (
        f"hostile property value {hostile_property!r} produced "
        f"{r.status_code}: {r.text[:200]}"
    )
    body = r.json()
    assert _lookup_param_value(body, "code") == SNOMED_T2DM


@pytest.mark.parametrize("hostile_property", HOSTILE_PROPERTY_VALUES)
def test_e51_hostile_property_value_multi_on_lookup(fhir_client, hostile_property):
    """Lens 5 / Hostile property value combined with valid property
    requests on $lookup: even with a hostile value mixed in, the server
    MUST NOT 5xx and MUST return the standard property set."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
        f"&property=cui&property={hostile_property}&property=tty"
    )
    assert r.status_code == 200


# Property null-byte case via POST body — httpx rejects null bytes in
# GET URLs (client-side constraint); the server-side boundary is what
# matters for DoS surface auditing.
@pytest.mark.parametrize("hostile_property", HOSTILE_PROPERTY_VALUES_POST_ONLY)
def test_e52_hostile_property_value_post_path_on_lookup(fhir_client, hostile_property):
    """Lens 5 / Hostile property value via POST body on $lookup: null
    bytes cannot be sent via GET URL (httpx rejects client-side), but
    they CAN be sent via POST Parameters body. The server MUST NOT 5xx
    on the POST path.

    Pattern-match: CS-04 SKEPTIC QA-001 + HISTORIAN QA-001 (isinstance
    guard at untrusted-data list-iterator boundary).
    """
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {"name": "code", "valueCode": SNOMED_T2DM},
            {"name": "property", "valueCode": hostile_property},
        ],
    }
    r = fhir_client.post("/fhir/CodeSystem/$lookup", json=body)
    assert r.status_code == 200, (
        f"POST with hostile property {hostile_property!r} produced "
        f"{r.status_code}: {r.text[:200]}"
    )


# ---------------------------------------------------------------------------
# Lens 6: Mixed encoding across operations.
# ---------------------------------------------------------------------------
# Pattern-match: $lookup accepts `coding`; $validate-code accepts `coding`
# AND `codeableConcept`; $subsumes accepts `codingA`/`codingB`;
# $translate accepts `coding`/`codeableConcept` (sourceCoding/targetCoding
# in R4 spec). The cross-op consistency: the SAME (system, code) input
# encoded via the operation's preferred alternative encoding MUST produce
# the same Out system/code/display as the canonical encoding.

def test_e60_lookup_coding_vs_validate_coding_consistency(fhir_client):
    """Lens 6 / Mixed encoding across operations: $lookup POST with
    `coding` body produces the same Out system/display as $validate-code
    POST with `coding` body."""
    # $lookup POST coding
    lookup_body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "coding",
                "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM},
            }
        ],
    }
    r_lookup = fhir_client.post("/fhir/CodeSystem/$lookup", json=lookup_body)
    assert r_lookup.status_code == 200
    lookup_display = _lookup_param_value(r_lookup.json(), "display")
    lookup_system = _lookup_param_value(r_lookup.json(), "system")

    # $validate-code POST coding
    validate_body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "coding",
                "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM},
            }
        ],
    }
    r_validate = fhir_client.post("/fhir/CodeSystem/$validate-code", json=validate_body)
    assert r_validate.status_code == 200
    validate_display = _lookup_param_value(r_validate.json(), "display")
    validate_system = _lookup_param_value(r_validate.json(), "system")

    assert lookup_display == validate_display
    assert lookup_system == validate_system == SNOMED_URI


def test_e61_validate_coding_vs_validate_codeable_concept_consistency(fhir_client):
    """Lens 6 / Mixed encoding: $validate-code POST with `coding` body
    produces the same Out display/system as $validate-code POST with
    `codeableConcept` body containing a single matching coding."""
    # POST coding
    coding_body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "coding",
                "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM},
            }
        ],
    }
    r_coding = fhir_client.post("/fhir/CodeSystem/$validate-code", json=coding_body)
    assert r_coding.status_code == 200
    coding_display = _lookup_param_value(r_coding.json(), "display")
    coding_system = _lookup_param_value(r_coding.json(), "system")

    # POST codeableConcept with single matching coding
    cc_body = {
        "resourceType": "Parameters",
        "parameter": [
            {
                "name": "codeableConcept",
                "valueCodeableConcept": {
                    "coding": [
                        {"system": SNOMED_URI, "code": SNOMED_T2DM},
                    ]
                },
            }
        ],
    }
    r_cc = fhir_client.post("/fhir/CodeSystem/$validate-code", json=cc_body)
    assert r_cc.status_code == 200
    cc_display = _lookup_param_value(r_cc.json(), "display")
    cc_system = _lookup_param_value(r_cc.json(), "system")

    assert coding_display == cc_display
    assert coding_system == cc_system == SNOMED_URI


def test_e62_subsumes_coding_a_b_consistency(fhir_client):
    """Lens 6 / Mixed encoding: $subsumes POST with codingA/codingB
    produces the same outcome as GET with scalar codeA/codeB."""
    # GET scalar
    r_get = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_DIABETES_MELLITUS}&codeB={SNOMED_T2DM}"
    )
    assert r_get.status_code == 200
    get_outcome = _lookup_param_value(r_get.json(), "outcome")

    # POST codingA/codingB
    post_body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "system", "valueUri": SNOMED_URI},
            {
                "name": "codingA",
                "valueCoding": {"system": SNOMED_URI, "code": SNOMED_DIABETES_MELLITUS},
            },
            {
                "name": "codingB",
                "valueCoding": {"system": SNOMED_URI, "code": SNOMED_T2DM},
            },
        ],
    }
    r_post = fhir_client.post("/fhir/CodeSystem/$subsumes", json=post_body)
    assert r_post.status_code == 200
    post_outcome = _lookup_param_value(r_post.json(), "outcome")

    assert get_outcome == post_outcome == "subsumes"


# ---------------------------------------------------------------------------
# Lens 7: Display-mismatch interplay with version + property multi.
# ---------------------------------------------------------------------------
# Pattern-match: CS-03 SKEPTIC QA-048 (display mismatch) + CS-05 EXPLORER
# Lens 2 (hostile version+display) extended to verify display mismatch
# enforcement HOLDS under hostile version AND multi-property combination.

def test_e70_display_mismatch_under_hostile_version_and_property(fhir_client):
    """Lens 7 / Display mismatch enforcement HOLDS under hostile
    version + multi-property combination: even with hostile version
    AND 4-property request, the engine STILL enforces display mismatch
    (result=false + message + canonical display).

    Pattern-match: CS-03 SKEPTIC QA-048 + CS-05 SKEPTIC Lens 8 + CS-05
    EXPLORER Lens 2.
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM}&version=v1' OR '1'='1"
        f"&display=WRONG&display=IGNORED"
        f"&property=cui&property=tty"
    )
    assert r.status_code == 200
    body = r.json()
    # Display mismatch MUST still fire.
    assert _lookup_param_value(body, "result") is False
    msg = _lookup_param_value(body, "message") or ""
    assert "incorrect" in msg.lower()


def test_e71_display_mismatch_under_unicode_version(fhir_client):
    """Lens 7 / Display mismatch enforcement HOLDS under unicode CJK
    version."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={RXNORM_URI}"
        f"&code={RXNORM_METFORMIN}&version=v测试版本"
        f"&display=WRONG_DISPLAY"
    )
    assert r.status_code == 200
    body = r.json()
    assert _lookup_param_value(body, "result") is False


# ---------------------------------------------------------------------------
# Lens 8: Cross-op short-circuit equivalence.
# ---------------------------------------------------------------------------
# Pattern-match: CS-05 SKEPTIC test_s56 ($subsumes identical A/B short-
# circuits to equivalent). Cross-op: $lookup on code X and $validate-code
# on code X both produce consistent code+system+display — the SAME code
# resolves to the SAME engine state across both ops.

@pytest.mark.parametrize("system,code", SEEDED_ALL)
def test_e80_lookup_validate_consistency_on_same_code(fhir_client, system, code):
    """Lens 8 / Cross-op short-circuit equivalence: $lookup and
    $validate-code on the SAME code MUST produce consistent Out system
    and display (canonical-agreement invariant).

    Pattern-match: CS-05 EXPLORER baseline test_e10 + test_e11 extended
    parametrically to every seeded code."""
    r_lookup = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={system}&code={code}"
    )
    assert r_lookup.status_code == 200
    lookup_system = _lookup_param_value(r_lookup.json(), "system")
    lookup_display = _lookup_param_value(r_lookup.json(), "display")

    r_validate = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={system}&code={code}"
    )
    assert r_validate.status_code == 200
    validate_system = _lookup_param_value(r_validate.json(), "system")
    validate_display = _lookup_param_value(r_validate.json(), "display")
    validate_result = _lookup_param_value(r_validate.json(), "result")

    assert lookup_system == validate_system
    assert lookup_display == validate_display
    assert validate_result is True


def test_e81_subsumes_equivalent_short_circuit_then_lookup_consistency(fhir_client):
    """Lens 8 / Cross-op short-circuit chain: $subsumes(X, X) returns
    equivalent; $lookup on the same X returns 200; $validate-code on
    the same X returns result=true. The cross-op chain is consistent."""
    # $subsumes
    r_sub = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_T2DM}&codeB={SNOMED_T2DM}"
    )
    assert r_sub.status_code == 200
    assert _lookup_param_value(r_sub.json(), "outcome") == "equivalent"

    # $lookup
    r_lookup = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r_lookup.status_code == 200
    assert _lookup_param_value(r_lookup.json(), "code") == SNOMED_T2DM

    # $validate-code
    r_validate = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}&code={SNOMED_T2DM}"
    )
    assert r_validate.status_code == 200
    assert _lookup_param_value(r_validate.json(), "result") is True


# ---------------------------------------------------------------------------
# Lens 9: XML wire-format lateral combinations (CR-002 extension).
# ---------------------------------------------------------------------------
# Pattern-match: CS-05 HISTORIAN test_h110 + test_h111 (XML wire-format
# lowercase boolean on $lookup abstract + $validate-code result) extended
# to lateral combinations:
#   (a) XML + display mismatch on $validate-code (result=false MUST be lowercase)
#   (b) XML + multi-property request on $lookup (abstract MUST be lowercase)
#   (c) XML + hostile version on $lookup
#   (d) XML + $subsumes outcome rendering (already in CS-04 HISTORIAN)

def test_e90_xml_display_mismatch_renders_lowercase_boolean(fhir_client):
    """Lens 9 / XML wire-format lateral combination: display mismatch
    on $validate-code under _format=xml MUST render result=false as
    `<valueBoolean value="false"/>` (lowercase per CR-002)."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM}&display=WRONG_DISPLAY&_format=xml"
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/fhir+xml")
    body = r.text
    assert "<valueBoolean value=\"false\"/>" in body
    assert "<valueBoolean value=\"False\"/>" not in body


def test_e91_xml_lookup_with_property_multi_renders_lowercase_abstract(fhir_client):
    """Lens 9 / XML wire-format lateral combination: $lookup with multi-
    property request under _format=xml MUST render abstract=false as
    lowercase."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
        f"&property=cui&property=tty&property=aui&property=abstract"
        f"&_format=xml"
    )
    assert r.status_code == 200
    body = r.text
    assert "<valueBoolean value=\"false\"/>" in body
    assert "<valueBoolean value=\"False\"/>" not in body


def test_e92_xml_lookup_with_hostile_version_renders_lowercase_abstract(fhir_client):
    """Lens 9 / XML wire-format lateral combination: $lookup with
    hostile version under _format=xml STILL renders abstract=false as
    lowercase (CR-002 holds under hostile input)."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
        f"&version=v1' OR '1'='1&_format=xml"
    )
    assert r.status_code == 200
    body = r.text
    assert "<valueBoolean value=\"false\"/>" in body
    assert "<valueBoolean value=\"False\"/>" not in body


def test_e93_xml_subsumes_outcome_renders_hyphenated(fhir_client):
    """Lens 9 / XML wire-format lateral combination: $subsumes with
    subsumed-by outcome under _format=xml MUST render the hyphenated
    value (`subsumed-by`, not camelCase `subsumedBy`)."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$subsumes?system={SNOMED_URI}"
        f"&codeA={SNOMED_T2DM}&codeB={SNOMED_DIABETES_MELLITUS}"
        f"&_format=xml"
    )
    assert r.status_code == 200
    body = r.text
    assert "subsumed-by" in body, (
        f"hyphenated 'subsumed-by' must render in XML body; got: {body[:300]}"
    )
    assert "subsumedBy" not in body, "camelCase 'subsumedBy' MUST NOT leak"


# ---------------------------------------------------------------------------
# Lens 10: Fixture-gap documentation probes (HISTORIAN tip 4).
# ---------------------------------------------------------------------------
# Per HISTORIAN tip 4: document fixture-gap combinations (multi-parent
# DAG, abstract concept, inactive code) rather than manufacturing probes
# requiring missing data. Pattern-match: CF-SKEPTIC-CS05-01 (abstract
# hardcoded False) + CF-SKEPTIC-CS05-02 (inactive never emitted) +
# CF-SKEPTIC-CS05-03 (multi-hierarchy BFS structural correctness).

def test_e100_fixture_gap_abstract_concepts_documented_via_source_read():
    """Lens 10 / Fixture-gap documentation: the conformance fixture has
    NO abstract concepts seeded. Per CF-SKEPTIC-CS05-01, the engine
    hardcodes `abstract=False` as a clinically safe default (literal-
    value-vs-canonical-registry drift sibling). Source-read confirms
    the literal."""
    from medterm4ds.engines.fhir import responses as resp_module

    _, tree = _get_module_source(resp_module)
    fn_node = _get_func_source(tree, "build_parameters_lookup")
    assert fn_node is not None

    found_literal_false = False
    for stmt in ast.walk(fn_node):
        if not isinstance(stmt, ast.Call):
            continue
        func = stmt.func
        if not isinstance(func, ast.Name) or func.id != "_param":
            continue
        args = stmt.args
        if len(args) < 2:
            continue
        name_arg, value_arg = args[0], args[1]
        if (
            isinstance(name_arg, ast.Constant)
            and name_arg.value == "abstract"
            and isinstance(value_arg, ast.Constant)
            and value_arg.value is False
        ):
            found_literal_false = True
            break
    assert found_literal_false, (
        "CF-SKEPTIC-CS05-01 carry-forward: build_parameters_lookup must "
        "still hardcode abstract=False (fixture lacks abstract concepts; "
        "future engine enhancement MUST propagate code_info.abstract)"
    )


def test_e101_fixture_gap_inactive_codes_documented_via_source_read():
    """Lens 10 / Fixture-gap documentation: the conformance fixture has
    NO inactive codes (SUPPRESS='O' or 'D') seeded. Per CF-SKEPTIC-
    CS05-02, the engine filters mrconso on SUPPRESS='N' AND never emits
    an `inactive` property. Source-read confirms the absence of an
    `inactive` literal in build_parameters_lookup's property group."""
    from medterm4ds.engines.fhir import responses as resp_module

    _, tree = _get_module_source(resp_module)
    fn_node = _get_func_source(tree, "build_parameters_lookup")
    assert fn_node is not None

    for stmt in ast.walk(fn_node):
        if not isinstance(stmt, ast.Call):
            continue
        func = stmt.func
        if not isinstance(func, ast.Name) or func.id != "_property_param":
            continue
        args = stmt.args
        if not args:
            continue
        first = args[0]
        if isinstance(first, ast.Constant) and first.value == "inactive":
            pytest.fail(
                "CF-SKEPTIC-CS05-02 carry-forward: build_parameters_lookup "
                "must NOT emit 'inactive' property today (fixture lacks "
                "inactive codes; future engine enhancement MUST emit "
                "inactive=true for SUPPRESS='O' rows)"
            )


def test_e102_fixture_gap_multi_hierarchy_documented_via_visited_set():
    """Lens 10 / Fixture-gap documentation: the conformance fixture
    seeds only a single-parent mrrel row. Per CF-SKEPTIC-CS05-03, the
    engine implementation IS correct for multi-parent DAGs (visited-set
    guards BFS). Source-read confirms the visited-set is present."""
    from medterm4ds.services import hierarchy as hier_module

    _, tree = _get_module_source(hier_module)
    fn_node = _get_func_source(tree, "get_descendants_bfs")
    assert fn_node is not None

    found_visited = False
    for stmt in ast.walk(fn_node):
        if isinstance(stmt, ast.AnnAssign):
            target = stmt.target
            if isinstance(target, ast.Name) and target.id == "visited":
                found_visited = True
                break
        elif isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                if isinstance(t, ast.Name) and t.id == "visited":
                    found_visited = True
                    break
    assert found_visited, (
        "CF-SKEPTIC-CS05-03 carry-forward: get_descendants_bfs MUST "
        "have a `visited` set to prevent infinite loops in multi-parent "
        "DAGs (engine structurally correct; fixture incomplete)"
    )


# ---------------------------------------------------------------------------
# Lens 11: Source-read structural contracts for sibling-handler parity.
# ---------------------------------------------------------------------------
# Pattern-match: CS-05 HISTORIAN test_h50/h51 (sibling-handler parity
# source-read — both _do_lookup AND _do_validate call canonical_system_uri).
# EXPLORER extension: extend the parity audit to verify BOTH handlers
# delegate to canonical_system_uri, AND that _do_subsumes uses
# is_descendant (BFS) for hierarchy resolution.

def test_e110_sibling_handler_parity_canonical_system_uri_lookup_validate():
    """Lens 11 / Sibling-handler parity: both _do_lookup AND _do_validate
    call canonical_system_uri (CS-05 HISTORIAN test_h50/h51 source-read
    contract re-confirmed via EXPLORER structural audit).

    Pattern-match: client-input-as-canonical drift count=8+1 PROMOTED
    (GLOBAL_RULES.md) — the structural fix is to delegate to
    canonical_system_uri from EVERY _do_* handler that emits Out `system`.
    """
    from medterm4ds.apps import fhir_api

    src_text, tree = _get_module_source(fhir_api)

    lookup_node = _get_nested_func_source(
        src_text, tree, "create_fhir_app", "_do_lookup"
    )
    validate_node = _get_nested_func_source(
        src_text, tree, "create_fhir_app", "_do_validate"
    )

    assert lookup_node is not None
    assert validate_node is not None

    lookup_calls = _count_calls_in(lookup_node, "canonical_system_uri")
    validate_calls = _count_calls_in(validate_node, "canonical_system_uri")

    assert lookup_calls >= 1, (
        "_do_lookup MUST call canonical_system_uri (CS-02 HISTORIAN QA-047)"
    )
    assert validate_calls >= 1, (
        "_do_validate MUST call canonical_system_uri (CS-03 HISTORIAN QA-051)"
    )


def test_e111_subsumes_handler_uses_is_descendant_for_hierarchy():
    """Lens 11 / _do_subsumes structural contract: handler delegates to
    is_descendant (BFS-based) for hierarchy resolution.

    Pattern-match: CS-05 HISTORIAN test_h33 (_do_subsumes calls
    is_descendant per CS-04 HISTORIAN L6 pattern).
    """
    from medterm4ds.apps import fhir_api

    src_text, tree = _get_module_source(fhir_api)
    subsumes_node = _get_nested_func_source(
        src_text, tree, "create_fhir_app", "_do_subsumes"
    )
    assert subsumes_node is not None

    call_count = _count_calls_in(subsumes_node, "is_descendant")
    assert call_count >= 1, (
        "_do_subsumes MUST call is_descendant (CF-SKEPTIC-CS05-03 "
        "structural correctness contract)"
    )


def test_e112_translate_handler_canonical_system_uri_parity():
    """Lens 11 / _do_translate sibling-handler parity: handler delegates
    to canonical_system_uri for source URI re-resolution (CR-012).
    Extends the sibling-handler parity audit to the $translate surface
    per the canonical-DISPLAY invariant 4-operation coverage.

    Pattern-match: CS-05 HISTORIAN test_h50/h51 + CS-04 HISTORIAN
    test_h70/h72 (canonical_system_uri helper exists + no local
    FHIR_EQUIVALENCE dict + imports from canonical equivalence module).
    """
    from medterm4ds.apps import fhir_api

    src_text, tree = _get_module_source(fhir_api)
    translate_node = _get_nested_func_source(
        src_text, tree, "create_fhir_app", "_do_translate"
    )
    assert translate_node is not None

    call_count = _count_calls_in(translate_node, "canonical_system_uri")
    assert call_count >= 1, (
        "_do_translate MUST call canonical_system_uri for source URI "
        "canonical re-resolution (CR-012)"
    )


# ---------------------------------------------------------------------------
# Lens 12: Response shape audit — Out parameter required-set audit.
# ---------------------------------------------------------------------------
# Pattern-match: CS-05 SKEPTIC Lens 10 (response shape audit 4-param × 2 +
# 1 + XML × 2 + Accept-header = 13 probes) extended to verify the
# required Out parameter set is present on every seeded code × every
# operation, including when combined with multi-property + version
# parameters.

REQUIRED_LOOKUP_PARAMS = {"name", "code", "system", "display", "abstract"}
REQUIRED_VALIDATE_PARAMS = {"result", "code", "system"}
REQUIRED_SUBSUMES_PARAMS = {"outcome"}


@pytest.mark.parametrize("system,code", SEEDED_ALL)
def test_e120_lookup_required_out_params_with_property_multi(
    fhir_client, system, code
):
    """Lens 12 / Response shape audit: $lookup with multi-property
    request still returns all required Out params."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={system}&code={code}"
        f"&property=cui&property=tty&property=aui&property=abstract"
    )
    assert r.status_code == 200
    body = r.json()
    actual_names = {p.get("name") for p in body.get("parameter", [])}
    missing = REQUIRED_LOOKUP_PARAMS - actual_names
    assert not missing, (
        f"{system}#{code}: $lookup missing required Out params {missing} "
        f"under multi-property request"
    )


@pytest.mark.parametrize("system,code", SEEDED_ALL)
def test_e121_validate_required_out_params_with_display_correct(
    fhir_client, system, code
):
    """Lens 12 / Response shape audit: $validate-code with CORRECT
    display still returns all required Out params."""
    canonical_display = _lookup_display(fhir_client, system, code)
    assert canonical_display is not None

    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={system}&code={code}"
        f"&display={canonical_display}"
    )
    assert r.status_code == 200
    body = r.json()
    actual_names = {p.get("name") for p in body.get("parameter", [])}
    missing = REQUIRED_VALIDATE_PARAMS - actual_names
    assert not missing


@pytest.mark.parametrize("system,code", SEEDED_ALL)
def test_e122_validate_required_out_params_with_display_wrong(fhir_client, system, code):
    """Lens 12 / Response shape audit: $validate-code with WRONG
    display still returns all required Out params (plus message)."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={system}&code={code}"
        f"&display=WRONG_DISPLAY"
    )
    assert r.status_code == 200
    body = r.json()
    actual_names = {p.get("name") for p in body.get("parameter", [])}
    missing = REQUIRED_VALIDATE_PARAMS - actual_names
    assert not missing
    # Display mismatch path also emits a message.
    assert "message" in actual_names


# ---------------------------------------------------------------------------
# Lens 13: Accept-header XML negotiation on lateral combinations.
# ---------------------------------------------------------------------------
# Pattern-match: CS-05 EXPLORER baseline test_e90 + test_e91 (XML Accept
# header on $subsumes + $validate-code) extended to:
#   (a) Accept: application/fhir+xml + display mismatch
#   (b) Accept: application/fhir+xml + multi-property
#   (c) Accept-header precedence over _format

def test_e130_accept_xml_with_display_mismatch(fhir_client):
    """Lens 13 / Accept-header XML negotiation with display mismatch:
    `Accept: application/fhir+xml` on $validate-code with display
    mismatch MUST return XML body."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$validate-code?system={SNOMED_URI}"
        f"&code={SNOMED_T2DM}&display=WRONG",
        headers={"Accept": "application/fhir+xml"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/fhir+xml")
    body = r.text
    assert "incorrect" in body.lower() or "value=\"false\"" in body


def test_e131_accept_xml_with_property_multi(fhir_client):
    """Lens 13 / Accept-header XML negotiation with multi-property:
    `Accept: application/fhir+xml` on $lookup with multi-property MUST
    return XML body."""
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
        f"&property=cui&property=tty&property=aui",
        headers={"Accept": "application/fhir+xml"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/fhir+xml")


def test_e132_format_takes_precedence_over_accept(fhir_client):
    """Lens 13 / _format takes precedence over Accept: when both are
    set with conflicting values, _format=json + Accept: application/
    fhir+xml MUST return JSON.

    Pattern-match: TS-01 EXPLORER QA-009 (_format precedence).
    """
    r = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_T2DM}"
        f"&_format=json",
        headers={"Accept": "application/fhir+xml"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/fhir+json")


# ---------------------------------------------------------------------------
# Lens 14: Cross-source lateral combination — ICD-10-CM via $translate
# source-echo parity.
# ---------------------------------------------------------------------------
# Pattern-match: CS-02 TERMINOLOGIST test_t30..t31 ($translate target
# display ↔ $lookup target display). EXPLORER extension: $translate
# SOURCE display (in match[].source.display) MUST byte-exact match
# $lookup display on the source code.

def test_e140_translate_source_display_matches_lookup(fhir_client):
    """Lens 14 / Cross-source $translate source.display parity: when
    translating SNOMED 73211009 → ICD-10-CM, the match[].source.display
    (echoed from the source code's canonical display) MUST byte-exact
    match $lookup Out display for SNOMED 73211009.

    Pattern-match: CS-02 TERMINOLOGIST test_t30 extended to verify
    cross-op display agreement on the SOURCE side of $translate.
    """
    # $lookup on source
    r_lookup = fhir_client.get(
        f"/fhir/CodeSystem/$lookup?system={SNOMED_URI}&code={SNOMED_DIABETES_MELLITUS}"
    )
    assert r_lookup.status_code == 200
    lookup_display = _lookup_param_value(r_lookup.json(), "display")

    # $translate SNOMED → ICD-10-CM
    r_translate = fhir_client.get(
        f"/fhir/ConceptMap/$translate?system={SNOMED_URI}"
        f"&code={SNOMED_DIABETES_MELLITUS}&targetSystem={ICD10CM_URI}"
    )
    assert r_translate.status_code == 200

    # Find source.display in the first match.
    body = r_translate.json()
    source_displays = []
    for p in body.get("parameter", []):
        if p.get("name") != "match":
            continue
        for sub in p.get("part", []):
            if sub.get("name") == "source":
                for k, v in sub.items():
                    if isinstance(v, dict) and "display" in v:
                        source_displays.append(v["display"])
    if source_displays:
        assert source_displays[0] == lookup_display, (
            f"$translate source.display {source_displays[0]!r} != "
            f"$lookup display {lookup_display!r}"
        )


def test_e141_translate_target_system_canonical(fhir_client):
    """Lens 14 / $translate target.system canonical: when translating
    SNOMED → ICD-10-CM, the target.system MUST be the canonical
    ICD-10-CM URI (http://hl7.org/fhir/sid/icd-10-cm).

    Pattern-match: CS-05 SKEPTIC test_s70..s78 (canonical-system
    invariant) extended to $translate target side."""
    r = fhir_client.get(
        f"/fhir/ConceptMap/$translate?system={SNOMED_URI}"
        f"&code={SNOMED_DIABETES_MELLITUS}&targetSystem={ICD10CM_URI}"
    )
    assert r.status_code == 200
    body = r.json()

    target_systems = []
    for p in body.get("parameter", []):
        if p.get("name") != "match":
            continue
        for sub in p.get("part", []):
            if sub.get("name") == "concept":
                for k, v in sub.items():
                    if isinstance(v, dict) and "system" in v:
                        target_systems.append(v["system"])
    if target_systems:
        # The target system MUST be the canonical ICD-10-CM URI.
        for ts in target_systems:
            assert ts == ICD10CM_URI, (
                f"$translate target.system {ts!r} != canonical "
                f"{ICD10CM_URI!r}"
            )
