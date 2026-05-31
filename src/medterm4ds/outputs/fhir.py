"""FHIR ConceptMap output helpers."""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from medterm4ds.core.models import ConceptMapRow
from medterm4ds.core.normalize import normalize_source

PATIENT_FRIENDLY_SYSTEM = "urn:medterm4ds:CodeSystem:patient-friendly"
DEFAULT_CONCEPT_MAP_URL = "urn:medterm4ds:ConceptMap:patient-friendly"

FHIR_CODE_SYSTEMS = {
    "ICD10CM": "http://hl7.org/fhir/sid/icd-10-cm",
    "ICD10PCS": "http://www.cms.gov/Medicare/Coding/ICD10",
    "HCPCS": "http://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets",
    "SNOMEDCT_US": "http://snomed.info/sct",
    "RXNORM": "http://www.nlm.nih.gov/research/umls/rxnorm",
    "LNC": "http://loinc.org",
    "CVX": "http://hl7.org/fhir/sid/cvx",
    "CPT": "http://www.ama-assn.org/go/cpt",
    "PATIENT_FRIENDLY": PATIENT_FRIENDLY_SYSTEM,
}

FHIR_EQUIVALENCES = {
    "equivalent": "equivalent",
    "source-is-narrower-than-target": "wider",
    "source-is-broader-than-target": "narrower",
    "related-to": "relatedto",
    "not-related-to": "disjoint",
    "unmatched": "unmatched",
    "not-translated": "equivalent",
}

EXTENSION_BASE = "urn:medterm4ds:StructureDefinition"


def concept_map_to_fhir(
    rows: Iterable[ConceptMapRow],
    *,
    id_: str = "medterm4ds-patient-friendly",
    url: str = DEFAULT_CONCEPT_MAP_URL,
    name: str = "Medterm4dsPatientFriendlyConceptMap",
    title: str = "medterm4ds Patient Friendly ConceptMap",
    status: str = "draft",
    publisher: str = "medterm4ds",
    version: str | None = None,
    date: str | None = None,
    include_extensions: bool = True,
    system_uris: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Convert internal ConceptMap rows to a FHIR R4 ConceptMap resource."""
    systems = {**FHIR_CODE_SYSTEMS, **(system_uris or {})}
    resource: dict[str, Any] = {
        "resourceType": "ConceptMap",
        "id": id_,
        "url": url,
        "name": name,
        "title": title,
        "status": status,
        "publisher": publisher,
        "group": [],
    }
    if version:
        resource["version"] = version
    if date:
        resource["date"] = date

    groups: OrderedDict[tuple[str, str], OrderedDict[str, dict[str, Any]]] = OrderedDict()
    for row in rows:
        source_system = code_system_uri(row.source.source, systems)
        target_system = code_system_uri(row.target.source, systems)
        group_key = (source_system, target_system)
        elements = groups.setdefault(group_key, OrderedDict())
        element = elements.setdefault(
            row.source.code,
            _element(row),
        )
        _merge_row_target(element, row, include_extensions=include_extensions)

    for (source_system, target_system), elements in groups.items():
        resource["group"].append(
            {
                "source": source_system,
                "target": target_system,
                "element": list(elements.values()),
            }
        )

    return resource


def write_fhir_concept_map(
    rows: Iterable[ConceptMapRow],
    path: str | Path,
    **kwargs: Any,
) -> Path:
    """Write rows as a FHIR R4 ConceptMap JSON resource."""
    output_path = Path(path)
    text = json.dumps(concept_map_to_fhir(rows, **kwargs), indent=2, sort_keys=False)
    output_path.write_text(f"{text}\n", encoding="utf-8")
    return output_path


def code_system_uri(source: str, system_uris: Mapping[str, str] | None = None) -> str:
    """Return a FHIR code system URI for a medterm source name."""
    systems = system_uris or FHIR_CODE_SYSTEMS
    normalized = normalize_source(source)
    return systems.get(normalized, f"urn:medterm4ds:CodeSystem:{normalized}")


def utc_date_time() -> str:
    """Return a FHIR dateTime-compatible UTC timestamp."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _element(row: ConceptMapRow) -> dict[str, Any]:
    element = {
        "code": row.source.code,
    }
    if row.source_display:
        element["display"] = row.source_display
    return element


def _merge_row_target(
    element: dict[str, Any],
    row: ConceptMapRow,
    *,
    include_extensions: bool,
) -> None:
    target = {
        "equivalence": fhir_equivalence(row.relationship),
    }
    if row.relationship != "unmatched":
        target["code"] = row.target.code
        target["display"] = row.target_display
    if include_extensions:
        extensions = _target_extensions(row)
        if extensions:
            target["extension"] = extensions
    element.setdefault("target", []).append(target)


def fhir_equivalence(relationship: str | None) -> str:
    """Map internal relationship labels to FHIR R4 ConceptMap equivalence codes."""
    if not relationship:
        return "relatedto"
    return FHIR_EQUIVALENCES.get(relationship, "relatedto")


def _target_extensions(row: ConceptMapRow) -> list[dict[str, Any]]:
    extensions: list[dict[str, Any]] = []
    _add_extension(extensions, "relationship", row.relationship, "valueCode")
    _add_extension(extensions, "friendly-source", row.friendly_source, "valueCode")
    _add_extension(extensions, "match-type", row.match_type, "valueCode")
    _add_extension(extensions, "match-depth", row.match_depth, "valueInteger")
    if row.matched_via:
        _add_extension(
            extensions,
            "matched-via",
            json.dumps(row.matched_via.to_dict(), sort_keys=True, separators=(",", ":")),
            "valueString",
        )
    return extensions


def _add_extension(
    extensions: list[dict[str, Any]],
    name: str,
    value: Any,
    value_key: str,
) -> None:
    if value is None:
        return
    extensions.append(
        {
            "url": f"{EXTENSION_BASE}/{name}",
            value_key: value,
        }
    )
