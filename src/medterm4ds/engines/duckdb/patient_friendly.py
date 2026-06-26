"""Patient-friendly resolver subsystem for the local DuckDB engine.

Extracted from engines/duckdb/engine.py (Phase 5a of Tier C refactor). These
functions resolve patient-friendly names for non-RxNorm sources: default
hierarchy walk, LOINC component/axis tiers, CPT/HCPCS, CVX, and SNOMED
fallback routing.

Functions take the engine instance as their first parameter. Engine
module-level helpers (_Row, _dedupe, _chunks, _BROAD_*_SQL, _BLACKLIST_LOINC,
SNOMED constants, etc.) are late-imported to avoid circular dependencies.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from medterm4ds.core.models import CodeRef, FriendlyNameResult, Provenance, ProvenanceStep


def _resolve_default(
    engine,
    codes: Sequence[str],
    source: str,
    max_depth: int,
    *,
    filter_broad: bool = False,
) -> list[_Row]:
    from medterm4ds.engines.duckdb.engine import (
        _Row,
        _is_broad_friendly_name,
        _source_atom_order_sql,
        _source_hierarchy_atom_order_sql,
        _source_hierarchy_join_sql,
    )
    atom_order_sql = _source_atom_order_sql(source)
    hierarchy_atom_order_sql = _source_hierarchy_atom_order_sql(source)
    hierarchy_join, hierarchy_target = _source_hierarchy_join_sql(
        source,
        "w.AUI",
        upward=True,
    )
    with engine._temp_codes(codes) as temp:
        rows = engine.con.execute(
            f"""
            WITH RECURSIVE
            base AS (
                SELECT CODE, CUI, STR AS orig_name, AUI,
                       ROW_NUMBER() OVER (PARTITION BY CODE ORDER BY {hierarchy_atom_order_sql}) AS rn
                FROM mrconso
                WHERE SAB = ? AND SUPPRESS = 'N'
                  AND CODE IN (SELECT code FROM {temp})
            ),
            preferred AS (
                SELECT CODE, orig_name
                FROM (
                    SELECT CODE, STR AS orig_name,
                           ROW_NUMBER() OVER (PARTITION BY CODE ORDER BY {atom_order_sql}) AS rn
                    FROM mrconso
                    WHERE SAB = ? AND SUPPRESS = 'N'
                      AND CODE IN (SELECT code FROM {temp})
                ) p
                WHERE rn = 1
            ),
            seed AS (
                SELECT CODE, CUI, AUI, orig_name, 0 AS depth
                FROM base
                WHERE rn = 1
            ),
            walk AS (
                SELECT CODE, CUI, AUI, orig_name, depth
                FROM seed
                UNION ALL
                SELECT w.CODE, p.CUI, p.AUI, w.orig_name, w.depth + 1
                FROM walk w
                JOIN mrrel r ON {hierarchy_join}
                JOIN mrconso p ON p.AUI = {hierarchy_target}
                WHERE w.depth < ?
                  AND p.SAB = ? AND p.SUPPRESS = 'N'
            ),
            checked AS (
                SELECT w.CODE, w.orig_name, w.depth,
                       mp.STR AS mp_name, chv.STR AS chv_name,
                       mp.TTY AS mp_tty, chv.TTY AS chv_tty,
                       mp.CUI AS mp_cui, chv.CUI AS chv_cui
                FROM walk w
                LEFT JOIN mrconso mp
                    ON w.CUI = mp.CUI AND mp.SAB = 'MEDLINEPLUS'
                    AND mp.SUPPRESS = 'N' AND mp.TTY != 'HT'
                LEFT JOIN mrconso chv
                    ON w.CUI = chv.CUI AND chv.SAB = 'CHV'
                    AND chv.SUPPRESS = 'N' AND chv.TTY != 'HT'
            ),
            ranked AS (
                SELECT *,
                        ROW_NUMBER() OVER (
                            PARTITION BY CODE
                            ORDER BY CASE WHEN mp_name IS NOT NULL OR chv_name IS NOT NULL THEN 0 ELSE 1 END,
                                     depth,
                                     CASE WHEN mp_name IS NOT NULL THEN 0 ELSE 1 END,
                                     CASE upper(CASE WHEN mp_name IS NOT NULL THEN mp_tty ELSE chv_tty END)
                                         WHEN 'PT' THEN 0
                                         WHEN 'MH' THEN 1
                                         WHEN 'SY' THEN 2
                                         ELSE 3
                                     END,
                                     lower(COALESCE(mp_name, chv_name, ''))
                       ) AS rn
                FROM checked
            )
            SELECT p.CODE, p.orig_name,
                   COALESCE(r.mp_name, r.chv_name, p.orig_name) AS friendly_name,
                   CASE
                       WHEN r.mp_name IS NOT NULL THEN 'MEDLINEPLUS'
                       WHEN r.chv_name IS NOT NULL THEN 'CHV'
                       ELSE ?
                   END AS friendly_source,
                   CASE
                       WHEN r.mp_name IS NOT NULL OR r.chv_name IS NOT NULL THEN
                           CASE WHEN r.depth = 0 THEN 'exact' ELSE 'broader' END
                       ELSE 'original'
                   END AS match_type,
                   COALESCE(r.depth, 0) AS match_depth,
                   CASE WHEN r.mp_name IS NOT NULL THEN r.mp_tty ELSE r.chv_tty END AS tty,
                   COALESCE(r.mp_cui, r.chv_cui) AS matched_cui
            FROM preferred p
            LEFT JOIN ranked r ON r.CODE = p.CODE
            WHERE r.rn = 1
            """,
            [source, source, max_depth, source, source],
        ).fetchall()

    by_code: dict[str, _Row] = {}
    for code, orig_name, friendly_name, friendly_source, match_type, depth, tty, cui in rows:
        if friendly_name and (
            not filter_broad
            or not _is_broad_friendly_name(friendly_source, friendly_name)
        ):
            by_code[code] = _Row(
                code=code,
                source=source,
                name=friendly_name,
                friendly_source=friendly_source,
                match_type=match_type,
                match_depth=int(depth or 0),
                technical_name=orig_name,
                matched_via=engine._provenance(
                    "default_friendly",
                    CodeRef(source=source, code=code),
                    friendly_source=friendly_source,
                    friendly_name=friendly_name,
                    depth=int(depth or 0),
                    tty=tty,
                    cui=cui,
                ),
            )
        else:
            by_code[code] = engine._make_original(
                code,
                source,
                technical_name=orig_name,
                display_name=orig_name,
            )

    return [by_code.get(code) or engine._make_original(code, source) for code in codes]

def _apply_snomed_fallback(
    engine,
    source: str,
    rows: list[_Row],
    max_depth: int,
) -> None:
    from medterm4ds.engines.duckdb.engine import (
        _Row,
        _SNOMED_FALLBACK_SOURCES,
    )
    if source not in _SNOMED_FALLBACK_SOURCES:
        return
    fallback_codes = [
        row.code
        for row in rows
        if row.match_type == "original" or (
            row.match_type == "exact" and row.friendly_source == "CHV"
        )
    ]
    if not fallback_codes:
        return
    replacements = engine._resolve_default_via_snomed(fallback_codes, source, max_depth)
    if not replacements:
        return
    for row in rows:
        replacement = replacements.get(row.code)
        if replacement:
            row.name = replacement.name
            row.friendly_source = replacement.friendly_source
            row.match_type = replacement.match_type
            row.match_depth = replacement.match_depth
            row.matched_via = replacement.matched_via

def _resolve_default_via_snomed(
    engine,
    codes: Sequence[str],
    source: str,
    max_depth: int,
) -> dict[str, _Row]:
    from medterm4ds.engines.duckdb.engine import (
        _BROAD_CHV_NAME_SQL,
        _Row,
        _SNOMED_FALLBACK_QUERY_CHUNK_SIZE,
        _SNOMED_TOP_LEVEL_GUARD_DEPTH,
        _chunks,
        _dedupe,
        _is_broad_friendly_name,
        _is_combo_chv_mismatch,
    )
    if not codes:
        return {}
    codes = _dedupe(codes)
    effective_chunk_size = min(engine.query_chunk_size, _SNOMED_FALLBACK_QUERY_CHUNK_SIZE)
    if len(codes) > effective_chunk_size:
        result: dict[str, _Row] = {}
        chunks = list(_chunks(codes, effective_chunk_size))
        for chunk_index, chunk in enumerate(chunks, 1):
            engine._progress(
                f"resolving {source} SNOMED fallback chunk {chunk_index}/{len(chunks)} "
                f"({len(chunk)} codes)"
            )
            result.update(engine._resolve_default_via_snomed(chunk, source, max_depth))
        return result

    parent_join_isa = """
SELECT r.AUI1 AS child_aui, r.AUI2 AS parent_aui
FROM mrrel r
JOIN mrconso c1 ON c1.AUI = r.AUI1
JOIN mrconso c2 ON c2.AUI = r.AUI2
WHERE c1.SAB = 'SNOMEDCT_US'
  AND c2.SAB = 'SNOMEDCT_US'
  AND c1.SUPPRESS = 'N'
  AND c2.SUPPRESS = 'N'
  AND r.REL = 'PAR'
  AND r.RELA = 'isa'
UNION ALL
SELECT r.AUI1 AS child_aui, r.AUI2 AS parent_aui
FROM mrrel r
JOIN mrconso c1 ON c1.AUI = r.AUI1
JOIN mrconso c2 ON c2.AUI = r.AUI2
WHERE c1.SAB = 'SNOMEDCT_US'
  AND c2.SAB = 'SNOMEDCT_US'
  AND c1.SUPPRESS = 'N'
  AND c2.SUPPRESS = 'N'
  AND r.REL = 'PAR'
AND r.RELA = 'inverse_isa'
"""

    if source == "SNOMEDCT_US":
        parent_join_isa = f"snomed_parent_links AS (\n{parent_join_isa}\n),"
    else:
        parent_join_isa = ""

    if engine._table_exists("snomed_top_level_depth"):
        snomed_stop_join = """
                LEFT JOIN snomed_top_level_depth parent_depth
                  ON parent_depth.code = p.CODE
        """
        snomed_stop_predicate = (
            "AND (parent_depth.min_top_depth IS NULL "
            f"OR parent_depth.min_top_depth > {_SNOMED_TOP_LEVEL_GUARD_DEPTH})"
        )
    else:
        snomed_stop_join = ""
        snomed_stop_predicate = ""

    with engine._temp_codes(codes) as temp:
        if source == "SNOMEDCT_US":
            source_walk_sql = f"""
base AS (
SELECT CODE, CUI, AUI, STR AS source_name, rn
FROM (
    SELECT CODE, CUI, AUI, STR,
           ROW_NUMBER() OVER (PARTITION BY CODE, CUI ORDER BY AUI) as rn
    FROM mrconso
    WHERE CODE IN (SELECT code FROM {temp}) AND SAB = 'SNOMEDCT_US' AND SUPPRESS = 'N'
) base
WHERE rn = 1
),
source_walk AS (
SELECT CODE, CUI, AUI, source_name, 0 AS src_depth
FROM base
),
"""
        else:
            source_walk_sql = f"""
base AS (
SELECT CODE, CUI, AUI, STR AS source_name,
       ROW_NUMBER() OVER (PARTITION BY CODE, CUI ORDER BY AUI) as rn
FROM mrconso
WHERE CODE IN (SELECT code FROM {temp}) AND SAB = ? AND SUPPRESS = 'N'
),
source_walk AS (
SELECT CODE, CUI, AUI, source_name, 0 AS src_depth
FROM base WHERE rn = 1
UNION ALL
SELECT w.CODE, p.CUI, p.AUI, w.source_name, w.src_depth + 1
FROM source_walk w
JOIN mrrel r ON r.AUI1 = w.AUI AND r.REL = 'PAR'
JOIN mrconso p ON p.AUI = r.AUI2 AND p.SAB = ? AND p.SUPPRESS = 'N'
WHERE w.src_depth < ?
),
"""

        if source == "SNOMEDCT_US":
            query = f"""
WITH RECURSIVE
{source_walk_sql}
{parent_join_isa}
snomed_seed AS (
SELECT DISTINCT w.CODE, w.source_name, w.src_depth,
       s.CODE AS snomed_code, s.AUI AS snomed_aui,
       s.CUI AS snomed_cui, s.TTY AS snomed_tty
FROM source_walk w
JOIN mrconso s ON s.CUI = w.CUI
WHERE s.SAB = 'SNOMEDCT_US' AND s.SUPPRESS = 'N'
),
snomed_seed_nearest AS (
SELECT *
FROM (
    SELECT *,
           MIN(src_depth) OVER (PARTITION BY CODE) AS min_src_depth
    FROM snomed_seed
) nearest
WHERE src_depth = min_src_depth
),
snomed_seed_filtered AS (
SELECT CODE, source_name, src_depth, snomed_code, snomed_aui, snomed_cui
FROM (
    SELECT *,
           ROW_NUMBER() OVER (
               PARTITION BY CODE, snomed_code
               ORDER BY CASE upper(snomed_tty)
                            WHEN 'PT' THEN 0
                            WHEN 'SCD' THEN 1
                            WHEN 'FN' THEN 2
                            WHEN 'SY' THEN 3
                            ELSE 4
                        END,
               snomed_aui
           ) AS rn
    FROM snomed_seed_nearest
) ranked_snomed_seed
WHERE rn = 1
),
snomed_walk AS (
SELECT CODE, source_name, src_depth,
       snomed_code AS walk_seed, snomed_code AS walk_code,
       snomed_aui, snomed_cui, 0 AS snomed_depth
FROM snomed_seed_filtered
UNION
SELECT w.CODE, w.source_name, w.src_depth,
       w.walk_seed, p.CODE, p.AUI, p.CUI, w.snomed_depth + 1
FROM snomed_walk w
JOIN snomed_parent_links rels ON rels.child_aui = w.snomed_aui
JOIN mrconso p ON rels.parent_aui = p.AUI
{snomed_stop_join}
WHERE w.snomed_depth < ?
  AND p.SAB = 'SNOMEDCT_US' AND p.SUPPRESS = 'N'
  {snomed_stop_predicate}
),
matched AS (
SELECT
    w.CODE as code,
    w.source_name,
    w.snomed_aui as matched_aui,
    w.walk_seed,
    w.walk_code,
    coalesce(mp.STR, chv.STR) as friendly_name,
    CASE WHEN mp.STR IS NOT NULL THEN 'MEDLINEPLUS'
         WHEN chv.STR IS NOT NULL THEN 'CHV'
         ELSE ? END as friendly_source,
    w.src_depth as src_depth,
    w.snomed_depth as snomed_depth,
    w.src_depth + w.snomed_depth as match_depth,
    CASE WHEN mp.STR IS NOT NULL THEN 0 ELSE 1 END as source_priority,
    CASE WHEN mp.STR IS NOT NULL OR chv.STR IS NOT NULL THEN 1 ELSE 0 END as has_fallback,
    mp.TTY as tty,
    mp.CUI as cui,
    CASE WHEN w.src_depth = 0 AND w.snomed_depth = 0 THEN 'exact' ELSE 'broader' END as match_type
FROM snomed_walk w
LEFT JOIN mrconso mp
    ON w.snomed_cui = mp.CUI AND mp.SAB = 'MEDLINEPLUS'
    AND mp.SUPPRESS = 'N' AND mp.TTY != 'HT'
LEFT JOIN mrconso chv
    ON w.snomed_cui = chv.CUI AND chv.SAB = 'CHV'
    AND chv.SUPPRESS = 'N' AND chv.TTY != 'HT'
    AND lower(chv.STR) NOT IN ({_BROAD_CHV_NAME_SQL})
)
SELECT code, source_name, matched_aui, walk_seed, walk_code,
   friendly_name, friendly_source, src_depth, snomed_depth, match_depth,
   source_priority, has_fallback, tty, cui, match_type
FROM matched
WHERE has_fallback = 1
"""
            rows = engine.con.execute(query, [max_depth, source]).fetchall()
        else:
            query = f"""
WITH RECURSIVE
{source_walk_sql}
snomed_seed AS (
SELECT DISTINCT w.CODE, w.source_name, w.src_depth,
       s.AUI AS snomed_aui, s.CUI AS snomed_cui,
       s.CODE as walk_seed, s.CODE as walk_code
FROM source_walk w
JOIN mrconso s ON s.CUI = w.CUI AND s.SAB = 'SNOMEDCT_US' AND s.SUPPRESS = 'N'
),
snomed_walk AS (
SELECT w.CODE, w.source_name, w.src_depth,
       w.walk_seed,
       w.snomed_aui AS walk_seed_aui, w.snomed_cui AS walk_seed_cui,
       w.walk_code, 0 AS snomed_depth
FROM snomed_seed w
UNION ALL
SELECT w.CODE, w.source_name, w.src_depth,
       w.walk_seed, p.AUI, p.CUI, p.CODE, w.snomed_depth + 1
FROM snomed_walk w
JOIN mrrel r ON r.AUI1 = w.walk_seed_aui AND r.REL = 'PAR'
JOIN mrconso p ON p.AUI = r.AUI2
{snomed_stop_join}
WHERE w.snomed_depth < ?
  AND p.SAB = 'SNOMEDCT_US' AND p.SUPPRESS = 'N'
  {snomed_stop_predicate}
),
matched AS (
SELECT
    w.CODE as code,
    w.source_name,
    w.walk_seed_aui as matched_aui,
    w.walk_seed,
    w.walk_code,
    coalesce(mp.STR, chv.STR) as friendly_name,
    CASE WHEN mp.STR IS NOT NULL THEN 'MEDLINEPLUS'
         WHEN chv.STR IS NOT NULL THEN 'CHV'
         ELSE ? END as friendly_source,
    w.src_depth as src_depth,
    w.snomed_depth as snomed_depth,
    w.src_depth + w.snomed_depth as match_depth,
    CASE WHEN mp.STR IS NOT NULL THEN 0 ELSE 1 END as source_priority,
    CASE WHEN mp.STR IS NOT NULL OR chv.STR IS NOT NULL THEN 1 ELSE 0 END as has_fallback,
    mp.TTY as tty,
    mp.CUI as cui,
    CASE WHEN w.src_depth = 0 AND w.snomed_depth = 0 THEN 'exact' ELSE 'broader' END as match_type
FROM snomed_walk w
LEFT JOIN mrconso mp
    ON w.walk_seed_cui = mp.CUI AND mp.SAB = 'MEDLINEPLUS'
    AND mp.SUPPRESS = 'N' AND mp.TTY != 'HT'
LEFT JOIN mrconso chv
    ON w.walk_seed_cui = chv.CUI AND chv.SAB = 'CHV'
    AND chv.SUPPRESS = 'N' AND chv.TTY != 'HT'
    AND lower(chv.STR) NOT IN ({_BROAD_CHV_NAME_SQL})
)
SELECT code, source_name, matched_aui, walk_seed, walk_code,
   friendly_name, friendly_source, src_depth, snomed_depth, match_depth,
   source_priority, has_fallback, tty, cui, match_type
FROM matched
WHERE has_fallback = 1
"""
            params = [source, source, max_depth, max_depth, source]
            rows = engine.con.execute(query, params).fetchall()

    walk_code_index = 4
    depth_lookup = engine._snomed_top_level_depths([row[walk_code_index] for row in rows if row[walk_code_index]])

    def _is_too_broad(walk_code: str | None) -> bool:
        if not walk_code:
            return False
        walk_depth = depth_lookup.get(walk_code)
        return walk_depth is not None and walk_depth <= _SNOMED_TOP_LEVEL_GUARD_DEPTH

    ranked: dict[str, tuple[tuple[int, int, int, str, str, str], _Row]] = {}
    for (
        code,
        source_name,
        matched_aui,
        walk_seed,
        walk_code,
        friendly_name,
        friendly_source,
        src_depth,
        snomed_depth,
        match_depth,
        source_priority,
        has_fallback,
        tty,
        cui,
        match_type,
    ) in rows:
        _ = matched_aui
        if not friendly_name:
            continue
        if not has_fallback:
            continue
        if _is_broad_friendly_name(friendly_source, friendly_name):
            continue
        if source == "SNOMEDCT_US" and _is_too_broad(walk_code):
            continue
        if source == "SNOMEDCT_US" and friendly_source == "CHV" and _is_combo_chv_mismatch(
            source_name, friendly_name
        ):
            continue

        row_obj = _Row(
            code=code,
            source=source,
            name=friendly_name,
            friendly_source=friendly_source,
            match_type=match_type,
            match_depth=int(match_depth or 0),
            technical_name=source_name,
            matched_via=Provenance.from_steps(
                "snomed_fallback" if source == "SNOMEDCT_US" else "source_snomed_fallback",
                [
                    ProvenanceStep(op="input", source=source, code=code),
                    ProvenanceStep(
                        op="cross_reference",
                        source=source,
                        code=code,
                        target_source="SNOMEDCT_US",
                        target_code=walk_seed,
                        mode="broader",
                        depth=int(src_depth or 0),
                    ),
                    ProvenanceStep(
                        op="ancestor",
                        source="SNOMEDCT_US",
                        code=walk_code,
                        depth=int(snomed_depth or 0),
                    ),
                    ProvenanceStep(
                        op="friendly_atom",
                        source=friendly_source,
                        name=friendly_name,
                        tty=tty,
                        cui=cui,
                        depth=int(match_depth or 0),
                    ),
                ],
            ),
        )
        score = (
            int(match_depth or 0),
            int(source_priority or 0),
            str(friendly_name).lower(),
            friendly_source,
            match_type,
            0,
        )
        current = ranked.get(code)
        if current is None or score < current[0]:
            ranked[code] = (score, row_obj)
    return {code: row for code, (_score, row) in ranked.items()}

def _resolve_loinc(engine, codes: Sequence[str], max_depth: int) -> list[_Row]:
    from medterm4ds.engines.duckdb.engine import (
        _BLACKLIST_LOINC,
        _BROAD_CHV_NAME_SQL,
        _Row,
        _is_broad_friendly_name,
    )
    if not codes:
        return []
    with engine._temp_codes(codes) as temp:
        rows = engine.con.execute(
            f"""
            WITH
            base AS (
                SELECT CODE, CUI, STR AS orig_name, AUI
                FROM mrconso WHERE CODE IN (SELECT code FROM {temp}) AND SAB = 'LNC' AND SUPPRESS = 'N'
            ),
            comp_parts AS (
                SELECT c_src.CODE as loinc_code, c_tgt.STR as part_name
                FROM base c_src
                JOIN mrrel r ON r.AUI1 = c_src.AUI AND r.RELA IN ('component_of', 'measured_by')
                JOIN mrconso c_tgt ON c_tgt.AUI = r.AUI2 AND c_tgt.TTY = 'LPDN' AND c_tgt.SUPPRESS = 'N'
            ),
            tier1 AS (
                SELECT loinc_code, part_name as friendly_name, 'LNC' as fs, 'first_axis' as mt
                FROM (
                    SELECT loinc_code, part_name,
                        ROW_NUMBER() OVER (
                            PARTITION BY loinc_code ORDER BY LENGTH(part_name) DESC, part_name
                        ) as rn
                    FROM comp_parts
                    WHERE part_name NOT IN ({','.join(["'" + name + "'" for name in _BLACKLIST_LOINC])})
                ) sub WHERE rn = 1
            ),
            comp_cuis AS (
                SELECT DISTINCT c_src.CODE as loinc_code, c_tgt.CUI as comp_cui
                FROM base c_src
                LEFT JOIN tier1 t ON c_src.CODE = t.loinc_code
                JOIN mrrel r ON r.AUI1 = c_src.AUI AND r.RELA IN ('component_of', 'measured_by')
                JOIN mrconso c_tgt ON c_tgt.AUI = r.AUI2
                    AND c_tgt.SUPPRESS = 'N' AND c_tgt.CUI IS NOT NULL
                WHERE t.loinc_code IS NULL
            ),
            tier2 AS (
                SELECT cc.loinc_code,
                    COALESCE(mp.STR, chv.STR) as friendly_name,
                    CASE WHEN mp.STR IS NOT NULL THEN 'MEDLINEPLUS' ELSE 'CHV' END as fs,
                    'component' as mt
                FROM comp_cuis cc
                LEFT JOIN mrconso mp ON cc.comp_cui = mp.CUI AND mp.SAB = 'MEDLINEPLUS'
                    AND mp.SUPPRESS = 'N' AND mp.TTY != 'HT'
                LEFT JOIN mrconso chv ON cc.comp_cui = chv.CUI AND chv.SAB = 'CHV'
                    AND chv.SUPPRESS = 'N' AND chv.TTY != 'HT'
                    AND lower(chv.STR) NOT IN ({_BROAD_CHV_NAME_SQL})
                WHERE mp.STR IS NOT NULL OR chv.STR IS NOT NULL
            ),
            tier2_dedup AS (
                SELECT loinc_code, friendly_name, fs, mt
                FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY loinc_code ORDER BY fs) as rn FROM tier2
                ) sub
                WHERE rn = 1
            ),
            tier4 AS (
                SELECT b.CODE as loinc_code,
                    COALESCE(lc.STR, b.orig_name) as friendly_name,
                    'LNC' as fs,
                    CASE WHEN lc.STR IS NOT NULL THEN 'loinc_common' ELSE 'original' END as mt
                FROM base b
                LEFT JOIN tier1 t ON b.CODE = t.loinc_code
                LEFT JOIN tier2_dedup t2 ON b.CODE = t2.loinc_code
                LEFT JOIN mrconso lc ON b.CUI = lc.CUI
                    AND lc.SAB = 'LNC' AND lc.TTY = 'LC' AND lc.SUPPRESS = 'N'
                WHERE t.loinc_code IS NULL AND t2.loinc_code IS NULL
            ),
            all_results AS (
                SELECT loinc_code AS code, friendly_name, fs, mt, 0 as match_depth FROM tier1
                UNION ALL
                SELECT loinc_code, friendly_name, fs, mt, 1 FROM tier2_dedup
                UNION ALL
                SELECT loinc_code, friendly_name, fs, mt, 0 FROM tier4
            ),
            ranked AS (
                SELECT code, friendly_name, fs, mt, match_depth,
                    ROW_NUMBER() OVER (PARTITION BY code ORDER BY match_depth, code) AS rn
                FROM all_results
            )
            SELECT code, friendly_name, fs, mt, match_depth
            FROM ranked WHERE rn = 1
            """
        ).fetchall()

    by_code: dict[str, _Row] = {}
    for code, friendly_name, friendly_source, match_type, match_depth in rows:
        orig = engine._technical_name(code, "LNC")
        if match_type == "first_axis":
            by_code[code] = _Row(
                code=code,
                source="LNC",
                name=friendly_name,
                friendly_source="LNC",
                match_type=match_type,
                match_depth=0,
                technical_name=orig,
                matched_via=engine._simple_provenance(match_type, "LNC", code, friendly_name),
            )
        elif match_type == "component" and friendly_name and not _is_broad_friendly_name(friendly_source, friendly_name):
            by_code[code] = _Row(
                code=code,
                source="LNC",
                name=friendly_name,
                friendly_source=friendly_source,
                match_type=match_type,
                match_depth=match_depth,
                technical_name=orig,
                matched_via=engine._simple_provenance("loinc_component", "LNC", code, friendly_name),
            )
        elif match_type == "loinc_common":
            by_code[code] = _Row(
                code=code,
                source="LNC",
                name=friendly_name,
                friendly_source="LNC",
                match_type="loinc_common",
                match_depth=match_depth,
                technical_name=orig,
                matched_via=engine._simple_provenance("loinc_common", "LNC", code, friendly_name),
            )
        else:
            by_code[code] = engine._make_original(code, "LNC", technical_name=orig)

    rows_out = [by_code.get(code) or engine._make_original(code, "LNC") for code in codes]
    engine._apply_snomed_fallback("LNC", rows_out, max_depth)
    return rows_out

def _resolve_cpt(engine, codes: Sequence[str], max_depth: int) -> list[_Row]:
    from medterm4ds.engines.duckdb.engine import (
        _BROAD_CHV_NAME_SQL,
        _BROAD_MEDLINEPLUS_NAME_SQL,
        _Row,
        _source_atom_order_sql,
        _source_hierarchy_join_sql,
    )
    if not codes:
        return []
    display_order_sql = _source_atom_order_sql("CPT")
    hierarchy_join, hierarchy_target = _source_hierarchy_join_sql(
        "CPT",
        "w.AUI",
        upward=True,
    )
    with engine._temp_codes(codes) as temp:
        query = f"""
WITH RECURSIVE
base AS (
SELECT CODE, CUI, STR as orig_name, AUI
FROM mrconso WHERE CODE IN (SELECT code FROM {temp}) AND SAB = 'CPT' AND SUPPRESS = 'N'
),
cpt_walk AS (
SELECT b.CODE, b.CUI, b.orig_name, 0 as walk_depth, b.AUI
FROM base b
UNION ALL
SELECT w.CODE, p.CUI, w.orig_name, w.walk_depth + 1 as walk_depth, p.AUI
FROM cpt_walk w
JOIN mrrel r ON {hierarchy_join}
JOIN mrconso p ON p.AUI = {hierarchy_target} AND p.SAB = 'CPT' AND p.SUPPRESS = 'N'
WHERE w.walk_depth < ?
),
walked AS (
SELECT DISTINCT CODE, CUI, orig_name, walk_depth
FROM cpt_walk
),
walk_friendly AS (
SELECT w.CODE, w.walk_depth, mp.STR as friendly_name, 'MEDLINEPLUS' as fs
FROM walked w
JOIN mrconso mp ON w.CUI = mp.CUI AND mp.SAB = 'MEDLINEPLUS' AND mp.SUPPRESS = 'N'
WHERE mp.TTY != 'HT'
  AND lower(mp.STR) NOT IN ({_BROAD_MEDLINEPLUS_NAME_SQL})
UNION ALL
SELECT w.CODE, w.walk_depth, chv.STR as friendly_name, 'CHV' as fs
FROM walked w
JOIN mrconso chv
  ON w.CUI = chv.CUI AND chv.SAB = 'CHV' AND chv.SUPPRESS = 'N'
    AND chv.TTY != 'HT'
    AND lower(chv.STR) NOT IN ({_BROAD_CHV_NAME_SQL})
),
walk_results AS (
SELECT CODE, friendly_name, fs as friendly_source,
       CASE WHEN walk_depth = 0 THEN 'exact' ELSE 'broader' END as mt,
       walk_depth as match_depth
FROM (
    SELECT CODE, friendly_name, fs, walk_depth,
           ROW_NUMBER() OVER (
               PARTITION BY CODE
               ORDER BY walk_depth,
                        CASE WHEN fs = 'MEDLINEPLUS' THEN 0 ELSE 1 END,
                        LOWER(friendly_name)
           ) as rn
    FROM walk_friendly
) ranked
WHERE rn = 1
),
original_preferred AS (
SELECT CODE, orig_name
FROM (
    SELECT CODE, STR AS orig_name,
           ROW_NUMBER() OVER (
               PARTITION BY CODE
               ORDER BY {display_order_sql}
           ) AS rn
    FROM mrconso
    WHERE CODE IN (SELECT code FROM {temp})
      AND SAB = 'CPT'
      AND SUPPRESS = 'N'
) ranked_original
WHERE rn = 1
),
original AS (
SELECT p.CODE, p.orig_name as friendly_name, 'CPT' as friendly_source, 'original' as mt
FROM original_preferred p
LEFT JOIN walk_results w ON p.CODE = w.CODE
WHERE w.CODE IS NULL
),
all_results AS (
SELECT CODE, friendly_name, friendly_source, mt, match_depth
FROM walk_results
UNION ALL
SELECT CODE, friendly_name, friendly_source, mt, 0
FROM original
)
SELECT CODE, friendly_name, friendly_source, mt as match_type, match_depth, 'CPT' as _source
FROM all_results
"""
        rows = engine.con.execute(query, [max_depth]).fetchall()

    by_code: dict[str, _Row] = {}
    for code, friendly_name, friendly_source, match_type, match_depth, _source in rows:
        by_code[code] = _Row(
            code=code,
            source="CPT",
            name=friendly_name,
            friendly_source=friendly_source,
            match_type=match_type,
            match_depth=int(match_depth or 0),
            technical_name=engine._technical_name(code, "CPT"),
            matched_via=engine._simple_provenance(match_type, "CPT", code, friendly_name),
        )

    fallback_rows = [row for row in by_code.values() if row.match_type == "original"]
    if not fallback_rows:
        return [by_code.get(code) or engine._make_original(code, "CPT") for code in codes]

    fallback_codes = [row.code for row in fallback_rows]
    mapping = engine._map_cpt_targets(fallback_codes)
    if not mapping:
        return [by_code.get(code) or engine._make_original(code, "CPT") for code in codes]

    hcpcs_targets = sorted({target for (src, target) in mapping.values() if src == "HCPCS"})
    icd10_targets = sorted({target for (src, target) in mapping.values() if src == "ICD10CM"})
    snomed_targets = sorted({target for (src, target) in mapping.values() if src == "SNOMEDCT_US"})

    hcpcs_results = {
        row.code: row for row in engine._resolve_default(hcpcs_targets, "HCPCS", max_depth)
    } if hcpcs_targets else {}
    icd10_results = {
        row.code: row for row in engine._resolve_default(icd10_targets, "ICD10CM", max_depth)
    } if icd10_targets else {}
    snomed_results = (
        engine._resolve_default_via_snomed(snomed_targets, "SNOMEDCT_US", max_depth)
        if snomed_targets else {}
    )

    by_code_lookup = {row.code: row for row in by_code.values()}
    for cpt_code, (target_source, target_code) in mapping.items():
        base = by_code_lookup.get(cpt_code)
        if not base:
            continue
        replacement: _Row | None
        if target_source == "HCPCS":
            replacement = hcpcs_results.get(target_code)
        elif target_source == "ICD10CM":
            replacement = icd10_results.get(target_code)
        elif target_source == "SNOMEDCT_US":
            replacement = snomed_results.get(target_code)
        else:
            replacement = None

        if not replacement or replacement.match_type in {"original", "none"}:
            continue
        base.name = replacement.name
        base.friendly_source = replacement.friendly_source
        base.match_type = replacement.match_type
        base.match_depth = replacement.match_depth
        base.technical_name = engine._technical_name(cpt_code, "CPT")
        base.matched_via = Provenance.from_steps(
            "cpt_cross_reference",
            [
                ProvenanceStep(op="input", source="CPT", code=cpt_code),
                ProvenanceStep(
                    op="cross_reference",
                    source="CPT",
                    code=cpt_code,
                    target_source=target_source,
                    target_code=target_code,
                ),
                *(replacement.matched_via.steps if replacement.matched_via else ()),
            ],
        )

    return [by_code.get(code) or engine._make_original(code, "CPT") for code in codes]

def _resolve_cvx(engine, codes: Sequence[str]) -> list[_Row]:
    from medterm4ds.engines.duckdb.engine import (
        _Row,
        _load_default_cvx_groups,
    )
    metadata: dict[str, list[tuple[str | None, str | None]]] = {}
    if codes:
        try:
            with engine._temp_codes(codes) as temp:
                rows = engine.con.execute(
                    f"""
                    SELECT code, group_name, short_name
                    FROM mt4ds.cvx_metadata
                    WHERE code IN (SELECT code FROM {temp})
                    """
                ).fetchall()
            for code, group_name, short_name in rows:
                metadata.setdefault(str(code), []).append(
                    (
                        str(group_name) if group_name else None,
                        str(short_name) if short_name else None,
                    )
                )
        except Exception:
            metadata = {}

    needs_external_groups = any(code not in metadata for code in codes)
    if engine._cvx_groups_auto and not engine.cvx_groups and needs_external_groups:
        engine.cvx_groups = _load_default_cvx_groups()
    rows: list[_Row] = []
    for code in codes:
        metadata_rows = metadata.get(code, [])
        metadata_groups = [
            group_name
            for group_name, _short_name in metadata_rows
            if group_name
        ]
        metadata_short_names = [
            short_name
            for _group_name, short_name in metadata_rows
            if short_name
        ]
        groups = metadata_groups or engine.cvx_groups.get(code)
        short_names = metadata_short_names
        if groups or short_names:
            name = " / ".join(sorted(dict.fromkeys(groups or short_names)))
            rows.append(
                _Row(
                    code=code,
                    source="CVX",
                    name=name,
                    friendly_source="CVX",
                    match_type="cvx_group" if groups else "cvx_short_name",
                    match_depth=0,
                    technical_name=engine._technical_name(code, "CVX"),
                    matched_via=Provenance.from_steps(
                        "cvx_group" if groups else "cvx_short_name",
                        [
                            ProvenanceStep(op="input", source="CVX", code=code),
                            ProvenanceStep(
                                op="vaccine_group" if groups else "short_name",
                                source="CVX",
                                code=code,
                                name=name,
                            ),
                        ],
                    ),
                )
            )
        else:
            rows.append(engine._make_original(code, "CVX"))
    return rows


def _resolve_rxnorm(engine, codes: Sequence[str]) -> list[_Row]:
    from medterm4ds.engines.duckdb.engine import (
        _Row,
        _rxnorm_base_tty_order_sql,
        _rxnorm_tty_sql_rows,
        _sql_values,
    )
    if not codes:
        return []
    # RxNorm paths sometimes require walking through suppressed intermediate
    # AUIs (e.g. suppressed quantified-form / component nodes) before
    # reaching a target. Final selection prefers active candidates while
    # still allowing suppressed atoms to preserve recall.
    candidate_rows, path_step_rows = _rxnorm_tty_sql_rows()
    candidate_values = _sql_values(candidate_rows)
    path_step_values = _sql_values(path_step_rows)
    with engine._temp_codes(codes) as temp:
        rows = engine.con.execute(
            f"""
            WITH RECURSIVE
            base AS (
                SELECT c.CODE AS input_code, upper(c.TTY) AS start_tty,
                       c.STR AS orig_name, c.AUI AS start_aui, c.SUPPRESS AS start_suppress,
                       ROW_NUMBER() OVER (
                           PARTITION BY c.CODE
                ORDER BY {_rxnorm_base_tty_order_sql("c")}, c.AUI
                ) AS base_rn
                FROM mrconso c
                WHERE c.SAB = 'RXNORM' AND c.SUPPRESS = 'N'
                  AND c.CODE IN (SELECT code FROM {temp})
            ),
            candidate_targets(start_tty, target_tty, target_order, match_type, path_depth) AS (
                VALUES {candidate_values}
            ),
            path_steps(start_tty, target_tty, step, step_tty, path_depth) AS (
                VALUES {path_step_values}
            ),
            base_candidates AS (
                SELECT b.input_code, b.start_tty, b.orig_name, b.start_aui, b.base_rn,
                       b.start_suppress,
                       ct.target_tty, ct.target_order, ct.match_type, ct.path_depth
                FROM base b
                JOIN candidate_targets ct ON ct.start_tty = b.start_tty
            ),
            same_tty_hits AS (
                SELECT input_code, start_tty, orig_name, start_aui, base_rn,
                       target_tty, target_order, match_type,
                       start_aui AS target_aui, input_code AS target_code,
                       orig_name AS target_name, start_tty AS resolved_tty,
                       CASE WHEN start_suppress = 'N' THEN 0 ELSE 1 END AS target_is_active,
                       0 AS match_depth
                FROM base_candidates
                WHERE path_depth = 0
            ),
            topology_walk(
                input_code, start_tty, orig_name, start_aui, base_rn,
                target_tty, target_order, match_type, path_depth,
                step, aui
            ) AS (
                SELECT input_code, start_tty, orig_name, start_aui, base_rn,
                       target_tty, target_order, match_type, path_depth,
                       0 AS step, start_aui AS aui
                FROM base_candidates
                WHERE path_depth > 0
                UNION
                -- use the full RxNorm graph so suppressed/intermediate nodes can still
                -- be traversed as fallback intermediates while preferring active targets.
                SELECT w.input_code, w.start_tty, w.orig_name, w.start_aui, w.base_rn,
                       w.target_tty, w.target_order, w.match_type, w.path_depth,
                       ps.step, n.AUI
                FROM topology_walk w
                JOIN path_steps ps
                  ON ps.start_tty = w.start_tty
                 AND ps.target_tty = w.target_tty
                 AND ps.step = w.step + 1
                JOIN main.mrrel r ON r.AUI1 = w.aui OR r.AUI2 = w.aui
                JOIN main.mrconso n ON n.AUI = CASE
                    WHEN r.AUI1 = w.aui THEN r.AUI2 ELSE r.AUI1
                END
                WHERE w.step < w.path_depth
                  AND n.SAB = 'RXNORM'
                  AND upper(n.TTY) = ps.step_tty
            ),
            topology_hits AS (
                SELECT w.input_code, w.start_tty, w.orig_name, w.start_aui, w.base_rn,
                       w.target_tty, w.target_order, w.match_type,
                       n.AUI AS target_aui, n.CODE AS target_code,
                       n.STR AS target_name, upper(n.TTY) AS resolved_tty,
                       CASE WHEN n.SUPPRESS = 'N' THEN 0 ELSE 1 END AS target_is_active,
                       w.path_depth AS match_depth
                FROM topology_walk w
                JOIN main.mrconso n ON n.AUI = w.aui
                WHERE w.step = w.path_depth
                  AND n.SAB = 'RXNORM'
                UNION ALL
                SELECT * FROM same_tty_hits
            ),
            topology_best AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY input_code, start_aui, target_tty
                           ORDER BY target_is_active,
                                    CASE WHEN regexp_matches(target_code, '^[0-9]+$') THEN 0 ELSE 1 END,
                                    TRY_CAST(target_code AS BIGINT),
                                    target_code,
                                    target_name,
                                    target_aui
                       ) AS candidate_rn
                FROM topology_hits
                WHERE NOT (target_tty = 'IN' AND start_tty = 'IN' AND target_code != input_code)
            ),
            topology_selected AS (
                SELECT * FROM topology_best WHERE candidate_rn = 1
            ),
            missing_candidates AS (
                SELECT bc.input_code, bc.start_tty, bc.orig_name, bc.start_aui, bc.base_rn,
                       bc.target_tty, bc.target_order, bc.match_type
                FROM base_candidates bc
                LEFT JOIN topology_selected ts
                  ON ts.input_code = bc.input_code
                 AND ts.start_aui = bc.start_aui
                 AND ts.target_tty = bc.target_tty
                WHERE bc.path_depth > 0
                  AND ts.target_aui IS NULL
            ),
            brand_fallback_candidates AS (
                SELECT mc.input_code, mc.start_tty, mc.orig_name, mc.start_aui, mc.base_rn,
                       mc.target_tty, mc.target_order, mc.match_type,
                       regexp_extract(mc.orig_name, '\\[(.*?)\\]', 1) AS brand_name
                FROM missing_candidates mc
                WHERE mc.start_tty = 'SBDC'
                  AND mc.target_tty = 'IN'
                  AND mc.orig_name IS NOT NULL
                  AND regexp_extract(mc.orig_name, '\\[(.*?)\\]', 1) IS NOT NULL
            ),
            brand_bns AS (
                SELECT bfc.input_code, bfc.start_tty, bfc.orig_name, bfc.start_aui, bfc.base_rn,
                       bfc.target_tty, bfc.target_order, bfc.match_type,
                       b.STR AS brand_name, b.AUI AS brand_aui
                FROM (
                    SELECT
                        input_code,
                        start_tty,
                        orig_name,
                        start_aui,
                        base_rn,
                        target_tty,
                        target_order,
                        match_type,
                        upper(trim(regexp_extract(orig_name, '\\[(.*?)\\]', 1))) AS brand_name
                    FROM brand_fallback_candidates
                ) bfc
                JOIN main.mrconso b
                  ON b.SAB = 'RXNORM'
                 AND b.SUPPRESS = 'N'
                 AND b.TTY = 'BN'
                 AND upper(trim(b.STR)) = bfc.brand_name
            ),
            brand_fallback_hits AS (
                SELECT bb.input_code, bb.start_tty, bb.orig_name, bb.start_aui, bb.base_rn,
                       bb.target_tty, bb.target_order, bb.match_type,
                       n.AUI AS target_aui, n.CODE AS target_code,
                       n.STR AS target_name, 'IN' AS resolved_tty,
                       0 AS target_is_active, 1 AS match_depth
                FROM brand_bns bb
                JOIN main.mrrel r ON r.AUI1 = bb.brand_aui
                JOIN main.mrconso n ON n.AUI = r.AUI2
                WHERE n.SAB = 'RXNORM'
                  AND upper(n.TTY) = 'IN'
                  AND n.SUPPRESS = 'N'
            ),
            brand_fallback_selected AS (
                SELECT *
                FROM (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY input_code, start_aui, target_tty
                               ORDER BY target_is_active,
                                    CASE WHEN regexp_matches(target_code, '^[0-9]+$') THEN 0 ELSE 1 END,
                                    TRY_CAST(target_code AS BIGINT),
                                    target_code,
                                    target_name,
                                    target_aui
                           ) AS candidate_rn
                    FROM brand_fallback_hits
                ) ranked_brand_fallback
                WHERE candidate_rn = 1
            ),
            fallback_walk(
                input_code, start_tty, orig_name, start_aui, base_rn,
                target_tty, target_order, match_type,
                depth, aui, tty, path
            ) AS (
                SELECT input_code, start_tty, orig_name, start_aui, base_rn,
                       target_tty, target_order, match_type,
                       0 AS depth, start_aui AS aui, start_tty AS tty,
                       start_aui AS path
                FROM missing_candidates
                UNION
            SELECT w.input_code, w.start_tty, w.orig_name, w.start_aui, w.base_rn,
                   w.target_tty, w.target_order, w.match_type,
                   w.depth + 1 AS depth, n.AUI AS aui, upper(n.TTY) AS tty,
                   w.path || '>' || n.AUI AS path
            FROM fallback_walk w
            JOIN main.mrrel r ON r.AUI1 = w.aui
            JOIN main.mrconso n ON n.AUI = r.AUI2
            WHERE w.depth < 6
              AND n.SAB = 'RXNORM'
              AND strpos('>' || w.path || '>', '>' || n.AUI || '>') = 0
            ),
            fallback_hits AS (
                SELECT w.input_code, w.start_tty, w.orig_name, w.start_aui, w.base_rn,
                       w.target_tty, w.target_order, w.match_type,
                       n.AUI AS target_aui, n.CODE AS target_code,
                       n.STR AS target_name, upper(n.TTY) AS resolved_tty,
                       CASE WHEN n.SUPPRESS = 'N' THEN 0 ELSE 1 END AS target_is_active,
                       w.depth AS match_depth
                FROM fallback_walk w
                JOIN main.mrconso n ON n.AUI = w.aui
                WHERE w.depth > 0
                  AND w.tty = w.target_tty
                  AND n.SAB = 'RXNORM'
                  AND NOT (w.target_tty = 'IN' AND w.start_tty = 'IN' AND n.CODE != w.input_code)
            ),
            fallback_selected AS (
                SELECT *
                FROM (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY input_code, start_aui, target_tty
                               ORDER BY match_depth,
                                       target_is_active,
                                    CASE WHEN regexp_matches(target_code, '^[0-9]+$') THEN 0 ELSE 1 END,
                                    TRY_CAST(target_code AS BIGINT),
                                    target_code,
                                    target_name,
                                    target_aui
                           ) AS candidate_rn
                    FROM fallback_hits
                ) ranked_fallback
                WHERE candidate_rn = 1
            ),
            all_selected AS (
                SELECT input_code, start_tty, orig_name, start_aui, base_rn,
                       target_tty, target_order, match_type, target_aui,
                       target_code, target_name, resolved_tty, match_depth,
                       target_is_active
                FROM topology_selected
                UNION ALL
                SELECT input_code, start_tty, orig_name, start_aui, base_rn,
                       target_tty, target_order, match_type, target_aui,
                       target_code, target_name, resolved_tty, match_depth,
                        target_is_active
                FROM fallback_selected
                UNION ALL
                SELECT input_code, start_tty, orig_name, start_aui, base_rn,
                       target_tty, target_order, match_type, target_aui,
                       target_code, target_name, resolved_tty, match_depth,
                       target_is_active
                FROM brand_fallback_selected
            ),
            ranked AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY input_code
                           ORDER BY base_rn,
                                    target_order,
                                    target_is_active,
                                    CASE WHEN regexp_matches(target_code, '^[0-9]+$') THEN 0 ELSE 1 END,
                                    TRY_CAST(target_code AS BIGINT),
                                    target_code,
                                    target_name,
                                    target_aui
                       ) AS rn
                FROM all_selected
            ),
            base_summary AS (
                SELECT input_code, FIRST(orig_name ORDER BY base_rn) AS orig_name,
                       BOOL_OR(start_tty IN ('IN','PIN')) AS has_in_or_pin
                FROM base
                GROUP BY input_code
            )
            SELECT b.input_code, b.orig_name, b.has_in_or_pin,
                   r.target_code, r.target_name, r.target_tty,
                   r.match_type, r.match_depth
            FROM base_summary b
            LEFT JOIN ranked r ON r.input_code = b.input_code AND r.rn = 1
            """
        ).fetchall()

    by_code: dict[str, _Row] = {}
    for (
        code,
        orig_name,
        has_in_or_pin,
        target_code,
        target_name,
        target_tty,
        match_type,
        depth,
    ) in rows:
        if target_code and target_name:
            by_code[code] = _Row(
                code=code,
                source="RXNORM",
                name=target_name,
                friendly_source="RXNORM",
                match_type=match_type,
                match_depth=int(depth or 0),
                technical_name=orig_name,
                matched_via=Provenance.from_steps(
                    "rxnorm_tty",
                    [
                        ProvenanceStep(op="input", source="RXNORM", code=code),
                        ProvenanceStep(
                            op="tty_traversal",
                            source="RXNORM",
                            code=code,
                            target_source="RXNORM",
                            target_code=target_code,
                            tty=target_tty,
                            depth=int(depth or 0),
                        ),
                    ],
                ),
            )
        else:
            fallback_match_type = "ingredient" if has_in_or_pin else "original"
            by_code[code] = _Row(
                code=code,
                source="RXNORM",
                name=orig_name,
                friendly_source="RXNORM",
                match_type=fallback_match_type,
                match_depth=0,
                technical_name=orig_name,
                matched_via=engine._simple_provenance("rxnorm_tty", "RXNORM", code, orig_name),
            )

    return [by_code.get(code) or engine._make_none(code, "RXNORM") for code in codes]


def _resolve_snomed(
    engine,
    snomed_codes: Sequence[str],
    snomed_map: Mapping[str, tuple[str, str, bool]],
    non_snomed: Mapping[tuple[str, str], _Row],
    max_depth: int,
) -> list[_Row]:
    from medterm4ds.engines.duckdb.engine import _Row
    rows: list[_Row] = []
    fallback: list[tuple[str, bool, _Row | None]] = []
    for code in snomed_codes:
        mapped = snomed_map.get(code)
        if mapped:
            target_source, target_code, is_broader = mapped
            target = non_snomed.get((target_source, target_code))
            if target and target.match_type not in {"original", "none"}:
                match_type = f"broader_{target.match_type}" if is_broader else target.match_type
                rows.append(
                    _Row(
                        code=code,
                        source="SNOMEDCT_US",
                        name=target.name,
                        friendly_source=target.friendly_source,
                        match_type=match_type,
                        match_depth=target.match_depth,
                        technical_name=engine._technical_name(code, "SNOMEDCT_US"),
                        matched_via=Provenance.from_steps(
                            "snomed_cross_reference",
                            [
                                ProvenanceStep(op="input", source="SNOMEDCT_US", code=code),
                                ProvenanceStep(
                                    op="cross_reference",
                                    source="SNOMEDCT_US",
                                    code=code,
                                    target_source=target_source,
                                    target_code=target_code,
                                    mode="broader" if is_broader else "exact",
                                ),
                                *(target.matched_via.steps if target.matched_via else ()),
                            ],
                        ),
                    )
                )
            else:
                mapped_original = None
                if target:
                    mapped_original = _Row(
                        code=code,
                        source="SNOMEDCT_US",
                        name=target.name,
                        friendly_source=target.friendly_source,
                        match_type=f"broader_{target.match_type}" if is_broader else target.match_type,
                        match_depth=target.match_depth,
                        technical_name=engine._technical_name(code, "SNOMEDCT_US"),
                        matched_via=target.matched_via,
                    )
                fallback.append((code, is_broader, mapped_original))
        else:
            fallback.append((code, False, None))

    fallback_rows = engine._resolve_default_via_snomed(
        [code for code, _is_broader, _mapped in fallback],
        "SNOMEDCT_US",
        max_depth,
    )
    for code, is_broader, mapped_original in fallback:
        replacement = fallback_rows.get(code)
        if replacement:
            if is_broader:
                replacement.match_type = f"broader_{replacement.match_type}"
            rows.append(replacement)
        elif mapped_original:
            rows.append(mapped_original)
        else:
            rows.append(engine._make_original(code, "SNOMEDCT_US"))
    return rows

def _display_name(engine, code: str, source: str) -> str | None:
    from medterm4ds.engines.duckdb.engine import (
        _Row,
        _source_atom_order_sql,
    )
    if source == "LNC":
        row = engine.con.execute(
            """
            SELECT STR
            FROM mrconso
            WHERE CODE = ? AND SAB = ? AND SUPPRESS = 'N'
            LIMIT 1
            """,
            [code, source],
        ).fetchone()
        return row[0] if row else None

    atom_order_sql = _source_atom_order_sql(source)
    row = engine.con.execute(
        f"""
        SELECT STR
        FROM mrconso
        WHERE CODE = ? AND SAB = ? AND SUPPRESS = 'N'
        ORDER BY {atom_order_sql}
        LIMIT 1
        """,
        [code, source],
    ).fetchone()
    return row[0] if row else None

def _technical_name(engine, code: str, source: str) -> str | None:
    from medterm4ds.engines.duckdb.engine import (
        _Row,
        _source_technical_atom_order_sql,
    )
    if source == "LNC":
        row = engine.con.execute(
            """
            SELECT STR
            FROM mrconso
            WHERE CODE = ? AND SAB = ? AND SUPPRESS = 'N'
            LIMIT 1
            """,
            [code, source],
        ).fetchone()
        return row[0] if row else None

    atom_order_sql = _source_technical_atom_order_sql(source)
    row = engine.con.execute(
        f"""
        SELECT STR
        FROM mrconso
        WHERE CODE = ? AND SAB = ? AND SUPPRESS = 'N'
        ORDER BY {atom_order_sql}
        LIMIT 1
        """,
        [code, source],
    ).fetchone()
    return row[0] if row else None

def _make_original(
    engine,
    code: str,
    source: str,
    *,
    technical_name: str | None = None,
    display_name: str | None = None,
) -> _Row:
    from medterm4ds.engines.duckdb.engine import _Row
    display_name = display_name or engine._display_name(code, source)
    technical_name = technical_name or engine._technical_name(code, source) or display_name
    if display_name:
        return _Row(
            code=code,
            source=source,
            name=display_name,
            friendly_source=source,
            match_type="original",
            match_depth=0,
            technical_name=technical_name,
            matched_via=engine._simple_provenance("original", source, code, display_name),
        )
    return engine._make_none(code, source)
