"""FHIR R4 response builders for the terminology facade."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from medterm4ds.core.models import CodeInfo, CodeMapping
from medterm4ds.engines.fhir import (
    SYSTEM_TO_FHIR_URI,
    system_to_fhir_uri,
)
# CR-024 (milestone-3 review): the engine → R4 ConceptMapEquivalence
# translation map is now defined in the canonical ``equivalence`` submodule.
# Both this module (the $translate HTTP surface) and ``outputs/fhir.py`` (the
# ConceptMap export surface) import from there, so the two surfaces can no
# longer drift on shared keys. The closed-enum membership assertion at the
# canonical module's load time applies to BOTH surfaces uniformly.
from medterm4ds.engines.fhir.equivalence import (
    INTERNAL_REL_TO_FHIR_EQUIVALENCE as _INTERNAL_REL_TO_FHIR_EQUIVALENCE,
)

MATCH_GRADE_EXTENSION_URL = "http://fhir4ds.org/fhir/StructureDefinition/match-grade"


def _param(name: str, value: Any, type_name: str = "valueString") -> dict[str, Any]:
    """Build a single FHIR Parameters.parameter entry."""
    return {"name": name, type_name: value}


def _property_param(code: str, value: Any) -> dict[str, Any]:
    """Build a FHIR Parameters property entry with parts."""
    return {
        "name": "property",
        "part": [
            {"name": "code", "valueCode": code},
            {"name": "value", "valueString": str(value) if value is not None else ""},
        ],
    }


def build_parameters_lookup(
    code_info: CodeInfo | None,
    *,
    system_uri: str,
    custom_properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a FHIR Parameters resource for CodeSystem $lookup."""
    if code_info is None:
        return build_operation_outcome(
            "error", "not-found", "Code not found in the specified system."
        )

    params: list[dict[str, Any]] = [
        _param("name", _system_display_name(system_uri)),
        _param("code", code_info.code.code, "valueCode"),
        _param("system", system_uri, "valueUri"),
        _param("display", code_info.name or code_info.code.code),
        _param("abstract", False, "valueBoolean"),
    ]
    if code_info.cui:
        params.append(_property_param("cui", code_info.cui))
    if code_info.tty:
        params.append(_property_param("tty", code_info.tty))
    if code_info.aui:
        params.append(_property_param("aui", code_info.aui))
    if custom_properties:
        for key, val in custom_properties.items():
            if val is not None:
                params.append(_property_param(key, val))
    return {"resourceType": "Parameters", "parameter": params}


def build_parameters_validate(
    result: bool,
    *,
    system_uri: str,
    code: str,
    display: str | None = None,
    code_info: CodeInfo | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    """Build a FHIR Parameters resource for CodeSystem $validate-code.

    Per FHIR R4 Out Parameters (https://hl7.org/fhir/R4/codesystem-operation-
    validate-code.html): the Out `display` is "A display to show to the user
    when the system doesn't know what to do with the code, or to verify the
    code is the right one." This is the **server's canonical display** for
    the code, NOT an echo of the client-supplied `display` parameter. The
    client-supplied value is compared against this canonical display to
    drive `result`; the response shows what the server holds. Found by
    TERMINOLOGIST iteration TS-02 (QA-029).

    The optional Out `message` carries "Error details, if result = false.
    If this is provided when result = true, the message carries hints and
    warnings". When the client-supplied display does not match the
    engine's canonical display, the caller passes the message and sets
    `result=False`. Found by SKEPTIC iteration CS-03 (QA-048) — the spec
    example response mandates this shape: result=false + message
    "The display \"X\" is incorrect" + the canonical display.
    """
    params: list[dict[str, Any]] = [
        {"name": "result", "valueBoolean": result},
        {"name": "code", "valueCode": code},
        {"name": "system", "valueUri": system_uri},
    ]
    # Prefer the engine's canonical display; fall back to the client-supplied
    # display only when the engine has no name for the code (e.g. when the
    # engine lookup failed but the server still wants to return a Parameters
    # body for client-side display). NEVER echo the client's display when the
    # engine has a different canonical name — that would mask display drift.
    canonical = (code_info.name if code_info and code_info.name else None) or display
    if canonical:
        params.append(_param("display", canonical))
    if message:
        params.append(_param("message", message))
    return {"resourceType": "Parameters", "parameter": params}


# Map medterm4ds internal CodeMapping.relationship vocabulary to the FHIR R4
# ConceptMapEquivalence enum — the canonical translation table lives in
# ``engines/fhir/equivalence.py`` (CR-024, milestone-3 review). Both this
# module and ``outputs/fhir.py`` import it from there, so the $translate HTTP
# surface and the ConceptMap export surface can no longer drift on shared
# keys. The closed-enum membership assertion at the canonical module's load
# time applies to BOTH surfaces uniformly.
#
# CF-HISTORIAN-VS01-01 (milestone-2 review): the prior inline map emitted two
# values that were NOT in the FHIR R4 closed enum:
#   * ``subsumedby`` (R5/R4B value; R4 spec-correct is ``specializes``).
#   * ``not-relatedto`` (not in ANY FHIR enum; R4 catch-all for "no mapping"
#     is ``unmatched``).
# Fixed in the milestone-2 structural remediation pass and inherited by this
# module via the canonical import.


def _fhir_equivalence_from_relationship(relationship: str | None) -> str:
    """Translate a CodeMapping.relationship to a FHIR R4 ConceptMapEquivalence
    value. Returns ``"relatedto"`` for unknown/null relationships — the FHIR
    enum's catch-all for "a relationship exists but isn't a strict equivalence".

    Never raises: the FHIR enum is closed, so unrecognized internal vocabularies
    MUST be translated rather than echoed raw (otherwise the response contains
    a value outside the FHIR R4 value set).
    """
    if not relationship:
        return "relatedto"
    # Try exact match first, then case-insensitive. The engine currently emits
    # lowercase values; the case-insensitive fallback future-proofs against a
    # vocabulary change without silently emitting a non-FHIR value.
    exact = _INTERNAL_REL_TO_FHIR_EQUIVALENCE.get(relationship)
    if exact:
        return exact
    lowered = relationship.lower()
    return _INTERNAL_REL_TO_FHIR_EQUIVALENCE.get(lowered, "relatedto")


def build_parameters_translate(
    mappings: list[CodeMapping],
    *,
    source_system_uri: str,
    source_code: str,
) -> dict[str, Any]:
    """Build a FHIR Parameters resource for ConceptMap $translate.

    The match.equivalence value is sourced from each ``CodeMapping.relationship``
    and translated to the FHIR R4 ConceptMapEquivalence enum via
    ``_fhir_equivalence_from_relationship``. Hardcoding "equivalent" would
    misrepresent SNOMED→ICD10CM crosswalks (which are typically `relatedto`)
    and ancestor/descendant mappings (which are `subsumes`/`specializes`).
    Found by TERMINOLOGIST iteration TS-02 (QA-030). CF-HISTORIAN-VS01-01
    (milestone-2 review) fixed the map to emit R4 spec-correct values.
    """
    matches: list[dict[str, Any]] = []
    for m in mappings:
        target_uri = system_to_fhir_uri(m.target.source) or m.target.source
        match_entry: dict[str, Any] = {
            "name": "match",
            "part": [
                {"name": "equivalence", "valueCode": _fhir_equivalence_from_relationship(m.relationship)},
                {"name": "concept", "valueCoding": {
                    "system": target_uri,
                    "code": m.target.code,
                    "display": m.target_display or "",
                }},
                {"name": "source", "valueCoding": {
                    "system": source_system_uri,
                    "code": source_code,
                }},
            ],
        }
        matches.append(match_entry)

    result_val = len(matches) > 0
    return {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "result", "valueBoolean": result_val},
            {"name": "message", "valueString": f"{len(matches)} matches found"},
            *matches,
        ],
    }


def build_bundle_search(
    results: list[dict[str, Any]],
    *,
    query: str,
    search_mode: str = "lexical",
) -> dict[str, Any]:
    """Build a FHIR Bundle for the custom $search operation.

    Each result dict has: code, system, display, score, match_grade, [category].
    Modeled after Patient $match — entries have search.score + match-grade extension.
    """
    entries: list[dict[str, Any]] = []
    for r in results:
        entry: dict[str, Any] = {
            "fullUrl": f"CodeSystem/{r['system']}-{r['code']}",
            "resource": {
                "system": r["system"],
                "code": r["code"],
                "display": r["display"],
            },
            "search": {
                "mode": "match",
                "score": r["score"],
                "extension": [
                    {
                        "url": MATCH_GRADE_EXTENSION_URL,
                        "valueCode": r["match_grade"],
                    }
                ],
            },
        }
        entries.append(entry)

    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": len(entries),
        "entry": entries,
    }


def build_operation_outcome(
    severity: str = "error",
    code: str = "invalid",
    diagnostics: str = "",
) -> dict[str, Any]:
    """Build a FHIR OperationOutcome resource for errors."""
    return {
        "resourceType": "OperationOutcome",
        "issue": [
            {
                "severity": severity,
                "code": code,
                "diagnostics": diagnostics,
            }
        ],
    }


def build_parameters_subsumes(outcome: str) -> dict[str, Any]:
    """Build a FHIR Parameters resource for CodeSystem $subsumes.

    outcome: "equivalent" | "subsumes" | "subsumed-by" | "not-subsumed"
    """
    return {
        "resourceType": "Parameters",
        "parameter": [{"name": "outcome", "valueCode": outcome}],
    }


def build_valueset_expand(
    contains: list[dict[str, Any]],
    *,
    url: str | None = None,
    filter_text: str | None = None,
    extensions: list[dict[str, Any]] | None = None,
    total: int | None = None,
) -> dict[str, Any]:
    """Build a FHIR ValueSet resource with expansion for $expand.

    Args:
        contains: The (possibly count-truncated) ``contains[]`` list to
            include in the expansion.
        url: Optional ValueSet canonical URL.
        filter_text: Optional filter text echoed in the response.
        extensions: Optional expansion-level extensions (e.g.
            ``valueset-toocostly`` when count truncated).
        total: Optional override for ``expansion.total``. When None,
            defaults to ``len(contains)`` (the un-truncated size when call
            sites pass it; the truncated size otherwise).

            Per FHIR R4 §4.9.2 ``ValueSet.expansion.total``: "The total
            number of concepts in the expansion." When count truncation
            occurs, ``total`` MUST reflect the UN-truncated size — clients
            paging an expansion rely on this field to know how many entries
            to expect across all pages. Found by SKEPTIC iteration VS-02
            (QA-057). Call sites that pre-truncate via ``[:count]`` MUST
            pass the pre-truncation size here; call sites that don't
            pre-truncate may leave this as None.
    """
    vs: dict[str, Any] = {
        "resourceType": "ValueSet",
        "status": "active",
        "expansion": {
            # VR-001 (v0.0.1 code review): per FHIR R4 §4.9.2, expansion.timestamp
            # is "Time ValueSet expansion was produced". The prior hardcoded
            # literal reported a stale date on every response.
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total": len(contains) if total is None else total,
            "contains": contains,
        },
    }
    if url:
        vs["url"] = url
    if extensions:
        vs["expansion"]["extension"] = extensions
    return vs


CAPABILITY_STATEMENT_URL = "http://medterm4ds.org/fhir/CapabilityStatement/terminology-server"
# VR-004 (v0.0.1 code review): source from package metadata so future releases
# don't require manual sync. Late import to avoid a circular dependency at
# module load (medterm4ds.__init__ imports submodules).
def _package_version() -> str:
    try:
        from medterm4ds import __version__
        return __version__
    except (ImportError, AttributeError):
        return "0.0.1"
CAPABILITY_STATEMENT_VERSION = _package_version()
# VR-002 (v0.0.1 code review): per FHIR R4 §3.2.1.0.5, CapabilityStatement.date
# is "The date this was last changed". Stale literal reported a date 9+ days in
# the past at v0.0.1 ship time; dynamic date tracks the actual response time.
CAPABILITY_STATEMENT_DATE = date.today().isoformat()
CAPABILITY_STATEMENT_NAME = "Medterm4dsTerminologyServer"
CAPABILITY_STATEMENT_TITLE = "medterm4ds FHIR Terminology Server"
CAPABILITY_STATEMENT_DESCRIPTION = (
    "medterm4ds FHIR R4 terminology server facade. Exposes $lookup, "
    "$validate-code, $translate, $subsumes, $expand, $closure, and the "
    "custom $search / $extract operations over UMLS data loaded into DuckDB."
)
CAPABILITY_STATEMENT_DATE = "2026-07-05"

# FHIR R4 extension URL for advertising the list of code-system URIs supported
# by a terminology server. Per https://hl7.org/fhir/R4/extension-
# capabilitystatement-supported-system.html: 'A list of all the system URIs for
# code systems that are supported by the server.' Clients use this extension
# to discover supported systems without trial-and-error on $lookup. Found by
# SKEPTIC iteration TS-03 (QA-031).
SUPPORTED_SYSTEM_EXTENSION_URL = (
    "http://hl7.org/fhir/StructureDefinition/capabilitystatement-supported-system"
)


def _supported_system_extensions() -> list[dict[str, Any]]:
    """Build the capabilitystatement-supported-system extension list.

    One extension entry per supported external code system URI, sourced from
    `SYSTEM_TO_FHIR_URI` (the single source of truth — do NOT hardcode URIs
    here; that would re-introduce the literal-vs-canonical-registry drift
    pattern, count=3 as of TS-02 TERMINOLOGIST QA-030).
    """
    return [
        {"url": SUPPORTED_SYSTEM_EXTENSION_URL, "valueUri": uri}
        for _, uri in sorted(SYSTEM_TO_FHIR_URI.items())
    ]


# Canonical HL7 OperationDefinition URIs for the FHIR R4 terminology operations.
# Per §3.2.1.0.5 (CapabilityStatement.operation.definition): 'Definition of the
# operation - a reference to an OperationDefinition resource ... Note that the
# OperationDefinition is a single, canonical definition of the operation.' Using
# server-local URIs makes it impossible for clients to confirm whether the
# operation is the standard FHIR one or a custom variant. Found by SKEPTIC
# iteration TS-02 (QA-016).
OPDEF_LOOKUP = "http://hl7.org/fhir/OperationDefinition/CodeSystem-lookup"
OPDEF_CS_VALIDATE_CODE = "http://hl7.org/fhir/OperationDefinition/CodeSystem-validate-code"
OPDEF_SUBSUMES = "http://hl7.org/fhir/OperationDefinition/CodeSystem-subsumes"
OPDEF_CLOSURE = "http://hl7.org/fhir/OperationDefinition/CodeSystem-closure"
OPDEF_EXPAND = "http://hl7.org/fhir/OperationDefinition/ValueSet-expand"
OPDEF_VS_VALIDATE_CODE = "http://hl7.org/fhir/OperationDefinition/ValueSet-validate-code"
OPDEF_TRANSLATE = "http://hl7.org/fhir/OperationDefinition/ConceptMap-translate"

# The custom $search and $extract operations have no canonical HL7
# OperationDefinition. Use medterm4ds-local URIs and document this in the
# operation's `documentation` field so clients can distinguish them.
OPDEF_SEARCH = f"{CAPABILITY_STATEMENT_URL}/OperationDefinition/CodeSystem-search"


def build_capability_statement(base_url: str = "http://127.0.0.1:8001") -> dict[str, Any]:
    """Build a FHIR CapabilityStatement advertising supported operations.

    Conforms to FHIR R4 terminology-service §4.7.1.1 item 4: includes the
    required elements url, version, name, title, status, date, description,
    kind=instance, fhirVersion.

    The ``base_url`` carries the deployment scheme + host + port (e.g.
    ``https://fhir.example.com:443``). It is surfaced as
    ``implementation.url`` and ``rest[].url`` per FHIR R4 §3.2.1.0.5 so
    clients can discover the deployment endpoint — including the correct
    scheme for HTTPS deployments (per §4.7.2 'Servers SHOULD ensure that
    all interactions occur over a secure connection'). Found by SKEPTIC
    iteration TS-04 (QA-037).
    """
    return {
        "resourceType": "CapabilityStatement",
        "url": CAPABILITY_STATEMENT_URL,
        "version": CAPABILITY_STATEMENT_VERSION,
        "name": CAPABILITY_STATEMENT_NAME,
        "title": CAPABILITY_STATEMENT_TITLE,
        "status": "active",
        "date": CAPABILITY_STATEMENT_DATE,
        "description": CAPABILITY_STATEMENT_DESCRIPTION,
        "publisher": "medterm4ds",
        "kind": "instance",
        "fhirVersion": "4.0.1",
        "format": ["json", "xml"],
        # Per FHIR R4 §3.2.1.0.5 implementation.url: identifies the deployment
        # endpoint. Surfacing the scheme is load-bearing for §4.7.2 — without
        # it, an HTTPS deployment advertised via env var has no surfaced
        # signal, and clients following the CapabilityStatement may attempt
        # plain HTTP. Found by SKEPTIC iteration TS-04 (QA-037).
        "implementation": {
            "url": base_url,
            "description": "medterm4ds FHIR terminology server deployment.",
        },
        # FHIR R4 §4.7.3 / extension spec: advertise every supported external
        # code system URI so clients can discover them without trial-and-error
        # on $lookup. Sourced from SYSTEM_TO_FHIR_URI (single source of truth).
        # Found by SKEPTIC iteration TS-03 (QA-031).
        "extension": _supported_system_extensions(),
        "rest": [
            {
                "mode": "server",
                # DA-6 (v0.0.1 docs audit): per FHIR R4 §3.2.1.0.4, a server
                # that supports batch/transaction processing SHOULD advertise
                # it via rest[].interaction. Without this, clients introspecting
                # the CapabilityStatement can't discover the POST /fhir batch
                # endpoint (FHIR R4 §3.7). Spec:
                # https://hl7.org/fhir/R4/capabilitystatement-definitions.html#CapabilityStatement.rest.interaction
                "interaction": [
                    {"code": "batch"},
                    {"code": "transaction"},
                ],
                "resource": [
                    {
                        "type": "CodeSystem",
                        "interaction": [
                            {"code": "read"},
                            {"code": "search-type"},
                        ],
                        "searchParam": [
                            {"name": "url", "type": "uri"},
                            {"name": "version", "type": "token"},
                            {"name": "name", "type": "string"},
                            {"name": "title", "type": "string"},
                            {"name": "status", "type": "token"},
                        ],
                        "operation": [
                            {"name": "lookup", "definition": OPDEF_LOOKUP},
                            {"name": "validate-code", "definition": OPDEF_CS_VALIDATE_CODE},
                            {"name": "subsumes", "definition": OPDEF_SUBSUMES},
                            {"name": "closure", "definition": OPDEF_CLOSURE},
                            {
                                "name": "search",
                                "definition": OPDEF_SEARCH,
                                "documentation": (
                                    "Custom medterm4ds operation (modeled after "
                                    "Patient $match). Not the standard FHIR search "
                                    "interaction; supports lexical, semantic, and hybrid modes."
                                ),
                            },
                        ],
                    },
                    {
                        "type": "ValueSet",
                        "interaction": [
                            {"code": "read"},
                            {"code": "search-type"},
                        ],
                        "searchParam": [
                            {"name": "url", "type": "uri"},
                            {"name": "version", "type": "token"},
                            {"name": "name", "type": "string"},
                            {"name": "title", "type": "string"},
                            {"name": "status", "type": "token"},
                        ],
                        "operation": [
                            {"name": "expand", "definition": OPDEF_EXPAND},
                            {"name": "validate-code", "definition": OPDEF_VS_VALIDATE_CODE},
                        ],
                    },
                    {
                        "type": "ConceptMap",
                        "interaction": [
                            {"code": "read"},
                            {"code": "search-type"},
                        ],
                        "searchParam": [
                            {"name": "url", "type": "uri"},
                            {"name": "version", "type": "token"},
                            {"name": "name", "type": "string"},
                            {"name": "title", "type": "string"},
                            {"name": "status", "type": "token"},
                        ],
                        "operation": [
                            {"name": "translate", "definition": OPDEF_TRANSLATE},
                        ],
                    },
                ],
            }
        ],
    }


def build_terminology_capabilities(base_url: str = "http://127.0.0.1:8001") -> dict[str, Any]:
    """Build a FHIR TerminologyCapabilities resource.

    Conforms to FHIR R4 terminology-service §4.7.1.1 item 5: includes the
    required elements url, name, title, status, date, kind=instance, and a
    codeSystem block per supported code system with uri, version, and content.
    """
    code_systems: list[dict[str, Any]] = []
    for source, uri in sorted(SYSTEM_TO_FHIR_URI.items()):
        code_systems.append({
            "uri": uri,
            "content": "not-present",
        })
    return {
        "resourceType": "TerminologyCapabilities",
        "url": f"{base_url}/TerminologyCapabilities/terminology",
        "name": CAPABILITY_STATEMENT_NAME,
        "title": CAPABILITY_STATEMENT_TITLE,
        "status": "active",
        "date": CAPABILITY_STATEMENT_DATE,
        "kind": "instance",
        "fhirVersion": "4.0.1",
        "codeSystem": code_systems,
    }


_SYSTEM_DISPLAY_NAMES = {
    "http://snomed.info/sct": "SNOMED Clinical Terms (US)",
    "http://www.nlm.nih.gov/research/umls/rxnorm": "RxNorm",
    "http://hl7.org/fhir/sid/icd-10-cm": "International Classification of Diseases, 10th Revision, Clinical Modification",
    "http://hl7.org/fhir/sid/icd-10-pcs": "International Classification of Diseases, 10th Revision, Procedure Coding System",
    "http://loinc.org": "Logical Observation Identifiers Names and Codes (LOINC)",
    "http://www.ama-assn.org/go/cpt": "Current Procedural Terminology (CPT)",
    "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets": "Healthcare Common Procedure Coding System (HCPCS Level II)",
    "http://hl7.org/fhir/sid/cvx": "Vaccine Administered Code Set (CVX)",
}


def _system_display_name(uri: str) -> str:
    return _SYSTEM_DISPLAY_NAMES.get(uri, uri)
