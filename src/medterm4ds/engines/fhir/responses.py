"""FHIR R4 response builders for the terminology facade."""

from __future__ import annotations

from typing import Any

from medterm4ds.core.models import CodeInfo, CodeMapping
from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI, system_to_fhir_uri

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
) -> dict[str, Any]:
    """Build a FHIR Parameters resource for CodeSystem $validate-code."""
    params: list[dict[str, Any]] = [
        {"name": "result", "valueBoolean": result},
        {"name": "code", "valueCode": code},
        {"name": "system", "valueUri": system_uri},
    ]
    if display or (code_info and code_info.name):
        params.append(
            _param("display", display or (code_info.name if code_info else None))
        )
    return {"resourceType": "Parameters", "parameter": params}


def build_parameters_translate(
    mappings: list[CodeMapping],
    *,
    source_system_uri: str,
    source_code: str,
) -> dict[str, Any]:
    """Build a FHIR Parameters resource for ConceptMap $translate."""
    matches: list[dict[str, Any]] = []
    for m in mappings:
        target_uri = system_to_fhir_uri(m.target.source) or m.target.source
        match_entry: dict[str, Any] = {
            "name": "match",
            "part": [
                {"name": "equivalence", "valueCode": "equivalent"},
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
) -> dict[str, Any]:
    """Build a FHIR ValueSet resource with expansion for $expand."""
    vs: dict[str, Any] = {
        "resourceType": "ValueSet",
        "status": "active",
        "expansion": {
            "timestamp": "2026-06-27T00:00:00Z",
            "total": len(contains),
            "contains": contains,
        },
    }
    if url:
        vs["url"] = url
    return vs


def build_capability_statement(base_url: str = "http://127.0.0.1:8001") -> dict[str, Any]:
    """Build a FHIR CapabilityStatement advertising supported operations."""
    return {
        "resourceType": "CapabilityStatement",
        "status": "active",
        "date": "2026-06-27",
        "publisher": "medterm4ds",
        "kind": "instance",
        "fhirVersion": "4.0.1",
        "format": ["json"],
        "rest": [
            {
                "mode": "server",
                "resource": [
                    {
                        "type": "CodeSystem",
                        "operation": [
                            {"name": "lookup", "definition": f"{base_url}/OperationDefinition/cs-lookup"},
                            {"name": "validate-code", "definition": f"{base_url}/OperationDefinition/cs-validate-code"},
                            {"name": "subsumes", "definition": f"{base_url}/OperationDefinition/cs-subsumes"},
                            {"name": "closure", "definition": f"{base_url}/OperationDefinition/cs-closure"},
                            {"name": "search", "definition": f"{base_url}/OperationDefinition/cs-search"},
                        ],
                    },
                    {
                        "type": "ValueSet",
                        "operation": [
                            {"name": "expand", "definition": f"{base_url}/OperationDefinition/vs-expand"},
                        ],
                    },
                    {
                        "type": "ConceptMap",
                        "operation": [
                            {"name": "translate", "definition": f"{base_url}/OperationDefinition/cm-translate"},
                        ],
                    },
                ],
            }
        ],
    }


_SYSTEM_DISPLAY_NAMES = {
    "http://snomed.info/sct": "SNOMED Clinical Terms (US)",
    "http://www.nlm.nih.gov/research/umls/rxnorm": "RxNorm",
    "http://hl7.org/fhir/sid/icd-10-cm": "International Classification of Diseases, 10th Revision, Clinical Modification",
    "http://hl7.org/fhir/sid/icd-10-pcs": "International Classification of Diseases, 10th Revision, Procedure Coding System",
    "http://loinc.org": "Logical Observation Identifiers Names and Codes (LOINC)",
    "http://www.ama-assn.org/go/cpt": "Current Procedural Terminology (CPT)",
    "http://terminology.hl7.org/CodeSystem/hcpcs-Level-II": "Healthcare Common Procedure Coding System (HCPCS Level II)",
    "http://hl7.org/fhir/sid/cvx": "Vaccine Administered Code Set (CVX)",
}


def _system_display_name(uri: str) -> str:
    return _SYSTEM_DISPLAY_NAMES.get(uri, uri)
