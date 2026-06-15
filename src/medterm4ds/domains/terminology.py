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

_DEFAULT_INDICATION_RELATIONSHIPS = ("may_treat",)
_ALLOWED_INDICATION_RELATIONSHIPS = {
    "may_treat",
    "may_prevent",
    "may_diagnose",
    "contraindicated_with_disease",
}
_INDICATION_TARGET_TTYS = ("IN", "MIN", "SCDG")


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
    """
    if not str(condition or "").strip():
        raise ValueError("condition must be a non-empty string.")
    if code is not None and not source:
        raise ValueError("source is required when code is provided.")

    con = getattr(engine, "con", None)
    relationships = _indication_relationships(relationship_types)
    normalized_source = normalize_source(source) if source else None
    input_code = str(code or condition) if normalized_source else None
    diagnosis_limit = min(max(int(limit), 1), 10)
    diagnosis = None if normalized_source else diagnosis_codes(condition, engine=engine, limit=diagnosis_limit)
    status: str
    results: list[dict[str, Any]] = []

    if con is None:
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
            rows = _query_condition_medication_relationships(
                con,
                candidates,
                relationships=relationships,
                max_depth=max_depth,
                limit=limit,
                include_product_groups=include_product_groups,
            )
            results = [_condition_medication_row(row) for row in rows]
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


def _indication_relationships(relationship_types: Sequence[str] | str | None) -> tuple[str, ...]:
    if relationship_types is None:
        return _DEFAULT_INDICATION_RELATIONSHIPS
    raw_values = [relationship_types] if isinstance(relationship_types, str) else list(relationship_types)
    relationships = tuple(
        dict.fromkeys(
            value.strip().lower()
            for value in raw_values
            if str(value).strip()
        )
    )
    unsupported = [value for value in relationships if value not in _ALLOWED_INDICATION_RELATIONSHIPS]
    if unsupported:
        raise ValueError(
            "Unsupported indication relationship(s): "
            + ", ".join(unsupported)
            + ". Supported values: "
            + ", ".join(sorted(_ALLOWED_INDICATION_RELATIONSHIPS))
        )
    return relationships or _DEFAULT_INDICATION_RELATIONSHIPS


def _query_condition_medication_relationships(
    con,
    candidates: Sequence[tuple[str, str, int]],
    *,
    relationships: Sequence[str],
    max_depth: int,
    limit: int,
    include_product_groups: bool,
) -> list[tuple[Any, ...]]:
    values_sql = ", ".join(["(?, ?, ?)"] * len(candidates))
    values: list[Any] = []
    for source, code, rank in candidates:
        values.extend([normalize_source(source), str(code), int(rank)])
    relationship_placeholders = ", ".join(["?"] * len(relationships))
    params = [
        *values,
        max(0, min(int(max_depth), 8)),
        *relationships,
        bool(include_product_groups),
        max(1, int(limit)),
    ]
    return con.execute(
        f"""
        WITH RECURSIVE input(source, code, candidate_rank) AS (
            VALUES {values_sql}
        ),
        seed_atoms AS (
            SELECT input.source AS input_source,
                   input.code AS input_code,
                   input.candidate_rank,
                   atom.AUI,
                   atom.CODE AS source_code,
                   atom.STR AS source_name,
                   atom.CUI,
                   0 AS source_depth,
                   CAST(input.source || ':' || input.code AS VARCHAR) AS path,
                   CAST(atom.AUI AS VARCHAR) AS path_auis
            FROM input
            JOIN mrconso atom
              ON atom.SAB = input.source
             AND atom.CODE = input.code
             AND atom.SUPPRESS = 'N'
        ),
        source_walk AS (
            SELECT * FROM seed_atoms
            UNION ALL
            SELECT walk.input_source,
                   walk.input_code,
                   walk.candidate_rank,
                   parent.AUI,
                   parent.CODE AS source_code,
                   parent.STR AS source_name,
                   parent.CUI,
                   walk.source_depth + 1,
                   walk.path || ' -> ' || walk.input_source || ':' || parent.CODE,
                   walk.path_auis || ' -> ' || parent.AUI
            FROM source_walk walk
            JOIN mrrel rel
              ON rel.AUI1 = walk.AUI
             AND rel.REL IN ('PAR', 'RB')
            JOIN mrconso parent
              ON parent.AUI = rel.AUI2
             AND parent.SAB = walk.input_source
             AND parent.SUPPRESS = 'N'
            WHERE walk.source_depth < ?
              AND position(' -> ' || parent.AUI || ' -> ' IN ' -> ' || walk.path_auis || ' -> ') = 0
        ),
        msh_nodes AS (
            SELECT DISTINCT walk.input_source,
                   walk.input_code,
                   walk.candidate_rank,
                   walk.source_code,
                   walk.source_name,
                   walk.source_depth,
                   mesh.AUI AS mesh_aui,
                   mesh.CODE AS mesh_code,
                   mesh.STR AS mesh_name,
                   walk.path || CASE
                       WHEN walk.input_source = 'MSH' AND walk.source_code = mesh.CODE THEN ''
                       ELSE ' -> MSH:' || mesh.CODE
                   END AS condition_path
            FROM source_walk walk
            JOIN mrconso mesh
              ON mesh.CUI = walk.CUI
             AND mesh.SAB = 'MSH'
             AND mesh.TTY = 'MH'
             AND mesh.SUPPRESS = 'N'
        ),
        relationship_edges AS (
            SELECT msh.input_source,
                   msh.input_code,
                   msh.candidate_rank,
                   msh.source_code,
                   msh.source_name,
                   msh.source_depth,
                   msh.mesh_code,
                   msh.mesh_name,
                   msh.condition_path,
                   lower(rel.RELA) AS relationship,
                   rx.AUI AS relationship_rx_aui,
                   rx.CODE AS rx_code,
                   rx.TTY AS rx_tty,
                   rx.STR AS rx_name
            FROM msh_nodes msh
            JOIN mrrel rel
              ON rel.AUI1 = msh.mesh_aui
             AND lower(rel.RELA) IN ({relationship_placeholders})
            JOIN mrconso rx
              ON rx.AUI = rel.AUI2
             AND rx.SAB = 'RXNORM'
             AND rx.SUPPRESS = 'N'
             AND rx.TTY IN ('IN', 'MIN', 'SCDG')
        ),
        nearest_depth AS (
            SELECT input_source,
                   input_code,
                   relationship,
                   MIN(source_depth) AS source_depth
            FROM relationship_edges
            GROUP BY 1, 2, 3
        ),
        nearest_relationship_edges AS (
            SELECT edge.*
            FROM relationship_edges edge
            JOIN nearest_depth nearest
              ON nearest.input_source = edge.input_source
             AND nearest.input_code = edge.input_code
             AND nearest.relationship = edge.relationship
             AND nearest.source_depth = edge.source_depth
        ),
        expanded_edges AS (
            SELECT edge.input_source,
                   edge.input_code,
                   edge.candidate_rank,
                   edge.source_code,
                   edge.source_name,
                   edge.source_depth,
                   edge.mesh_code,
                   edge.mesh_name,
                   edge.condition_path,
                   edge.relationship,
                   edge.rx_code AS relationship_rx_code,
                   edge.rx_tty AS relationship_rx_tty,
                   edge.rx_name AS relationship_rx_name,
                   edge.rx_code AS target_code,
                   edge.rx_tty AS target_tty,
                   edge.rx_name AS target_name,
                   'self' AS target_expansion_relationship,
                   0 AS expansion_rank
            FROM nearest_relationship_edges edge
            UNION ALL
            SELECT edge.input_source,
                   edge.input_code,
                   edge.candidate_rank,
                   edge.source_code,
                   edge.source_name,
                   edge.source_depth,
                   edge.mesh_code,
                   edge.mesh_name,
                   edge.condition_path,
                   edge.relationship,
                   edge.rx_code AS relationship_rx_code,
                   edge.rx_tty AS relationship_rx_tty,
                   edge.rx_name AS relationship_rx_name,
                   target_rx.CODE AS target_code,
                   target_rx.TTY AS target_tty,
                   target_rx.STR AS target_name,
                   lower(expansion.RELA) AS target_expansion_relationship,
                   CASE target_rx.TTY WHEN 'MIN' THEN 1 ELSE 2 END AS expansion_rank
            FROM nearest_relationship_edges edge
            JOIN mrrel expansion
              ON expansion.AUI1 = edge.relationship_rx_aui
             AND lower(expansion.RELA) IN ('has_part', 'has_ingredient')
            JOIN mrconso target_rx
              ON target_rx.AUI = expansion.AUI2
             AND target_rx.SAB = 'RXNORM'
             AND target_rx.SUPPRESS = 'N'
             AND target_rx.TTY IN ('MIN', 'SCDG')
            WHERE ?
        ),
        deduped_edges AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY input_source, input_code, relationship, relationship_rx_code, target_code
                       ORDER BY expansion_rank, target_expansion_relationship, target_name, mesh_code, mesh_name
                   ) AS expansion_rn
            FROM expanded_edges
        ),
        ingredient_counts AS (
            SELECT group_rx.CODE AS group_code,
                   COUNT(DISTINCT ingredient.CODE) AS ingredient_count
            FROM mrconso group_rx
            JOIN mrrel ingredient_rel
              ON ingredient_rel.AUI2 = group_rx.AUI
             AND lower(ingredient_rel.RELA) = 'has_ingredient'
            JOIN mrconso ingredient
              ON ingredient.AUI = ingredient_rel.AUI1
             AND ingredient.SAB = 'RXNORM'
             AND ingredient.SUPPRESS = 'N'
             AND ingredient.TTY = 'IN'
            WHERE group_rx.SAB = 'RXNORM'
              AND group_rx.SUPPRESS = 'N'
              AND group_rx.TTY IN ('SCDG', 'MIN')
              AND group_rx.CODE IN (SELECT target_code FROM deduped_edges WHERE target_tty IN ('SCDG', 'MIN'))
            GROUP BY 1
        )
        SELECT edge.input_source,
               edge.input_code,
               edge.candidate_rank,
               edge.source_code,
               edge.source_name,
               edge.source_depth,
               edge.mesh_code,
               edge.mesh_name,
               edge.relationship,
               edge.relationship_rx_code,
               edge.relationship_rx_tty,
               edge.relationship_rx_name,
               edge.target_code,
               edge.target_tty,
               edge.target_name,
               edge.target_expansion_relationship,
               COALESCE(ingredient_counts.ingredient_count, CASE WHEN edge.target_tty = 'SCDG' THEN 0 ELSE 1 END) AS ingredient_count,
               edge.condition_path || ' -> ' || edge.relationship || ' -> RXNORM:' || edge.relationship_rx_code
                   || CASE
                       WHEN edge.target_expansion_relationship = 'self' THEN ''
                       ELSE ' -> ' || edge.target_expansion_relationship || ' -> RXNORM:' || edge.target_code
                   END AS path
        FROM deduped_edges edge
        LEFT JOIN ingredient_counts
          ON ingredient_counts.group_code = edge.target_code
        WHERE edge.expansion_rn = 1
        ORDER BY edge.candidate_rank,
                 edge.source_depth,
                 CASE edge.relationship
                     WHEN 'may_treat' THEN 0
                     WHEN 'may_prevent' THEN 1
                     WHEN 'may_diagnose' THEN 2
                     ELSE 3
                 END,
                 edge.relationship_rx_name,
                 edge.expansion_rank,
                 CASE edge.target_tty WHEN 'IN' THEN 0 WHEN 'MIN' THEN 1 ELSE 2 END,
                 edge.target_name,
                 edge.relationship_rx_code,
                 edge.target_code,
                 edge.mesh_code,
                 edge.mesh_name
        LIMIT ?
        """,
        params,
    ).fetchall()


def _condition_medication_row(row: tuple[Any, ...]) -> dict[str, Any]:
    (
        input_source,
        input_code,
        candidate_rank,
        source_code,
        source_name,
        source_depth,
        mesh_code,
        mesh_name,
        relationship,
        relationship_rx_code,
        relationship_rx_tty,
        relationship_rx_name,
        target_code,
        target_tty,
        target_name,
        target_expansion_relationship,
        ingredient_count,
        path,
    ) = row
    is_self_target = target_expansion_relationship == "self"
    return {
        "source": input_source,
        "code": input_code,
        "candidate_rank": candidate_rank,
        "matched_condition_source": input_source,
        "matched_condition_code": source_code,
        "matched_condition_name": source_name,
        "match_depth": source_depth,
        "relationship_source": "MSH",
        "relationship_source_code": mesh_code,
        "relationship_source_name": mesh_name,
        "relationship": relationship,
        "relationship_target_source": "RXNORM",
        "relationship_target_code": relationship_rx_code,
        "relationship_target_tty": relationship_rx_tty,
        "relationship_target_name": relationship_rx_name,
        "target_source": "RXNORM",
        "target_code": target_code,
        "target_tty": target_tty,
        "target_name": target_name,
        "target_expansion_relationship": target_expansion_relationship,
        "target_is_relationship_target": is_self_target,
        "ingredient_count": ingredient_count,
        "is_single_ingredient": ingredient_count == 1,
        "path": path.split(" -> "),
        "path_text": path,
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
