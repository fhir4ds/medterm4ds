"""Non-RxNorm patient-friendly resolution using prepared mt4ds tables.

Handles ICD10CM, ICD10PCS, HCPCS, CPT, LOINC, SNOMEDCT_US, and CVX sources.
Each source uses its own workflow based on prepared tables (best_atoms,
walk_edges, friendly_atoms, crosswalk_edges/same_cui_edges, snomed_top_level_depth,
patient_friendly_strategy, cvx_metadata).

This module is Phase 6 of the terminology normalization refactor.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence

from medterm4ds.core.models import (
    CodeRef,
    FriendlyNameResult,
    Provenance,
    ProvenanceStep,
)
from medterm4ds.services.prepared_primitives import (
    same_cui_crosswalk_sql as _same_cui_crosswalk_sql,
)
from medterm4ds.services.prepared_primitives import (
    walk_closure_table as _walk_closure_table,
)
from medterm4ds.services.rxnorm_tty_walk import get_rxnorm_patient_friendly
from medterm4ds.services.selection import is_combo_name_mismatch
from medterm4ds.sources.snomed import (
    SNOMED_TARGET_PRIORITY,
    SNOMED_TOP_LEVEL_GUARD_DEPTH,
)

logger = logging.getLogger(__name__)

_STRATEGY = "non_rxnorm_prepared"

# Maximum depth for native parent walks
_MAX_WALK_DEPTH = 5

# Sources that use hierarchy walk -> friendly candidate workflow
_HIERARCHY_SOURCES = frozenset({"ICD10CM", "ICD10PCS", "HCPCS", "CPT"})

# SNOMED target routing priority (from snomed.py)
_SNOMED_TARGET_ORDER = ("RXNORM",) + tuple(
    source
    for source in sorted(SNOMED_TARGET_PRIORITY.keys(), key=lambda s: SNOMED_TARGET_PRIORITY[s])
    if source != "RXNORM"
)

# Friendly atom sources in preference order (MEDLINEPLUS first, CHV second)
_FRIENDLY_SOURCES_PREFERRED = ("MEDLINEPLUS", "CHV")

_CPT_ALWAYS_BLOCK_FRIENDLY_NAMES = frozenset({
    "biochemical test",
    "cpt4",
    "current procedural terminology",
    "current procedural terminology (cpt)",
    "current procedural terminology concept",
})

_CPT_GENERIC_FRIENDLY_CUIS = frozenset({
    "C0022885",  # laboratory test
    "C0038894",  # Surgery
    "C0086143",  # diagnostic test
    "C0201682",  # chemical test
    "C0430027",  # biochemical test
    "C0543467",  # surgery / operation / surgical treatment
    "C0677612",  # operative procedure
    "C1138431",  # Current Procedural Terminology Concept
})

_CPT_DEEP_GENERIC_FRIENDLY_NAMES = frozenset({
    "operational procedures",
    "operation",
    "operations",
    "operative procedure",
    "operative procedures",
    "procedure operative",
    "procedure surgical",
    "procedures operative",
    "procedures surgical",
    "surgeries",
    "surgery",
    "surgery procedure",
    "surgical procedure",
    "surgical procedures",
})


def _snomed_target_priority(source: str) -> int:
    if source == "RXNORM":
        return -1
    return SNOMED_TARGET_PRIORITY[source]


def _friendly_tty_order_sql(expr: str = "tty") -> str:
    """Prefer preferred patient-friendly atoms before synonyms for the same CUI."""
    return f"""CASE upper({expr})
        WHEN 'PT' THEN 0
        WHEN 'MH' THEN 1
        WHEN 'HT' THEN 2
        WHEN 'SY' THEN 3
        ELSE 9
    END"""


def _sql_literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _sql_literal_list(values: frozenset[str]) -> str:
    return ", ".join(_sql_literal(value) for value in sorted(values))


def get_non_rxnorm_patient_friendly(
    codes: Sequence[CodeRef],
    con,
    *,
    max_depth: int = _MAX_WALK_DEPTH,
) -> list[FriendlyNameResult]:
    """Resolve patient-friendly names for non-RxNorm sources using prepared tables.

    Parameters
    ----------
    codes : Sequence[CodeRef]
        Non-RxNorm codes to resolve.  All must have source != 'RXNORM'.
    con : duckdb.DuckDBPyConnection
        Open DuckDB connection with mt4ds schema populated.

    Returns
    -------
    list[FriendlyNameResult]
        One result per input code, preserving input order.
    """
    if not codes:
        return []

    # Group by source for batch processing
    by_source: dict[str, list[tuple[int, CodeRef]]] = defaultdict(list)
    for i, code_ref in enumerate(codes):
        by_source[code_ref.source].append((i, code_ref))

    # Process each source group and collect results keyed by original index
    result_map: dict[int, FriendlyNameResult] = {}

    for source, items in by_source.items():
        source_codes = [cr for _, cr in items]
        if source in _HIERARCHY_SOURCES:
            source_results = _resolve_hierarchy_sources(
                source,
                source_codes,
                con,
                max_depth=max_depth,
            )
        elif source == "LNC":
            source_results = _resolve_loinc(source_codes, con, max_depth=max_depth)
        elif source == "SNOMEDCT_US":
            source_results = _resolve_snomed(source_codes, con, max_depth=max_depth)
        elif source == "CVX":
            source_results = _resolve_cvx(source_codes, con)
        else:
            # Unknown source: return original display for each
            source_results = [
                _make_original(cr, _STRATEGY) for cr in source_codes
            ]

        for (orig_idx, _), result in zip(items, source_results, strict=True):
            result_map[orig_idx] = result

    # Return results in input order
    return [result_map[i] for i in range(len(codes))]


# ---------------------------------------------------------------------------
# ICD10CM / ICD10PCS / HCPCS / CPT -- hierarchy walk with friendly candidates
# ---------------------------------------------------------------------------

def _resolve_hierarchy_sources(
    source: str,
    codes: Sequence[CodeRef],
    con,
    *,
    max_depth: int = _MAX_WALK_DEPTH,
) -> list[FriendlyNameResult]:
    """Resolve patient-friendly names for hierarchy-based sources.

    Workflow:
    1. Lookup input in best_atoms
    2. Walk native parents via walk_edges (bounded depth)
    3. At each depth frontier, find MEDLINEPLUS/CHV in friendly_atoms (is_broad=false)
    4. Prefer MEDLINEPLUS over CHV at same depth
    5. If native walk misses, SNOMED fallback by walking source ancestors via
       walk_edges, then mapping each ancestor through crosswalk_edges -> SNOMED
       with same_cui_edges fallback for older prepared DBs
    6. If still miss, return original display
    """
    if not codes:
        return []
    walk_depth = max(0, int(max_depth))
    cpt_always_block_names_sql = _sql_literal_list(_CPT_ALWAYS_BLOCK_FRIENDLY_NAMES)
    cpt_generic_cuis_sql = _sql_literal_list(_CPT_GENERIC_FRIENDLY_CUIS)
    cpt_deep_generic_names_sql = _sql_literal_list(_CPT_DEEP_GENERIC_FRIENDLY_NAMES)

    input_values = ",\n    ".join(
        f"({_sql_literal(c.code)}, {_sql_literal(source)}, {i})" for i, c in enumerate(codes)
    )

    closure_table = _walk_closure_table(con, walk_depth)
    if closure_table:
        native_walk_cte = f"""
    native_walk(input_order, source, code, aui, cui, tty, depth) AS (
        SELECT input_order, source, code, aui, cui, tty, 0
        FROM lookup
        WHERE aui IS NOT NULL
        UNION ALL
        SELECT l.input_order, l.source, c.to_code, c.to_aui,
               c.to_cui, c.to_tty, c.depth
        FROM lookup l
        JOIN {closure_table} c
          ON c.source = l.source
         AND c.from_aui = l.aui
         AND c.depth <= {walk_depth}
        WHERE l.aui IS NOT NULL
    ),"""
    else:
        native_walk_cte = f"""
    native_walk(input_order, source, code, aui, cui, tty, depth) AS (
        SELECT input_order, source, code, aui, cui, tty, 0
        FROM lookup
        WHERE aui IS NOT NULL
        UNION ALL
        SELECT w.input_order, w.source, e.to_code, e.to_aui,
               e.to_cui, e.to_tty, w.depth + 1
        FROM native_walk w
        JOIN mt4ds.walk_edges e
          ON e.source = w.source AND e.from_aui = w.aui AND e.direction = 'parent'
        WHERE w.depth < {walk_depth}
    ),"""

    query = f"""
    WITH RECURSIVE
    input_codes(code, source, input_order) AS (
        VALUES {input_values}
    ),
    display_lookup AS (
        SELECT i.input_order, i.source, i.code,
               a.name AS technical_name
        FROM input_codes i
        LEFT JOIN mt4ds.best_atoms a
          ON a.source = i.source AND a.code = i.code AND a.rank = 1
    ),
    anchor_candidates AS (
        SELECT i.input_order, i.source, i.code,
               a.aui, a.cui, a.tty,
               ROW_NUMBER() OVER (
                   PARTITION BY i.input_order
                   ORDER BY
                       CASE
                           WHEN i.source = 'CPT' THEN
                               CASE upper(a.tty)
                                   WHEN 'PT' THEN 0
                                   WHEN 'HT' THEN 1
                                   WHEN 'ETCLIN' THEN 2
                                   WHEN 'ETCF' THEN 3
                                   WHEN 'SY' THEN 4
                                   ELSE 9
                               END
                           ELSE COALESCE(a.rank, 999)
                       END,
                       COALESCE(a.rank, 999),
                       a.aui
               ) AS rn
        FROM input_codes i
        LEFT JOIN mt4ds.best_atoms a
          ON a.source = i.source AND a.code = i.code
    ),
    lookup AS (
        SELECT d.input_order, d.source, d.code,
               a.aui, a.cui, a.tty, d.technical_name
        FROM display_lookup d
        LEFT JOIN anchor_candidates a
          ON a.input_order = d.input_order AND a.rn = 1
    ),
    {native_walk_cte}
    friendly_hits AS (
        SELECT w.input_order, w.depth, f.name, f.friendly_source, f.tty
        FROM native_walk w
        JOIN mt4ds.friendly_atoms f ON f.cui = w.cui
        WHERE f.is_broad = false
          AND NOT (
              w.source = 'CPT'
              AND (
                  f.cui IN ({cpt_generic_cuis_sql})
                  OR
                  lower(f.name) IN ({cpt_always_block_names_sql})
                  OR (
                      w.depth >= 4
                      AND lower(f.name) IN ({cpt_deep_generic_names_sql})
                  )
              )
          )
    ),
    ranked AS (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY input_order
            ORDER BY depth,
                CASE friendly_source
                    WHEN 'MEDLINEPLUS' THEN 0 ELSE 1
                END,
                {_friendly_tty_order_sql("tty")},
                name
        ) AS rn
        FROM friendly_hits
    )
    SELECT l.input_order, l.source, l.code, l.technical_name,
           r.name, r.friendly_source, r.depth AS match_depth
    FROM lookup l
    LEFT JOIN ranked r ON r.input_order = l.input_order AND r.rn = 1
    ORDER BY l.input_order
    """

    try:
        rows = con.execute(query).fetchall()
    except Exception:
        logger.exception("Hierarchy walk query failed for source %s", source)
        return [_make_original(c, _STRATEGY) for c in codes]

    # Check which codes need SNOMED fallback
    needs_fallback: dict[int, CodeRef] = {}
    base_info: dict[int, tuple[str, str]] = {}

    for row in rows:
        idx = int(row[0])
        code = str(row[2])
        tech_name = row[3]
        friendly_name = row[4]
        friendly_src = row[5]
        match_depth = row[6]

        code_ref = codes[idx]
        base_info[idx] = (code, str(tech_name) if tech_name else code)

    # Build results, noting which ones need SNOMED fallback
    results: list[FriendlyNameResult | None] = [None] * len(codes)

    for row in rows:
        idx = int(row[0])
        tech_name = row[3]
        friendly_name = row[4]
        friendly_src = row[5]
        match_depth = row[6]

        code_ref = codes[idx]
        if friendly_name and friendly_src:
            # Found a friendly candidate in native hierarchy walk
            resolved_match_depth = int(match_depth or 0)
            resolved_match_type = "exact" if resolved_match_depth == 0 else "broader"
            resolved_op = "exact_same_cui" if resolved_match_depth == 0 else "native_walk"
            results[idx] = FriendlyNameResult(
                code=code_ref,
                name=str(friendly_name),
                friendly_source=str(friendly_src),
                match_type=resolved_match_type,
                match_depth=resolved_match_depth,
                technical_name=str(tech_name) if tech_name else None,
                matched_via=Provenance.from_steps(
                    _STRATEGY,
                    [
                        ProvenanceStep(
                            op="input",
                            source=code_ref.source,
                            code=code_ref.code,
                        ),
                        ProvenanceStep(
                            op=resolved_op,
                            source=code_ref.source,
                            code=code_ref.code,
                            target_source=str(friendly_src),
                            depth=resolved_match_depth,
                            name=str(friendly_name),
                        ),
                    ],
                ),
            )
        else:
            # No friendly candidate found -- try SNOMED fallback
            needs_fallback[idx] = code_ref

    # SNOMED fallback for unresolved codes
    if needs_fallback:
        fallback_results = _snomed_fallback(needs_fallback, base_info, con, max_depth=walk_depth)
        for idx, result in fallback_results.items():
            results[idx] = result

    # Fill any remaining Nones with original display
    for i, r in enumerate(results):
        if r is None:
            _, tech_name = base_info.get(i, (codes[i].code, codes[i].code))
            results[i] = _make_original(
                codes[i], _STRATEGY, technical_name=tech_name
            )

    return results  # type: ignore[return-value]


def _snomed_fallback(
    needs_fallback: dict[int, CodeRef],
    base_info: dict[int, tuple[str, str]],
    con,
    *,
    max_depth: int = _MAX_WALK_DEPTH,
) -> dict[int, FriendlyNameResult]:
    """Attempt SNOMED fallback for unresolved source codes.

    Strategy:
    1) Walk source ancestors via mt4ds.walk_edges (parent direction).
    2) Crosswalk each walked source code through crosswalk_edges to SNOMED,
       with same_cui_edges fallback for older prepared DBs.
    3) Walk guarded SNOMED ancestors for MEDLINEPLUS/CHV candidates.
    4) Prefer shallower matches and MEDLINEPLUS over CHV.
    """
    if not needs_fallback:
        return {}
    walk_depth = max(0, int(max_depth))
    crosswalk_table, crosswalk_filter = _same_cui_crosswalk_sql(con)
    cpt_always_block_names_sql = _sql_literal_list(_CPT_ALWAYS_BLOCK_FRIENDLY_NAMES)
    cpt_generic_cuis_sql = _sql_literal_list(_CPT_GENERIC_FRIENDLY_CUIS)
    cpt_deep_generic_names_sql = _sql_literal_list(_CPT_DEEP_GENERIC_FRIENDLY_NAMES)

    # Build input values for fallback query
    items = list(needs_fallback.items())
    input_values = ",\n    ".join(
        f"({_sql_literal(cr.code)}, {_sql_literal(cr.source)}, {idx})" for idx, cr in items
    )

    closure_table = _walk_closure_table(con, walk_depth)
    if closure_table:
        native_walk_cte = f"""
    native_walk(input_order, source, source_code, source_depth, aui, cui, tty, depth) AS (
        SELECT l.input_order, l.source, l.code, 0 AS source_depth, l.aui, l.cui, l.tty, 0
        FROM lookup l
        WHERE l.aui IS NOT NULL
        UNION ALL
        SELECT l.input_order, l.source, c.to_code, c.depth AS source_depth, c.to_aui,
               c.to_cui, c.to_tty, c.depth AS depth
        FROM lookup l
        JOIN {closure_table} c
          ON c.source = l.source
         AND c.from_aui = l.aui
         AND c.depth <= {walk_depth}
        WHERE l.aui IS NOT NULL
    ),"""
        snomed_walk_cte = f"""
    snomed_walk(
        input_order, source, source_code, source_depth, snomed_code, walk_code, aui, cui, snomed_depth
    ) AS (
        SELECT input_order, source, source_code, source_depth, snomed_code, snomed_code, aui, cui, 0
        FROM snomed_base
        UNION ALL
        SELECT b.input_order, b.source, b.source_code, b.source_depth,
               b.snomed_code, c.to_code, c.to_aui, c.to_cui, c.depth AS snomed_depth
        FROM snomed_base b
        JOIN {closure_table} c
          ON c.source = 'SNOMEDCT_US'
         AND c.from_aui = b.aui
         AND c.depth <= {walk_depth}
    ),"""
    else:
        native_walk_cte = f"""
    native_walk(input_order, source, source_code, source_depth, aui, cui, tty, depth) AS (
        SELECT l.input_order, l.source, l.code, 0 AS source_depth, l.aui, l.cui, l.tty, 0
        FROM lookup l
        WHERE l.aui IS NOT NULL
        UNION ALL
        SELECT w.input_order, w.source, e.to_code, w.source_depth + 1, e.to_aui,
               e.to_cui, e.to_tty, w.depth + 1
        FROM native_walk w
        JOIN mt4ds.walk_edges e
          ON e.source = w.source AND e.from_aui = w.aui AND e.direction = 'parent'
        WHERE w.depth < {walk_depth}
    ),"""
        snomed_walk_cte = f"""
    snomed_walk(
        input_order, source, source_code, source_depth, snomed_code, walk_code, aui, cui, snomed_depth
    ) AS (
        SELECT input_order, source, source_code, source_depth, snomed_code, snomed_code, aui, cui, 0
        FROM snomed_base
        UNION ALL
        SELECT w.input_order, w.source, w.source_code, w.source_depth,
               w.snomed_code, e.to_code, e.to_aui, e.to_cui, w.snomed_depth + 1
        FROM snomed_walk w
        JOIN mt4ds.walk_edges e
          ON e.source = 'SNOMEDCT_US' AND e.from_aui = w.aui AND e.direction = 'parent'
        WHERE w.snomed_depth < {walk_depth}
    ),"""

    query = f"""
    WITH RECURSIVE
    input_codes(code, source, input_order) AS (
        VALUES {input_values}
    ),
    lookup AS (
        SELECT i.input_order, i.source, i.code, a.aui, a.cui, a.tty
        FROM input_codes i
        LEFT JOIN mt4ds.best_atoms a
          ON a.source = i.source AND a.code = i.code AND a.rank = 1
    ),
    {native_walk_cte}
    snomed_crosswalk AS (
        SELECT w.input_order, w.source, w.source_code,
               w.source_depth, sce.target_code AS snomed_code
        FROM native_walk w
        JOIN {crosswalk_table} sce
          ON sce.source = w.source
         AND sce.code = w.source_code
         AND sce.target_source = 'SNOMEDCT_US'
         {crosswalk_filter}
    ),
    snomed_base AS (
        SELECT DISTINCT s.input_order, s.source, s.source_code, s.source_depth,
               s.snomed_code, ba.aui, ba.cui
        FROM snomed_crosswalk s
        JOIN mt4ds.best_atoms ba
          ON ba.source = 'SNOMEDCT_US' AND ba.code = s.snomed_code AND ba.rank = 1
    ),
    {snomed_walk_cte}
    guarded_walk AS (
        SELECT w.*
        FROM snomed_walk w
        LEFT JOIN mt4ds.snomed_top_level_depth tld ON tld.code = w.walk_code
        WHERE tld.code IS NULL OR tld.min_top_depth > {SNOMED_TOP_LEVEL_GUARD_DEPTH}
    ),
    friendly_hits AS (
        SELECT w.input_order, w.source_code, w.source_depth, w.snomed_code,
               w.snomed_depth, w.source_depth + w.snomed_depth AS match_depth,
               f.name, f.friendly_source, f.tty
        FROM guarded_walk w
        JOIN mt4ds.friendly_atoms f ON f.cui = w.cui
        WHERE f.is_broad = false
          AND f.source IN ('MEDLINEPLUS', 'CHV')
          AND NOT (
              w.source = 'CPT'
              AND (
                  f.cui IN ({cpt_generic_cuis_sql})
                  OR
                  lower(f.name) IN ({cpt_always_block_names_sql})
                  OR (
                      w.source_depth + w.snomed_depth >= 4
                      AND lower(f.name) IN ({cpt_deep_generic_names_sql})
                  )
              )
          )
    )
    SELECT input_order, source_code, source_depth, snomed_code,
           name, friendly_source, match_depth
        FROM friendly_hits
        ORDER BY input_order, match_depth,
                 CASE friendly_source WHEN 'MEDLINEPLUS' THEN 0 ELSE 1 END,
                 {_friendly_tty_order_sql("tty")},
                 name
        """

    try:
        rows = con.execute(query).fetchall()
    except Exception:
        logger.exception("SNOMED fallback query failed")
        return {}

    fallback_map: dict[int, FriendlyNameResult] = {}
    for row in rows:
        idx = int(row[0])
        if idx in fallback_map:
            continue
        source_code = str(row[1]) if row[1] is not None else needs_fallback[idx].code
        source_depth = int(row[2] or 0)
        snomed_code = str(row[3]) if row[3] is not None else None
        friendly_name = str(row[4])
        friendly_src = str(row[5])
        match_depth = int(row[6] or 0)
        code_ref = needs_fallback[idx]
        _, tech_name = base_info.get(idx, (code_ref.code, code_ref.code))
        if friendly_src == "CHV" and is_combo_name_mismatch(tech_name, friendly_name):
            continue

        fallback_map[idx] = FriendlyNameResult(
            code=code_ref,
            name=friendly_name,
            friendly_source=friendly_src,
            match_type="snomed_fallback",
            match_depth=match_depth,
            technical_name=tech_name,
            matched_via=Provenance.from_steps(
                _STRATEGY,
                [
                    ProvenanceStep(
                        op="input",
                        source=code_ref.source,
                        code=code_ref.code,
                    ),
                    ProvenanceStep(
                        op="snomed_crosswalk",
                        source=code_ref.source,
                        code=source_code,
                        depth=source_depth,
                        target_source="SNOMEDCT_US",
                        target_code=snomed_code,
                        mode="broader",
                    ),
                    ProvenanceStep(
                        op="snomed_walk",
                        source="SNOMEDCT_US",
                        code=snomed_code,
                        target_source=friendly_src,
                        depth=match_depth,
                        name=friendly_name,
                    ),
                ],
            ),
        )

    return fallback_map


# ---------------------------------------------------------------------------
# LOINC -- component/axis/common-name tiers
# ---------------------------------------------------------------------------

def _resolve_loinc(
    codes: Sequence[CodeRef],
    con,
    *,
    max_depth: int = _MAX_WALK_DEPTH,
) -> list[FriendlyNameResult]:
    """Resolve patient-friendly names for LOINC codes.

    Workflow:
    1. Component/axis/common-name tiers from patient_friendly_strategy
    2. Native parent walk with MEDLINEPLUS/CHV candidates
    3. SNOMED fallback when native tiers miss
    4. Original display fallback
    """
    if not codes:
        return []
    walk_depth = max(0, int(max_depth))

    input_values = ",\n    ".join(
        f"({_sql_literal(c.code)}, {i})" for i, c in enumerate(codes)
    )

    # Phase 1: Try tiered resolution using patient_friendly_strategy
    # This covers first_axis, component, native parent walk, and common_name
    query = f"""
    WITH RECURSIVE
    input_codes(code, input_order) AS (
        VALUES {input_values}
    ),
    lookup AS (
        SELECT i.input_order, i.code,
               a.aui, a.cui, a.tty, a.name AS technical_name
        FROM input_codes i
        LEFT JOIN mt4ds.best_atoms a
          ON a.source = 'LNC' AND a.code = i.code AND a.rank = 1
    ),
    -- Get components from main.mrrel
    components AS (
        SELECT l.input_order, c.aui AS comp_aui, c.cui AS comp_cui, c.name AS comp_name
        FROM lookup l
        JOIN main.mrrel r ON r.AUI2 = l.aui AND r.RELA = 'has_component'
        JOIN mt4ds.atoms c ON c.aui = r.AUI1 AND c.source = 'LNC'
    ),
    -- Tier 0: first_axis -- longest component name
    first_axis_hits AS (
        SELECT input_order, 1 AS depth, comp_name AS name, 'LNC' AS friendly_source,
               'PT' AS tty, 'first_axis' AS match_type, 0 AS strategy_order
        FROM components
    ),
    -- Tier 1: component -- MEDLINEPLUS/CHV hits on component CUIs
    component_hits AS (
        SELECT c.input_order, 1 AS depth, f.name, f.friendly_source,
               f.tty, 'component' AS match_type,
               CASE f.friendly_source WHEN 'MEDLINEPLUS' THEN 1 ELSE 2 END AS strategy_order
        FROM components c
        JOIN mt4ds.friendly_atoms f ON f.cui = c.comp_cui
        WHERE f.is_broad = false
          AND f.source IN ('MEDLINEPLUS', 'CHV')
    ),
    -- Tier 2: native parent walk with friendly candidates
    native_walk(input_order, code, aui, cui, tty, depth) AS (
        SELECT input_order, code, aui, cui, tty, 0
        FROM lookup
        WHERE aui IS NOT NULL
        UNION ALL
        SELECT w.input_order, e.to_code, e.to_aui, e.to_cui, e.to_tty, w.depth + 1
        FROM native_walk w
        JOIN mt4ds.walk_edges e
          ON e.source = 'LNC' AND e.from_aui = w.aui AND e.direction = 'parent'
        WHERE w.depth < {walk_depth}
    ),
    native_hits AS (
        SELECT w.input_order, w.depth, f.name, f.friendly_source,
               f.tty, 'broader' AS match_type,
               CASE f.friendly_source WHEN 'MEDLINEPLUS' THEN 3 ELSE 4 END AS strategy_order
        FROM native_walk w
        JOIN mt4ds.friendly_atoms f ON f.cui = w.cui
        WHERE f.is_broad = false
          AND f.source IN ('MEDLINEPLUS', 'CHV')
    ),
    -- Combined and ranked
    all_hits AS (
        SELECT * FROM first_axis_hits
        UNION ALL
        SELECT * FROM component_hits
        UNION ALL
        SELECT * FROM native_hits
    ),
    ranked AS (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY input_order
            ORDER BY
                strategy_order,
                depth,
                {_friendly_tty_order_sql("tty")},
                length(name) DESC,
                name
        ) AS rn
        FROM all_hits
    )
    SELECT l.input_order, l.code, l.technical_name,
           r.name, r.friendly_source, r.match_type, r.depth AS match_depth
    FROM lookup l
    LEFT JOIN ranked r ON r.input_order = l.input_order AND r.rn = 1
    ORDER BY l.input_order
    """

    try:
        rows = con.execute(query).fetchall()
    except Exception:
        logger.exception("LOINC resolution query failed")
        return [_make_original(c, _STRATEGY) for c in codes]

    # Check which codes need SNOMED fallback
    needs_fallback: dict[int, CodeRef] = {}
    base_info: dict[int, tuple[str, str]] = {}
    results: list[FriendlyNameResult | None] = [None] * len(codes)

    for row in rows:
        idx = int(row[0])
        code = str(row[1])
        tech_name = row[2]
        base_info[idx] = (code, str(tech_name) if tech_name else code)

    for row in rows:
        idx = int(row[0])
        tech_name = row[2]
        friendly_name = row[3]
        friendly_src = row[4]
        match_type = row[5]
        match_depth = row[6]

        code_ref = codes[idx]
        if friendly_name and friendly_src:
            results[idx] = FriendlyNameResult(
                code=code_ref,
                name=str(friendly_name),
                friendly_source=str(friendly_src),
                match_type=str(match_type),
                match_depth=int(match_depth or 0),
                technical_name=str(tech_name) if tech_name else None,
                matched_via=Provenance.from_steps(
                    _STRATEGY,
                    [
                        ProvenanceStep(
                            op="input",
                            source="LNC",
                            code=code_ref.code,
                        ),
                        ProvenanceStep(
                            op=str(match_type),
                            source="LNC",
                            code=code_ref.code,
                            target_source=str(friendly_src),
                            depth=int(match_depth or 0),
                            name=str(friendly_name),
                        ),
                    ],
                ),
            )
        else:
            needs_fallback[idx] = code_ref

    # SNOMED fallback for unresolved LOINC codes
    if needs_fallback:
        fallback_results = _snomed_fallback(needs_fallback, base_info, con, max_depth=walk_depth)
        for idx, result in fallback_results.items():
            results[idx] = result

    # Fill remaining Nones with original display
    for i, r in enumerate(results):
        if r is None:
            _, tech_name = base_info.get(i, (codes[i].code, codes[i].code))
            results[i] = _make_original(
                codes[i], _STRATEGY, technical_name=tech_name
            )

    return results  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# SNOMED -- exact target routing + guarded hierarchy walk
# ---------------------------------------------------------------------------

def _resolve_snomed(
    codes: Sequence[CodeRef],
    con,
    *,
    max_depth: int = _MAX_WALK_DEPTH,
) -> list[FriendlyNameResult]:
    """Resolve patient-friendly names for SNOMED codes via target routing.

    Workflow:
    1. Crosswalk to target sources in priority order:
       RXNORM first for drug/product concepts, then ICD10CM, ICD10PCS, LNC, CPT, HCPCS.
    2. Walk each target source hierarchy first.
    3. Allow each target route to enter guarded SNOMED fallback at most once.
    4. If no target route resolves, walk direct guarded SNOMED hierarchy.
    5. Original display fallback.
    """
    if not codes:
        return []
    walk_depth = max(0, int(max_depth))

    input_values = ",\n    ".join(
        f"({_sql_literal(c.code)}, {i})" for i, c in enumerate(codes)
    )

    base_info: dict[int, tuple[str, str]] = {}
    lookup_query = f"""
    WITH input_codes(code, input_order) AS (
        VALUES {input_values}
    )
    SELECT i.input_order, i.code,
           a.name AS technical_name
    FROM input_codes i
    LEFT JOIN mt4ds.best_atoms a
      ON a.source = 'SNOMEDCT_US' AND a.code = i.code AND a.rank = 1
    ORDER BY i.input_order
    """
    try:
        lookup_rows = con.execute(lookup_query).fetchall()
        for row in lookup_rows:
            idx = int(row[0])
            tech_name = str(row[2]) if row[2] else str(row[1])
            base_info[idx] = (str(row[1]), tech_name)
    except Exception:
        logger.exception("SNOMED lookup query failed")
        for i, cr in enumerate(codes):
            base_info[i] = (cr.code, cr.code)

    target_case_parts = [
        f"WHEN '{target_source}' THEN {_snomed_target_priority(target_source)}"
        for target_source in _SNOMED_TARGET_ORDER
    ]
    target_case_sql = "CASE target_source " + " ".join(target_case_parts) + " END"
    crosswalk_table, crosswalk_filter = _same_cui_crosswalk_sql(con)
    closure_table = _walk_closure_table(con, walk_depth)
    if closure_table:
        snomed_walk_cte = f"""
    snomed_walk(input_order, code, technical_name, walk_code, aui, depth) AS (
        SELECT input_order, code, technical_name, code, aui, 0
        FROM lookup
        WHERE aui IS NOT NULL
        UNION ALL
        SELECT l.input_order, l.code, l.technical_name, c.to_code, c.to_aui, c.depth
        FROM lookup l
        JOIN {closure_table} c
          ON c.source = 'SNOMEDCT_US'
         AND c.from_aui = l.aui
         AND c.depth <= {walk_depth}
        WHERE l.aui IS NOT NULL
    ),"""
    else:
        snomed_walk_cte = f"""
    snomed_walk(input_order, code, technical_name, walk_code, aui, depth) AS (
        SELECT input_order, code, technical_name, code, aui, 0
        FROM lookup
        WHERE aui IS NOT NULL
        UNION ALL
        SELECT w.input_order, w.code, w.technical_name, e.to_code, e.to_aui, w.depth + 1
        FROM snomed_walk w
        JOIN mt4ds.walk_edges e
          ON e.source = 'SNOMEDCT_US'
         AND e.from_aui = w.aui
         AND e.direction = 'parent'
        WHERE w.depth < {walk_depth}
    ),"""

    crosswalk_query = f"""
    WITH RECURSIVE
    input_codes(code, input_order) AS (
        VALUES {input_values}
    ),
    lookup AS (
        SELECT i.input_order, i.code,
               a.name AS technical_name,
               a.aui
        FROM input_codes i
        LEFT JOIN mt4ds.best_atoms a
          ON a.source = 'SNOMEDCT_US' AND a.code = i.code AND a.rank = 1
    ),
    {snomed_walk_cte}
    crosswalk AS (
        SELECT l.input_order, l.code, l.technical_name,
               l.code AS route_source_code,
               0 AS source_depth,
               sce.target_source, sce.target_code
        FROM lookup l
        JOIN {crosswalk_table} sce
          ON sce.source = 'SNOMEDCT_US' AND sce.code = l.code
         AND sce.target_source IN ({', '.join(f"'{s}'" for s in _SNOMED_TARGET_ORDER)})
         {crosswalk_filter}
        UNION ALL
        SELECT w.input_order, w.code, w.technical_name,
               w.walk_code AS route_source_code,
               w.depth AS source_depth,
               sce.target_source, sce.target_code
        FROM snomed_walk w
        JOIN {crosswalk_table} sce
          ON sce.source = 'SNOMEDCT_US' AND sce.code = w.walk_code
         AND sce.target_source = 'RXNORM'
         {crosswalk_filter}
        WHERE w.depth > 0
    )
    SELECT DISTINCT input_order, code, technical_name,
           target_source, target_code, source_depth, route_source_code
    FROM crosswalk
    ORDER BY input_order, {target_case_sql}, source_depth, target_code
    """

    target_routes: list[tuple[int, str, str, str, int, str]] = []
    try:
        cw_rows = con.execute(crosswalk_query).fetchall()
        for row in cw_rows:
            idx = int(row[0])
            tech_name = row[2]
            target_source = str(row[3])
            target_code = str(row[4])
            source_depth = int(row[5] or 0)
            route_source_code = str(row[6]) if row[6] else str(row[1])
            target_routes.append((
                idx,
                str(tech_name) if tech_name else str(row[1]),
                target_source,
                target_code,
                source_depth,
                route_source_code,
            ))
    except Exception:
        logger.exception("SNOMED crosswalk query failed")

    results: list[FriendlyNameResult | None] = [None] * len(codes)
    for target_source in _SNOMED_TARGET_ORDER:
        route_items = [
            (
                idx,
                tech_name,
                CodeRef(source=target_source, code=target_code),
                source_depth,
                route_source_code,
            )
            for (
                idx,
                tech_name,
                route_target_source,
                target_code,
                source_depth,
                route_source_code,
            ) in target_routes
            if route_target_source == target_source and results[idx] is None
        ]
        if not route_items:
            continue

        target_codes = [
            target_ref
            for _idx, _tech_name, target_ref, _source_depth, _route_source_code in route_items
        ]
        if target_source == "RXNORM":
            target_results = get_rxnorm_patient_friendly(target_codes, con)
        elif target_source == "LNC":
            target_results = _resolve_loinc(target_codes, con, max_depth=walk_depth)
        else:
            target_results = _resolve_hierarchy_sources(
                target_source,
                target_codes,
                con,
                max_depth=walk_depth,
            )

        for (idx, tech_name, target_ref, source_depth, route_source_code), target_result in zip(
            route_items,
            target_results,
            strict=True,
        ):
            if results[idx] is not None or target_result.match_type == "original":
                continue
            if target_source == "RXNORM":
                match_type = (
                    f"broader_{target_result.match_type}"
                    if source_depth > 0
                    else target_result.match_type
                )
            elif target_result.match_type == "snomed_fallback":
                match_type = "snomed_to_target_snomed_fallback"
            elif target_result.match_depth == 0:
                match_type = "same_cui"
            else:
                match_type = "snomed_to_target_native_hierarchy"
            resolved_match_depth = source_depth + int(target_result.match_depth or 0)
            results[idx] = FriendlyNameResult(
                code=codes[idx],
                name=target_result.name,
                friendly_source=target_result.friendly_source,
                match_type=match_type,
                match_depth=resolved_match_depth,
                technical_name=tech_name,
                matched_via=Provenance.from_steps(
                    _STRATEGY,
                    [
                        ProvenanceStep(
                            op="input",
                            source="SNOMEDCT_US",
                            code=codes[idx].code,
                        ),
                        ProvenanceStep(
                            op="snomed_to_target",
                            source="SNOMEDCT_US",
                            code=route_source_code,
                            depth=source_depth,
                            target_source=target_ref.source,
                            target_code=target_ref.code,
                            mode="same_cui",
                        ),
                        ProvenanceStep(
                            op=match_type,
                            source=target_ref.source,
                            code=target_ref.code,
                            target_source=target_result.friendly_source,
                            depth=int(target_result.match_depth or 0),
                            name=target_result.name,
                        ),
                    ],
                ),
            )

    needs_snomed_walk: dict[int, CodeRef] = {}
    for i, cr in enumerate(codes):
        if results[i] is None:
            needs_snomed_walk[i] = cr

    if needs_snomed_walk:
        _guarded_snomed_walk(
            needs_snomed_walk,
            base_info,
            results,
            codes,
            con,
            max_depth=walk_depth,
        )

    # Fill remaining Nones with original display
    for i, r in enumerate(results):
        if r is None:
            _, tech_name = base_info.get(i, (codes[i].code, codes[i].code))
            results[i] = _make_original(
                codes[i], _STRATEGY, technical_name=tech_name
            )

    return results  # type: ignore[return-value]


def _resolve_crosswalked_codes(
    codes: Sequence[CodeRef],
    crosswalk_map: dict[int, tuple[str, str, str]],
    results: list[FriendlyNameResult | None],
    needs_snomed_walk: dict[int, CodeRef],
    con,
) -> None:
    """For SNOMED codes crosswalked to target sources, look up friendly names."""
    if not crosswalk_map:
        return

    # For each crosswalked code, check if the target source+code has a friendly name
    # Build a query to check friendly atoms for the target codes
    cw_items = []
    for idx, (tech_name, target_source, target_code) in crosswalk_map.items():
        cw_items.append((idx, tech_name, target_source, target_code))

    input_values = ",\n    ".join(
        f"({idx}, {_sql_literal(tsrc)}, {_sql_literal(tcode)})"
        for idx, _, tsrc, tcode in cw_items
    )

    query = f"""
    WITH crosswalk_targets(input_order, target_source, target_code) AS (
        VALUES {input_values}
    ),
    target_lookup AS (
        SELECT ct.input_order, ct.target_source, ct.target_code, a.cui
        FROM crosswalk_targets ct
        JOIN mt4ds.best_atoms a
          ON a.source = ct.target_source AND a.code = ct.target_code AND a.rank = 1
    ),
    friendly_hits AS (
        SELECT tl.input_order, f.name, f.friendly_source, f.tty
        FROM target_lookup tl
        JOIN mt4ds.friendly_atoms f ON f.cui = tl.cui
        WHERE f.is_broad = false
          AND f.source IN ('MEDLINEPLUS', 'CHV')
    ),
    ranked AS (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY input_order
            ORDER BY CASE friendly_source
                WHEN 'MEDLINEPLUS' THEN 0 ELSE 1
            END,
            {_friendly_tty_order_sql("tty")},
            name
        ) AS rn
        FROM friendly_hits
    )
    SELECT input_order, name, friendly_source
    FROM ranked
    WHERE rn = 1
    ORDER BY input_order
    """

    try:
        rows = con.execute(query).fetchall()
    except Exception:
        logger.exception("SNOMED crosswalk friendly lookup failed")
        return

    resolved_indices: set[int] = set()
    for row in rows:
        idx = int(row[0])
        friendly_name = str(row[1])
        friendly_src = str(row[2])
        tech_name, target_source, target_code = crosswalk_map[idx]
        code_ref = codes[idx]

        results[idx] = FriendlyNameResult(
            code=code_ref,
            name=friendly_name,
            friendly_source=friendly_src,
            match_type="same_cui",
            match_depth=0,
            technical_name=tech_name,
            matched_via=Provenance.from_steps(
                _STRATEGY,
                [
                    ProvenanceStep(
                        op="input",
                        source="SNOMEDCT_US",
                        code=code_ref.code,
                    ),
                    ProvenanceStep(
                        op="crosswalk",
                        source="SNOMEDCT_US",
                        code=code_ref.code,
                        target_source=target_source,
                        target_code=target_code,
                    ),
                    ProvenanceStep(
                        op="friendly_name",
                        source=friendly_src,
                        name=friendly_name,
                    ),
                ],
            ),
        )
        resolved_indices.add(idx)

    # Unresolved crosswalked codes also need SNOMED walk
    for idx in crosswalk_map:
        if idx not in resolved_indices:
            needs_snomed_walk[idx] = codes[idx]


def _guarded_snomed_walk(
    indices: dict[int, CodeRef],
    base_info: dict[int, tuple[str, str]],
    results: list[FriendlyNameResult | None],
    codes: Sequence[CodeRef],
    con,
    *,
    max_depth: int = _MAX_WALK_DEPTH,
) -> None:
    """Walk guarded SNOMED hierarchy for codes not resolved by crosswalk."""
    if not indices:
        return
    walk_depth = max(0, int(max_depth))

    input_values = ",\n    ".join(
        f"({_sql_literal(cr.code)}, {idx})" for idx, cr in indices.items()
    )

    closure_table = _walk_closure_table(con, walk_depth)
    if closure_table:
        snomed_walk_cte = f"""
    snomed_walk(input_order, code, walk_code, aui, cui, depth) AS (
        SELECT input_order, code, code, aui, cui, 0
        FROM base
        WHERE aui IS NOT NULL
        UNION ALL
        SELECT b.input_order, b.code, c.to_code, c.to_aui, c.to_cui, c.depth
        FROM base b
        JOIN {closure_table} c
          ON c.source = 'SNOMEDCT_US'
         AND c.from_aui = b.aui
         AND c.depth <= {walk_depth}
        WHERE b.aui IS NOT NULL
    ),"""
    else:
        snomed_walk_cte = f"""
    snomed_walk(input_order, code, walk_code, aui, cui, depth) AS (
        SELECT input_order, code, code, aui, cui, 0
        FROM base
        WHERE aui IS NOT NULL
        UNION ALL
        SELECT w.input_order, w.code, e.to_code, e.to_aui, e.to_cui, w.depth + 1
        FROM snomed_walk w
        JOIN mt4ds.walk_edges e
          ON e.source = 'SNOMEDCT_US' AND e.from_aui = w.aui AND e.direction = 'parent'
        WHERE w.depth < {walk_depth}
    ),"""

    query = f"""
    WITH RECURSIVE
    input_codes(code, input_order) AS (
        VALUES {input_values}
    ),
    base AS (
        SELECT i.input_order, i.code, a.aui, a.cui, a.name AS technical_name
        FROM input_codes i
        LEFT JOIN mt4ds.best_atoms a
          ON a.source = 'SNOMEDCT_US' AND a.code = i.code AND a.rank = 1
    ),
    {snomed_walk_cte}
    guarded_walk AS (
        SELECT sw.*
        FROM snomed_walk sw
        LEFT JOIN mt4ds.snomed_top_level_depth tld ON tld.code = sw.walk_code
        WHERE tld.code IS NULL OR tld.min_top_depth > {SNOMED_TOP_LEVEL_GUARD_DEPTH}
    ),
    friendly_hits AS (
        SELECT gw.input_order, gw.depth, f.name, f.friendly_source, f.tty
        FROM guarded_walk gw
        JOIN mt4ds.friendly_atoms f ON f.cui = gw.cui
        WHERE f.is_broad = false
          AND f.source IN ('MEDLINEPLUS', 'CHV')
    )
    SELECT input_order, name, friendly_source, depth AS match_depth
    FROM friendly_hits
    ORDER BY input_order, depth,
             CASE friendly_source WHEN 'MEDLINEPLUS' THEN 0 ELSE 1 END,
             {_friendly_tty_order_sql("tty")},
             name
    """

    try:
        rows = con.execute(query).fetchall()
    except Exception:
        logger.exception("Guarded SNOMED walk query failed")
        return

    for row in rows:
        idx = int(row[0])
        if results[idx] is not None:
            continue
        friendly_name = str(row[1])
        friendly_src = str(row[2])
        match_depth = int(row[3] or 0)
        code_ref = codes[idx]
        _, tech_name = base_info.get(idx, (code_ref.code, code_ref.code))
        if friendly_src == "CHV" and is_combo_name_mismatch(tech_name, friendly_name):
            continue

        results[idx] = FriendlyNameResult(
            code=code_ref,
            name=friendly_name,
            friendly_source=friendly_src,
            match_type="broader",
            match_depth=match_depth,
            technical_name=tech_name,
            matched_via=Provenance.from_steps(
                _STRATEGY,
                [
                    ProvenanceStep(
                        op="input",
                        source="SNOMEDCT_US",
                        code=code_ref.code,
                    ),
                    ProvenanceStep(
                        op="snomed_walk",
                        source="SNOMEDCT_US",
                        code=code_ref.code,
                        target_source=friendly_src,
                        depth=match_depth,
                        name=friendly_name,
                    ),
                ],
            ),
        )


# ---------------------------------------------------------------------------
# CVX -- lookup with group enrichment
# ---------------------------------------------------------------------------

def _resolve_cvx(
    codes: Sequence[CodeRef],
    con,
) -> list[FriendlyNameResult]:
    """Resolve patient-friendly names for CVX codes.

    Workflow:
    1. Lookup in best_atoms for technical name
    2. Group enrichment from cvx_metadata (if populated)
    3. Original display fallback (no hierarchy)
    """
    if not codes:
        return []

    input_values = ",\n    ".join(
        f"({_sql_literal(c.code)}, {i})" for i, c in enumerate(codes)
    )

    query = f"""
    WITH input_codes(code, input_order) AS (
        VALUES {input_values}
    ),
    lookup AS (
        SELECT i.input_order, i.code,
               a.name AS technical_name
        FROM input_codes i
        LEFT JOIN mt4ds.best_atoms a
          ON a.source = 'CVX' AND a.code = i.code AND a.rank = 1
    ),
    metadata_agg AS (
        SELECT code,
               string_agg(group_name, ' / ' ORDER BY group_name) AS group_name,
               string_agg(short_name, ' / ' ORDER BY short_name) AS short_name
        FROM (
            SELECT DISTINCT code, group_name, short_name
            FROM mt4ds.cvx_metadata
            WHERE group_name IS NOT NULL OR short_name IS NOT NULL
        ) cm
        GROUP BY code
    ),
    enrichment AS (
        SELECT l.input_order, l.code, l.technical_name,
               cm.group_name, cm.short_name
        FROM lookup l
        LEFT JOIN metadata_agg cm ON cm.code = l.code
    )
    SELECT input_order, code, technical_name, group_name, short_name
    FROM enrichment
    ORDER BY input_order
    """

    try:
        rows = con.execute(query).fetchall()
    except Exception:
        logger.exception("CVX resolution query failed")
        return [_make_original(c, _STRATEGY) for c in codes]

    results: list[FriendlyNameResult] = []
    for row in rows:
        idx = int(row[0])
        code = str(row[1])
        tech_name = row[2]
        group_name = row[3]
        short_name = row[4]

        code_ref = codes[idx]
        display = str(tech_name) if tech_name else code

        # Use group_name or short_name from cvx_metadata if available
        friendly_name = group_name or short_name or display
        friendly_src = "CVX"
        match_type = "original"

        if group_name or short_name:
            match_type = "cvx_group"

        results.append(
            FriendlyNameResult(
                code=code_ref,
                name=friendly_name,
                friendly_source=friendly_src,
                match_type=match_type,
                match_depth=0,
                technical_name=str(tech_name) if tech_name else None,
                matched_via=Provenance.from_steps(
                    _STRATEGY,
                    [
                        ProvenanceStep(
                            op="input",
                            source="CVX",
                            code=code_ref.code,
                        ),
                    ],
                ),
            )
        )

    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_original(
    code_ref: CodeRef,
    strategy: str,
    *,
    technical_name: str | None = None,
) -> FriendlyNameResult:
    """Create an 'original' match result for a code that could not be resolved."""
    name = technical_name or code_ref.code
    return FriendlyNameResult(
        code=code_ref,
        name=name,
        friendly_source=code_ref.source,
        match_type="original",
        match_depth=0,
        technical_name=technical_name,
        matched_via=Provenance.from_steps(
            strategy,
            [
                ProvenanceStep(
                    op="input",
                    source=code_ref.source,
                    code=code_ref.code,
                ),
            ],
        ),
    )
