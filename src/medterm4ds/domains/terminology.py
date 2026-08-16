"""Domain-specific terminology helpers built on core services."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from medterm4ds.core.models import CodeRef
from medterm4ds.core.normalize import normalize_source
from medterm4ds.services.discovery import MAX_DISCOVERY_LIMIT, sample_source_codes, search_names
from medterm4ds.services.hierarchy import get_code_relations
from medterm4ds.services.lookup import get_code_infos
from medterm4ds.services.mapping import get_code_mappings

_SMART_CROSS_REFERENCE_TARGETS = {
    "ICD10CM": ("SNOMEDCT_US", "MSH"),
    "SNOMEDCT_US": ("ICD10CM", "MSH"),
    "MSH": ("ICD10CM", "SNOMEDCT_US"),
    "RXNORM": ("ATC",),
    "ATC": ("RXNORM",),
    "LNC": ("SNOMEDCT_US", "MSH"),
    "LOINC": ("SNOMEDCT_US", "MSH"),
    "CVX": ("RXNORM", "HCPCS"),
    "CPT": ("HCPCS", "SNOMEDCT_US"),
    "HCPCS": ("CPT", "SNOMEDCT_US", "RXNORM"),
}



def terminology_search(
    query: str,
    *,
    engine,
    sources: Sequence[str],
    limit: int = 20,
    tty_filters: Sequence[str] | str | None = None,
    query_name: str = "terminology_search",
    query_field: str = "query",
) -> dict[str, Any]:
    """Search domain terminology sources and return structured rows."""
    normalized_sources = tuple(dict.fromkeys(normalize_source(source) for source in sources))
    rows = search_names(
        query,
        engine=engine,
        sources=normalized_sources,
        tty_filters=tty_filters,
        limit=limit,
    )
    return {
        "query": query_name,
        query_field: query,
        "sources": list(normalized_sources),
        "result_count": len(rows),
        "results": [row.to_dict() for row in rows],
    }


def diagnosis_codes(
    condition: str,
    *,
    engine,
    limit: int = 20,
    descendant_depth: int | None = None,
    include_ancestors: bool | None = None,
) -> dict[str, Any]:
    """Search diagnosis-oriented ICD-10-CM and SNOMED CT codes."""
    payload = terminology_search(
        condition,
        engine=engine,
        sources=("ICD10CM", "SNOMEDCT_US"),
        limit=limit,
        query_name="diagnosis_codes",
        query_field="condition",
    )
    _add_context_relations(payload, engine=engine, descendant_depth=descendant_depth, include_ancestors=include_ancestors)
    return payload


def lab_codes(
    lab_test: str,
    *,
    engine,
    limit: int = 20,
) -> dict[str, Any]:
    """Search lab test terminology sources."""
    return terminology_search(
        lab_test,
        engine=engine,
        sources=("LNC", "SNOMEDCT_US", "ICD10CM", "CPT", "HCPCS"),
        limit=limit,
        query_name="lab_codes",
        query_field="lab_test",
    )


def lab_value_codes(
    clinical_value: str,
    *,
    engine,
    limit: int = 20,
) -> dict[str, Any]:
    """Search lab value or clinical finding terminology sources."""
    return terminology_search(
        clinical_value,
        engine=engine,
        sources=("LNC", "SNOMEDCT_US"),
        limit=limit,
        query_name="lab_value_codes",
        query_field="clinical_value",
    )


def procedure_codes(
    procedure: str,
    *,
    engine,
    limit: int = 20,
    descendant_depth: int | None = None,
    include_ancestors: bool | None = None,
) -> dict[str, Any]:
    """Search procedure terminology sources."""
    payload = terminology_search(
        procedure,
        engine=engine,
        sources=("CPT", "HCPCS", "SNOMEDCT_US", "ICD10PCS"),
        limit=limit,
        query_name="procedure_codes",
        query_field="procedure",
    )
    _add_context_relations(payload, engine=engine, descendant_depth=descendant_depth, include_ancestors=include_ancestors)
    return payload


def hcpcs_drugs(
    drug_name: str,
    *,
    engine,
    limit: int = 20,
) -> dict[str, Any]:
    """Search HCPCS drug/device codes."""
    return terminology_search(
        drug_name,
        engine=engine,
        sources=("HCPCS",),
        limit=limit,
        query_name="hcpcs_drugs",
        query_field="drug_name",
    )


def vaccine_codes(
    vaccine: str,
    *,
    engine,
    limit: int = 20,
) -> dict[str, Any]:
    """Search vaccine-oriented CVX, RxNorm, and HCPCS codes."""
    return terminology_search(
        vaccine,
        engine=engine,
        sources=("CVX", "RXNORM", "HCPCS"),
        limit=limit,
        query_name="vaccine_codes",
        query_field="vaccine",
    )


def search_drug(
    drug_name: str,
    *,
    engine,
    limit: int = 20,
    tty_filters: Sequence[str] | str | None = None,
    include_equivalents: bool = True,
    include_ndc: bool = False,
) -> dict[str, Any]:
    """Search RxNorm drug names."""
    payload = terminology_search(
        drug_name,
        engine=engine,
        sources=("RXNORM",),
        limit=limit,
        tty_filters=tty_filters,
        query_name="search_drug",
        query_field="drug_name",
    )
    rows = payload["results"]
    if include_equivalents and rows:
        refs = [CodeRef("RXNORM", row["code"]) for row in rows[: min(len(rows), 10)]]
        mappings = get_code_mappings(
            refs,
            engine=engine,
            target_sources=["RXNORM"],
            max_results_per_code=10,
        )
        equivalents: dict[str, list[dict[str, Any]]] = {}
        for mapping in mappings:
            if mapping.source.code == mapping.target.code:
                continue
            equivalents.setdefault(mapping.source.code, []).append(mapping.to_dict())
        for row in rows:
            row["equivalents"] = equivalents.get(row["code"], [])
    if include_ndc and rows:
        ndcs = engine.get_ndcs_for_rxcuis([str(row["code"]) for row in rows[: min(len(rows), 10)]]) if hasattr(engine, "get_ndcs_for_rxcuis") else {}
        for row in rows:
            row["ndc"] = ndcs.get(str(row["code"]), [])
    payload["include_equivalents"] = include_equivalents
    payload["include_ndc"] = include_ndc
    return payload


def drugs_by_class(
    class_id: str,
    *,
    engine,
    limit: int = 20,
) -> dict[str, Any]:
    """Search ATC/RxNorm class names or class identifiers in UMLS."""
    return terminology_search(
        class_id,
        engine=engine,
        sources=("ATC", "RXNORM"),
        limit=limit,
        query_name="drugs_by_class",
        query_field="class_id",
    )


def drugs_for_indication(
    condition: str,
    *,
    engine,
    limit: int = 20,
    source: str | None = None,
    code: str | None = None,
    relationship_types: Sequence[str] | str | None = None,
    max_depth: int = 5,
    include_product_groups: bool = True,
) -> dict[str, Any]:
    """Return UMLS relationship-backed medications for a condition.

    Uses UMLS Metathesaurus relationships rather than FDA label text. Text input
    is first resolved to diagnosis-oriented ICD-10-CM/SNOMED candidates. Code
    input can be provided with ``source`` and ``code``; if ``source`` is provided
    without ``code``, ``condition`` is interpreted as the code.

    The recursive CTE runs via ``engine.get_drugs_for_indication(...)`` (engine
    protocol method). Engines without that method return a "relationship_mapping_unavailable"
    status with diagnosis context only.
    """
    if not str(condition or "").strip():
        raise ValueError("condition must be a non-empty string.")
    if code is not None and not source:
        raise ValueError("source is required when code is provided.")

    from medterm4ds.services.indications import (
        _INDICATION_TARGET_TTYS,
        format_condition_medication_row,
        validate_indication_relationships,
    )

    relationships = validate_indication_relationships(relationship_types)
    normalized_source = normalize_source(source) if source else None
    input_code = str(code or condition) if normalized_source else None
    diagnosis_limit = min(max(int(limit), 1), 10)
    diagnosis = None if normalized_source else diagnosis_codes(condition, engine=engine, limit=diagnosis_limit)
    status: str
    results: list[dict[str, Any]] = []

    has_indications = hasattr(engine, "get_drugs_for_indication")
    if not has_indications:
        status = "relationship_mapping_unavailable"
    else:
        if normalized_source:
            candidates = [(normalized_source, input_code or str(condition), 1)]
        else:
            candidates = [
                (str(row["source"]), str(row["code"]), index)
                for index, row in enumerate((diagnosis or {}).get("results", [])[:diagnosis_limit], 1)
                if row.get("source") and row.get("code")
            ]
        if not candidates:
            status = "no_condition_candidates"
        else:
            rows = engine.get_drugs_for_indication(
                candidates,
                relationships=relationships,
                max_depth=max_depth,
                limit=limit,
                include_product_groups=include_product_groups,
            )
            results = [format_condition_medication_row(row) for row in rows]
            status = "ok" if results else "no_relationships_found"

    return {
        "query": "drugs_for_indication",
        "condition": condition,
        "source": normalized_source,
        "code": input_code,
        "status": status,
        "relationship_types": list(relationships),
        "target_source": "RXNORM",
        "target_ttys": list(_INDICATION_TARGET_TTYS),
        "max_depth": max_depth,
        "include_product_groups": include_product_groups,
        "result_count": len(results),
        "results": results,
        "diagnosis_context": diagnosis,
    }
def discover(
    source_terminology: str,
    *,
    engine,
    code: str | None = None,
    depth: int = 1,
    include_ancestors: bool = False,
    limit: int = 20,
    include_retired: bool = False,
) -> dict[str, Any]:
    """Browse a source or a code's local hierarchy.

    ``include_retired=True`` includes retired/editorial-suppressed concepts
    as walk targets on the code branch (default active-only).
    """
    source = normalize_source(source_terminology)
    depth = min(max(depth, 1), 5)
    # QC-223/QC-228: validate the caller-facing parameter BEFORE delegating —
    # previously limit=0 leaked ``per_source must be at least 1`` (an internal
    # parameter name) on the no-code branch and was silently ignored on the
    # code branch. QC-217: unbounded limits crashed in the SQL layer.
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if limit > MAX_DISCOVERY_LIMIT:
        raise ValueError(f"limit must be at most {MAX_DISCOVERY_LIMIT} (got {limit})")
    # QC-222: an empty string is never a valid code — previously echoed back
    # as root {'code': ''} with empty descendants.
    if code is not None and not code.strip():
        raise ValueError("code must be a non-empty string")
    if code is None:
        samples = sample_source_codes(engine=engine, sources=[source], per_source=limit)
        infos = get_code_infos(samples, engine=engine)
        rows = [info.to_dict() if info else {"source": ref.source, "code": ref.code} for ref, info in zip(samples, infos, strict=True)]
        return {
            "query": "discover",
            "source": source,
            "code": None,
            "result_count": len(rows),
            "results": rows,
        }

    ref = CodeRef(source, code)
    root = get_code_infos([ref], engine=engine)[0]
    # QC-216 (HIGH): limit was never passed to get_code_relations on the code
    # branch — limit=5 on SNOMED 404684003 (depth=3) returned all 49,696
    # descendants (~18MB) and depth=5 crashed with a temp-storage IOException.
    descendants = get_code_relations(
        [ref],
        engine=engine,
        direction="descendants",
        max_depth=depth,
        limit=limit,
        include_retired=include_retired,
    )
    ancestors = (
        get_code_relations(
            [ref], engine=engine, direction="ancestors", max_depth=depth, limit=limit,
            include_retired=include_retired,
        )
        if include_ancestors
        else []
    )
    return {
        "query": "discover",
        "source": source,
        "code": ref.code,
        "depth": depth,
        "include_ancestors": include_ancestors,
        "root": root.to_dict() if root else {"source": ref.source, "code": ref.code},
        "descendants": [row.to_dict() for row in descendants],
        "ancestors": [row.to_dict() for row in ancestors],
    }


def cross_reference(
    code: str,
    from_source: str,
    *,
    engine,
    to_sources: Sequence[str] | None = None,
    mode: str = "exact",
    max_depth: int = 5,
    max_results_per_code: int = 50,
) -> dict[str, Any]:
    """Map one code to one or more target sources."""
    source = normalize_source(from_source)
    target_sources = tuple(to_sources or _SMART_CROSS_REFERENCE_TARGETS.get(source, ("SNOMEDCT_US", "MSH")))
    normalized_mode = mode.lower().strip()
    # QC-415 (MEDIUM): a typo'd mode previously fell through the membership
    # tests below and silently degraded to exact-mode semantics while being
    # echoed back as if honored. Validate the enumeration like search mode
    # and lookup resolve_mode.
    valid_modes = {"exact", "broader", "narrower", "best", "fallback"}
    if normalized_mode not in valid_modes:
        raise ValueError(
            f"Unknown cross-reference mode: {mode!r}. "
            f"Use one of: {', '.join(sorted(valid_modes))}."
        )
    mapping_depth = max_depth if normalized_mode in {"broader", "best", "fallback"} else 0
    include_descendants = normalized_mode in {"narrower", "best"}
    include_ancestors = normalized_mode in {"broader", "best"}
    rows = get_code_mappings(
        [CodeRef(source, code)],
        engine=engine,
        target_sources=target_sources,
        max_results_per_code=max_results_per_code,
        max_depth=mapping_depth,
        include_target_ancestors=include_ancestors,
        include_target_descendants=include_descendants,
    )
    return {
        "query": "cross_reference",
        "code": str(code),
        "from_source": source,
        "to_sources": [normalize_source(target) for target in target_sources],
        "mode": normalized_mode,
        "max_depth": max_depth,
        "result_count": len(rows),
        "results": [row.to_dict() for row in rows],
    }


def _add_context_relations(
    payload: dict[str, Any],
    *,
    engine,
    descendant_depth: int | None,
    include_ancestors: bool | None,
) -> None:
    refs = [CodeRef(row["source"], row["code"]) for row in payload.get("results", [])[:10]]
    if descendant_depth:
        descendants = get_code_relations(
            refs,
            engine=engine,
            direction="descendants",
            max_depth=min(max(descendant_depth, 1), 3),
        )
        payload["descendants"] = [row.to_dict() for row in descendants]
    if include_ancestors:
        ancestors = get_code_relations(
            refs,
            engine=engine,
            direction="ancestors",
            max_depth=3,
        )
        payload["ancestors"] = [row.to_dict() for row in ancestors]
