"""Condition-medication indication resolver for the local DuckDB engine.

Extracted from domains/terminology.py (Phase 7 of Tier C refactor). The
domain layer should not run raw UMLS SQL; these functions implement the
may_treat / may_prevent / may_diagnose / contraindicated_with_disease
traversal over mrconso/mrrel for the engine, and the domain layer calls
the engine protocol method.

Public entry points (called by LocalDuckDBEngine):
  - validate_indication_relationships: validate the relationship_types arg
  - query_condition_medication_relationships: the recursive CTE
  - format_condition_medication_row: row -> dict
  - query_ndcs_for_rxcuis: NDC lookup for RxNorm codes
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from medterm4ds.core.normalize import normalize_source


_DEFAULT_INDICATION_RELATIONSHIPS = ("may_treat",)
_ALLOWED_INDICATION_RELATIONSHIPS = {
    "may_treat",
    "may_prevent",
    "may_diagnose",
    "contraindicated_with_disease",
}
_INDICATION_TARGET_TTYS = ("IN", "MIN", "SCDG")


def validate_indication_relationships(relationship_types: Sequence[str] | str | None) -> tuple[str, ...]:
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


def query_condition_medication_relationships(
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


def format_condition_medication_row(row: tuple[Any, ...]) -> dict[str, Any]:
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


def query_ndcs_for_rxcuis(con, rxcuis: Sequence[str]) -> dict[str, list[str]]:
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
