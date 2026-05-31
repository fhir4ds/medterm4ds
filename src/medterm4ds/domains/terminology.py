"""Domain-specific terminology helpers built on core services."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from medterm4ds.core.models import CodeRef
from medterm4ds.core.normalize import normalize_source
from medterm4ds.services.discovery import sample_source_codes, search_names
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
        ndcs = _ndcs_for_rxcuis(engine, [str(row["code"]) for row in rows[: min(len(rows), 10)]])
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
) -> dict[str, Any]:
    """Return UMLS-backed indication search context for drug workflows."""
    diagnosis = diagnosis_codes(condition, engine=engine, limit=limit)
    drug_context = search_drug(
        condition,
        engine=engine,
        limit=limit,
        include_equivalents=False,
        include_ndc=False,
    )
    return {
        "query": "drugs_for_indication",
        "condition": condition,
        "status": "terminology_context_only",
        "reason": "UMLS-only mode returns diagnosis and RxNorm name context; use external evidence adapters for label-based indication evidence.",
        "diagnosis_context": diagnosis,
        "drug_name_context": drug_context,
    }


def discover(
    source_terminology: str,
    *,
    engine,
    code: str | None = None,
    depth: int = 1,
    include_ancestors: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    """Browse a source or a code's local hierarchy."""
    source = normalize_source(source_terminology)
    depth = min(max(depth, 1), 5)
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
    descendants = get_code_relations(
        [ref],
        engine=engine,
        direction="descendants",
        max_depth=depth,
    )
    ancestors = (
        get_code_relations([ref], engine=engine, direction="ancestors", max_depth=depth)
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


def _ndcs_for_rxcuis(engine, rxcuis: Sequence[str]) -> dict[str, list[str]]:
    con = getattr(engine, "con", None)
    if con is None or not rxcuis:
        return {}
    try:
        rows = con.execute(
            f"""
            SELECT CODE, ATV
            FROM mrsat
            WHERE SAB = 'RXNORM'
              AND ATN = 'NDC'
              AND CODE IN ({','.join(['?'] * len(rxcuis))})
            ORDER BY CODE, ATV
            """,
            list(rxcuis),
        ).fetchall()
    except Exception:
        return {}
    output: dict[str, list[str]] = {}
    for rxcui, ndc in rows:
        bucket = output.setdefault(str(rxcui), [])
        if len(bucket) < 10:
            bucket.append(str(ndc))
    return output


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
