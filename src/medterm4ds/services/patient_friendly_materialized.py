"""Build materialized patient-friendly candidate and resolution rows."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from medterm4ds.core.models import CodeRef, FriendlyNameResult, ProvenanceStep
from medterm4ds.engines.duckdb.prepared import (
    PATIENT_FRIENDLY_POLICY_VERSION,
    PREPARED_SCHEMA_VERSION,
)
from medterm4ds.services.patient_friendly_prepared import (
    get_non_rxnorm_patient_friendly,
)
from medterm4ds.services.prepared_primitives import (
    same_cui_crosswalk_sql as _same_cui_crosswalk_sql,
    table_exists as _table_exists,
)
from medterm4ds.services.rxnorm_tty_walk import get_rxnorm_patient_friendly
from medterm4ds.sources.snomed import SNOMED_TOP_LEVEL_GUARD_DEPTH

_NATIVE_FRONTIER_SOURCES = frozenset({"ICD10CM", "ICD10PCS", "LNC", "HCPCS", "CPT"})
_SNOMED_FALLBACK_FRONTIER_SOURCES = frozenset({
    "ICD10CM",
    "ICD10PCS",
    "LNC",
    "HCPCS",
    "CPT",
    "SNOMEDCT_US",
})


def materialize_patient_friendly_resolutions(
    codes: Sequence[CodeRef],
    con,
    *,
    policy_version: str = PATIENT_FRIENDLY_POLICY_VERSION,
    replace_existing: bool = True,
    max_depth: int = 5,
) -> dict[str, object]:
    """Populate patient-friendly candidate/path/resolution tables for codes.

    This is a build/review helper. Runtime patient-friendly lookup should read
    ``mt4ds.patient_friendly_resolutions`` after this table has been populated
    for the requested source/code/policy set.
    """
    ordered = [CodeRef(source=code.source, code=code.code) for code in codes]
    _ensure_materialized_tables(con)
    if not ordered:
        return {
            "inputs": 0,
            "candidates": 0,
            "paths": 0,
            "resolutions": 0,
            "policy_version": policy_version,
        }

    if replace_existing:
        _delete_existing_rows(con, ordered, policy_version)

    results = _resolve_prepared(ordered, con, max_depth=max_depth)
    match_types = Counter(result.match_type for result in results)
    candidate_rows, path_rows, resolution_rows = _rows_from_results(
        con,
        results,
        policy_version=policy_version,
        max_depth=max_depth,
    )

    if candidate_rows:
        con.executemany(
            """
            INSERT INTO mt4ds.patient_friendly_candidates (
              candidate_id, source, code, candidate_name, candidate_source,
              match_type, match_depth, candidate_origin, walk_source, walk_code,
              walk_depth, target_source, target_code, rank_features,
              policy_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            candidate_rows,
        )
    if path_rows:
        con.executemany(
            """
            INSERT INTO mt4ds.patient_friendly_candidate_paths (
              candidate_id, step_order, op, source, code, aui, cui,
              target_source, target_code, depth, name
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            path_rows,
        )
    if resolution_rows:
        con.executemany(
            """
            INSERT INTO mt4ds.patient_friendly_resolutions (
              source, code, name, friendly_source, match_type, match_depth,
              technical_name, selected_candidate_id, policy_version,
              umls_release, prepared_schema_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            resolution_rows,
        )

    return {
        "inputs": len(ordered),
        "candidates": len(candidate_rows),
        "paths": len(path_rows),
        "resolutions": len(resolution_rows),
        "missing_resolutions": len(ordered) - len(resolution_rows),
        "original_fallbacks": match_types.get("original", 0),
        "friendly_resolutions": len(resolution_rows) - match_types.get("original", 0),
        "match_types": dict(sorted(match_types.items())),
        "resolution_coverage": (
            round(len(resolution_rows) / len(ordered), 6) if ordered else 0.0
        ),
        "policy_version": policy_version,
    }


def materialize_patient_friendly_source(
    source: str,
    con,
    *,
    policy_version: str = PATIENT_FRIENDLY_POLICY_VERSION,
    replace_existing: bool = False,
    chunk_size: int = 5000,
    max_depth: int = 5,
) -> dict[str, object]:
    """Materialize patient-friendly rows for every prepared code in a source.

    Codes are streamed from ``mt4ds.best_atoms`` in deterministic chunks. This
    is the source-wide build path that should back full-code-system exports and
    benchmark refreshes.
    """
    source = str(source)
    chunk_size = max(1, int(chunk_size))
    _ensure_materialized_tables(con)
    if replace_existing:
        _delete_existing_source_rows(con, source, policy_version)

    total_inputs = 0
    total_candidates = 0
    total_paths = 0
    total_resolutions = 0
    total_original_fallbacks = 0
    total_friendly_resolutions = 0
    match_types: Counter[str] = Counter()
    chunks = 0
    last_code: str | None = None

    while True:
        if last_code is None:
            rows = con.execute(
                """
                SELECT DISTINCT code
                FROM mt4ds.best_atoms
                WHERE source = ?
                  AND rank = 1
                ORDER BY code
                LIMIT ?
                """,
                [source, chunk_size],
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT DISTINCT code
                FROM mt4ds.best_atoms
                WHERE source = ?
                  AND rank = 1
                  AND code > ?
                ORDER BY code
                LIMIT ?
                """,
                [source, last_code, chunk_size],
            ).fetchall()

        if not rows:
            break

        chunk_codes = [CodeRef(source=source, code=str(row[0])) for row in rows]
        summary = materialize_patient_friendly_resolutions(
            chunk_codes,
            con,
            policy_version=policy_version,
            replace_existing=not replace_existing,
            max_depth=max_depth,
        )
        total_inputs += int(summary["inputs"])
        total_candidates += int(summary["candidates"])
        total_paths += int(summary["paths"])
        total_resolutions += int(summary["resolutions"])
        total_original_fallbacks += int(summary.get("original_fallbacks", 0))
        total_friendly_resolutions += int(summary.get("friendly_resolutions", 0))
        match_types.update({
            str(match_type): int(count)
            for match_type, count in dict(summary.get("match_types", {})).items()
        })
        chunks += 1
        last_code = chunk_codes[-1].code

    return {
        "source": source,
        "chunks": chunks,
        "inputs": total_inputs,
        "candidates": total_candidates,
        "paths": total_paths,
        "resolutions": total_resolutions,
        "missing_resolutions": total_inputs - total_resolutions,
        "original_fallbacks": total_original_fallbacks,
        "friendly_resolutions": total_friendly_resolutions,
        "match_types": dict(sorted(match_types.items())),
        "resolution_coverage": (
            round(total_resolutions / total_inputs, 6) if total_inputs else 0.0
        ),
        "policy_version": policy_version,
    }


def materialize_patient_friendly_sources(
    sources: Sequence[str] | None,
    con,
    *,
    policy_version: str = PATIENT_FRIENDLY_POLICY_VERSION,
    replace_existing: bool = False,
    chunk_size: int = 5000,
    max_depth: int = 5,
) -> dict[str, object]:
    """Materialize patient-friendly rows for multiple prepared sources.

    When *sources* is None or empty, sources are discovered from
    ``mt4ds.best_atoms``.
    """
    if sources:
        source_list = list(dict.fromkeys(str(source) for source in sources))
    else:
        source_list = [
            str(row[0])
            for row in con.execute(
                """
                SELECT DISTINCT source
                FROM mt4ds.best_atoms
                WHERE rank = 1
                ORDER BY source
                """
            ).fetchall()
        ]

    source_summaries = [
        materialize_patient_friendly_source(
            source,
            con,
            policy_version=policy_version,
            replace_existing=replace_existing,
            chunk_size=chunk_size,
            max_depth=max_depth,
        )
        for source in source_list
    ]

    total_inputs = sum(int(summary["inputs"]) for summary in source_summaries)
    total_resolutions = sum(int(summary["resolutions"]) for summary in source_summaries)
    return {
        "sources": source_summaries,
        "source_count": len(source_summaries),
        "inputs": total_inputs,
        "candidates": sum(int(summary["candidates"]) for summary in source_summaries),
        "paths": sum(int(summary["paths"]) for summary in source_summaries),
        "resolutions": total_resolutions,
        "missing_resolutions": sum(
            int(summary.get("missing_resolutions", 0)) for summary in source_summaries
        ),
        "original_fallbacks": sum(
            int(summary.get("original_fallbacks", 0)) for summary in source_summaries
        ),
        "friendly_resolutions": sum(
            int(summary.get("friendly_resolutions", 0)) for summary in source_summaries
        ),
        "match_types": _combined_match_types(source_summaries),
        "resolution_coverage": (
            round(total_resolutions / total_inputs, 6) if total_inputs else 0.0
        ),
        "policy_version": policy_version,
    }


def _resolve_prepared(
    codes: Sequence[CodeRef],
    con,
    *,
    max_depth: int,
) -> list[FriendlyNameResult]:
    rxnorm_items: list[tuple[int, CodeRef]] = []
    other_items: list[tuple[int, CodeRef]] = []
    for index, code in enumerate(codes):
        if code.source == "RXNORM":
            rxnorm_items.append((index, code))
        else:
            other_items.append((index, code))

    by_index: dict[int, FriendlyNameResult] = {}
    if rxnorm_items:
        rows = get_rxnorm_patient_friendly([code for _index, code in rxnorm_items], con)
        for (index, _code), row in zip(rxnorm_items, rows, strict=True):
            by_index[index] = row
    if other_items:
        rows = get_non_rxnorm_patient_friendly(
            [code for _index, code in other_items],
            con,
            max_depth=max_depth,
        )
        for (index, _code), row in zip(other_items, rows, strict=True):
            by_index[index] = row
    return [by_index[index] for index in range(len(codes))]


def _combined_match_types(source_summaries: Sequence[dict[str, object]]) -> dict[str, int]:
    match_types: Counter[str] = Counter()
    for summary in source_summaries:
        match_types.update({
            str(match_type): int(count)
            for match_type, count in dict(summary.get("match_types", {})).items()
        })
    return dict(sorted(match_types.items()))


def _rows_from_results(
    con,
    results: Sequence[FriendlyNameResult],
    *,
    policy_version: str,
    max_depth: int,
) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]], list[tuple[object, ...]]]:
    next_candidate_id = _next_candidate_id(con)
    umls_release = _manifest_value(con, "umls_release")
    prepared_schema_version = (
        _manifest_value(con, "prepared_schema_version") or PREPARED_SCHEMA_VERSION
    )

    candidate_rows: list[tuple[object, ...]] = []
    path_rows: list[tuple[object, ...]] = []
    resolution_rows: list[tuple[object, ...]] = []

    for offset, result in enumerate(results):
        candidate_id = next_candidate_id + offset
        origin = _candidate_origin(result)
        first_step = _first_non_input_step(result)
        candidate_rows.append((
            candidate_id,
            result.code.source,
            result.code.code,
            result.name,
            result.friendly_source,
            result.match_type,
            result.match_depth,
            origin,
            first_step.source if first_step else result.code.source,
            first_step.code if first_step else result.code.code,
            first_step.depth if first_step else result.match_depth,
            first_step.target_source if first_step else result.friendly_source,
            first_step.target_code if first_step else None,
            _rank_features(result),
            policy_version,
        ))
        resolution_rows.append((
            result.code.source,
            result.code.code,
            result.name,
            result.friendly_source,
            result.match_type,
            result.match_depth,
            result.technical_name,
            candidate_id,
            policy_version,
            umls_release,
            prepared_schema_version,
        ))
        if result.matched_via:
            for step_order, step in enumerate(result.matched_via.steps):
                path_rows.append((
                    candidate_id,
                    step_order,
                    step.op,
                    step.source,
                    step.code,
                    step.aui,
                    step.cui,
                    step.target_source,
                    step.target_code,
                    step.depth,
                    step.name,
                ))

    extra_candidate_rows, extra_path_rows = _native_frontier_candidate_rows(
        con,
        results,
        start_candidate_id=next_candidate_id + len(results),
        policy_version=policy_version,
        max_depth=max_depth,
    )
    candidate_rows.extend(extra_candidate_rows)
    path_rows.extend(extra_path_rows)
    snomed_candidate_rows, snomed_path_rows = _snomed_fallback_frontier_candidate_rows(
        con,
        results,
        start_candidate_id=next_candidate_id + len(results) + len(extra_candidate_rows),
        policy_version=policy_version,
        max_depth=max_depth,
    )
    candidate_rows.extend(snomed_candidate_rows)
    path_rows.extend(snomed_path_rows)
    rxnorm_candidate_rows, rxnorm_path_rows = _rxnorm_tty_candidate_rows(
        con,
        results,
        start_candidate_id=(
            next_candidate_id
            + len(results)
            + len(extra_candidate_rows)
            + len(snomed_candidate_rows)
        ),
        policy_version=policy_version,
    )
    candidate_rows.extend(rxnorm_candidate_rows)
    path_rows.extend(rxnorm_path_rows)

    return candidate_rows, path_rows, resolution_rows


def _native_frontier_candidate_rows(
    con,
    results: Sequence[FriendlyNameResult],
    *,
    start_candidate_id: int,
    policy_version: str,
    max_depth: int,
) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    inputs = [
        (index, result)
        for index, result in enumerate(results)
        if result.code.source in _NATIVE_FRONTIER_SOURCES
    ]
    if not inputs:
        return [], []

    input_values = ",\n        ".join(
        "("
        f"{_sql_literal(result.code.source)}, "
        f"{_sql_literal(result.code.code)}, "
        f"{index}"
        ")"
        for index, result in inputs
    )
    query = f"""
    WITH RECURSIVE
    input_codes(source, code, input_order) AS (
        VALUES {input_values}
    ),
    lookup AS (
        SELECT i.input_order, i.source, i.code,
               a.aui, a.cui, a.tty, a.name AS technical_name
        FROM input_codes i
        LEFT JOIN mt4ds.best_atoms a
          ON a.source = i.source
         AND a.code = i.code
         AND a.rank = 1
    ),
    native_walk(input_order, source, input_code, walk_code, walk_aui, walk_cui, walk_tty, depth) AS (
        SELECT input_order, source, code, code, aui, cui, tty, 0
        FROM lookup
        WHERE aui IS NOT NULL
        UNION ALL
        SELECT w.input_order, w.source, w.input_code, e.to_code, e.to_aui,
               e.to_cui, e.to_tty, w.depth + 1
        FROM native_walk w
        JOIN mt4ds.walk_edges e
          ON e.source = w.source
         AND e.from_aui = w.walk_aui
         AND e.direction = 'parent'
        WHERE w.depth < {max(0, int(max_depth))}
    ),
    friendly_hits AS (
        SELECT w.input_order, w.source, w.input_code, w.walk_code, w.walk_aui,
               w.walk_cui, w.depth, f.name, f.friendly_source, f.code AS friendly_code
        FROM native_walk w
        JOIN mt4ds.friendly_atoms f
          ON f.cui = w.walk_cui
        WHERE f.is_broad = false
    ),
    ranked AS (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY input_order, name, friendly_source
            ORDER BY depth
        ) AS rn
        FROM friendly_hits
    )
    SELECT input_order, source, input_code, walk_code, walk_aui, walk_cui,
           depth, name, friendly_source, friendly_code
    FROM ranked
    WHERE rn = 1
    ORDER BY input_order, depth,
             CASE friendly_source WHEN 'MEDLINEPLUS' THEN 0 ELSE 1 END,
             name
    """
    try:
        rows = con.execute(query).fetchall()
    except Exception:
        return [], []

    selected_keys = {
        (
            result.code.source,
            result.code.code,
            result.name,
            result.friendly_source,
            result.match_type,
            result.match_depth,
        )
        for result in results
    }
    candidate_rows: list[tuple[object, ...]] = []
    path_rows: list[tuple[object, ...]] = []
    next_id = start_candidate_id

    for row in rows:
        source = str(row[1])
        code = str(row[2])
        depth = int(row[6] or 0)
        name = str(row[7])
        friendly_source = str(row[8])
        match_type = "exact" if depth == 0 else "broader"
        candidate_origin = "exact_same_cui" if depth == 0 else "native_hierarchy"
        key = (source, code, name, friendly_source, match_type, depth)
        if key in selected_keys:
            continue

        candidate_id = next_id
        next_id += 1
        candidate_rows.append((
            candidate_id,
            source,
            code,
            name,
            friendly_source,
            match_type,
            depth,
            candidate_origin,
            source,
            str(row[3]) if row[3] is not None else None,
            depth,
            friendly_source,
            str(row[9]) if row[9] is not None else None,
            (
                f"match_depth={depth};frontier_depth={depth};"
                f"friendly_source={friendly_source};"
                f"friendly_source_priority={_friendly_source_priority(friendly_source)};"
                f"match_type={match_type}"
            ),
            policy_version,
        ))
        path_rows.append((
            candidate_id,
            0,
            "native_frontier",
            source,
            str(row[3]) if row[3] is not None else None,
            str(row[4]) if row[4] is not None else None,
            str(row[5]) if row[5] is not None else None,
            friendly_source,
            str(row[9]) if row[9] is not None else None,
            depth,
            name,
        ))

    return candidate_rows, path_rows


def _snomed_fallback_frontier_candidate_rows(
    con,
    results: Sequence[FriendlyNameResult],
    *,
    start_candidate_id: int,
    policy_version: str,
    max_depth: int,
) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    inputs = [
        (index, result)
        for index, result in enumerate(results)
        if _should_materialize_snomed_fallback_frontier(result)
    ]
    if not inputs:
        return [], []

    input_values = ",\n        ".join(
        "("
        f"{_sql_literal(result.code.source)}, "
        f"{_sql_literal(result.code.code)}, "
        f"{index}"
        ")"
        for index, result in inputs
    )
    crosswalk_table, crosswalk_filter = _same_cui_crosswalk_sql(con)
    query = f"""
    WITH RECURSIVE
    input_codes(source, code, input_order) AS (
        VALUES {input_values}
    ),
    lookup AS (
        SELECT i.input_order, i.source, i.code,
               a.aui, a.cui, a.tty, a.name AS technical_name
        FROM input_codes i
        LEFT JOIN mt4ds.best_atoms a
          ON a.source = i.source
         AND a.code = i.code
         AND a.rank = 1
    ),
    native_walk(input_order, source, input_code, walk_code, walk_aui, walk_cui, walk_tty, source_depth) AS (
        SELECT input_order, source, code, code, aui, cui, tty, 0
        FROM lookup
        WHERE aui IS NOT NULL
          AND source != 'SNOMEDCT_US'
        UNION ALL
        SELECT w.input_order, w.source, w.input_code, e.to_code, e.to_aui,
               e.to_cui, e.to_tty, w.source_depth + 1
        FROM native_walk w
        JOIN mt4ds.walk_edges e
          ON e.source = w.source
         AND e.from_aui = w.walk_aui
         AND e.direction = 'parent'
        WHERE w.source_depth < {max(0, int(max_depth))}
    ),
    snomed_seed AS (
        SELECT DISTINCT l.input_order, l.source, l.code AS input_code,
               l.code AS source_walk_code, 0 AS source_depth,
               l.code AS snomed_code, l.aui AS snomed_aui, l.cui AS snomed_cui
        FROM lookup l
        WHERE l.source = 'SNOMEDCT_US'
          AND l.aui IS NOT NULL
        UNION ALL
        SELECT DISTINCT w.input_order, w.source, w.input_code,
               w.walk_code AS source_walk_code, w.source_depth,
               sce.target_code AS snomed_code, ba.aui AS snomed_aui,
               ba.cui AS snomed_cui
        FROM native_walk w
        JOIN {crosswalk_table} sce
          ON sce.source = w.source
         AND sce.code = w.walk_code
         AND sce.target_source = 'SNOMEDCT_US'
         {crosswalk_filter}
        JOIN mt4ds.best_atoms ba
          ON ba.source = 'SNOMEDCT_US'
         AND ba.code = sce.target_code
         AND ba.rank = 1
    ),
    snomed_walk(
        input_order, source, input_code, source_walk_code, source_depth,
        snomed_code, walk_code, walk_aui, walk_cui, snomed_depth
    ) AS (
        SELECT input_order, source, input_code, source_walk_code, source_depth,
               snomed_code, snomed_code, snomed_aui, snomed_cui, 0
        FROM snomed_seed
        UNION ALL
        SELECT w.input_order, w.source, w.input_code, w.source_walk_code,
               w.source_depth, w.snomed_code, e.to_code, e.to_aui, e.to_cui,
               w.snomed_depth + 1
        FROM snomed_walk w
        JOIN mt4ds.walk_edges e
          ON e.source = 'SNOMEDCT_US'
         AND e.from_aui = w.walk_aui
         AND e.direction = 'parent'
        WHERE w.snomed_depth < {max(0, int(max_depth))}
    ),
    guarded_walk AS (
        SELECT w.*
        FROM snomed_walk w
        LEFT JOIN mt4ds.snomed_top_level_depth tld
          ON tld.code = w.walk_code
        WHERE tld.code IS NULL OR tld.min_top_depth > {SNOMED_TOP_LEVEL_GUARD_DEPTH}
    ),
    friendly_hits AS (
        SELECT w.input_order, w.source, w.input_code, w.source_walk_code,
               w.source_depth, w.snomed_code, w.walk_code, w.walk_aui,
               w.walk_cui, w.snomed_depth, w.source_depth + w.snomed_depth AS match_depth,
               f.name, f.friendly_source, f.code AS friendly_code
        FROM guarded_walk w
        JOIN mt4ds.friendly_atoms f
          ON f.cui = w.walk_cui
        WHERE f.is_broad = false
          AND f.source IN ('MEDLINEPLUS', 'CHV')
    ),
    ranked AS (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY input_order, name, friendly_source
            ORDER BY match_depth
        ) AS rn
        FROM friendly_hits
    )
    SELECT input_order, source, input_code, source_walk_code, source_depth,
           snomed_code, walk_code, walk_aui, walk_cui, snomed_depth,
           match_depth, name, friendly_source, friendly_code
    FROM ranked
    WHERE rn = 1
    ORDER BY input_order, match_depth,
             CASE friendly_source WHEN 'MEDLINEPLUS' THEN 0 ELSE 1 END,
             name
    """
    try:
        rows = con.execute(query).fetchall()
    except Exception:
        return [], []

    selected_keys = {
        (
            result.code.source,
            result.code.code,
            result.name,
            result.friendly_source,
            result.match_type,
            result.match_depth,
        )
        for result in results
    }
    candidate_rows: list[tuple[object, ...]] = []
    path_rows: list[tuple[object, ...]] = []
    next_id = start_candidate_id

    for row in rows:
        source = str(row[1])
        code = str(row[2])
        match_depth = int(row[10] or 0)
        name = str(row[11])
        friendly_source = str(row[12])
        match_type = "broader" if source == "SNOMEDCT_US" else "snomed_fallback"
        key = (source, code, name, friendly_source, match_type, match_depth)
        if key in selected_keys:
            continue

        candidate_id = next_id
        next_id += 1
        origin = (
            "direct_snomed_guarded_walk"
            if source == "SNOMEDCT_US"
            else "snomed_fallback"
        )
        candidate_rows.append((
            candidate_id,
            source,
            code,
            name,
            friendly_source,
            match_type,
            match_depth,
            origin,
            "SNOMEDCT_US",
            str(row[6]) if row[6] is not None else None,
            int(row[9] or 0),
            friendly_source,
            str(row[13]) if row[13] is not None else None,
            (
                f"match_depth={match_depth};frontier_depth={match_depth};"
                f"friendly_source={friendly_source};"
                f"friendly_source_priority={_friendly_source_priority(friendly_source)};"
                f"match_type={match_type}"
            ),
            policy_version,
        ))
        path_rows.append((
            candidate_id,
            0,
            origin,
            "SNOMEDCT_US",
            str(row[6]) if row[6] is not None else None,
            str(row[7]) if row[7] is not None else None,
            str(row[8]) if row[8] is not None else None,
            friendly_source,
            str(row[13]) if row[13] is not None else None,
            match_depth,
            name,
        ))

    return candidate_rows, path_rows


def _rxnorm_tty_candidate_rows(
    con,
    results: Sequence[FriendlyNameResult],
    *,
    start_candidate_id: int,
    policy_version: str,
) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    inputs = [
        (index, result)
        for index, result in enumerate(results)
        if result.code.source == "RXNORM"
    ]
    if not inputs:
        return [], []

    input_values = ",\n        ".join(
        "("
        f"{_sql_literal(result.code.code)}, "
        f"{index}"
        ")"
        for index, result in inputs
    )
    query = f"""
    WITH RECURSIVE
    input_codes(code, input_order) AS (
        VALUES {input_values}
    ),
    base AS (
        SELECT i.input_order, a.code, a.aui, a.tty, a.name AS technical_name
        FROM input_codes i
        JOIN mt4ds.best_atoms a
          ON a.source = 'RXNORM'
         AND a.code = i.code
         AND a.rank = 1
    ),
    strategy AS (
        SELECT *
        FROM mt4ds.patient_friendly_strategy
        WHERE source = 'RXNORM'
          AND walk_kind = 'tty_traversal'
    ),
    paths AS (
        SELECT b.input_order, b.code AS input_code, b.aui AS start_aui,
               b.tty AS start_tty, b.technical_name,
               p.path_id, p.target_tty, p.match_type, p.target_order,
               p.path_depth AS max_depth
        FROM base b
        JOIN mt4ds.rxnorm_tty_paths p
          ON p.start_tty = b.tty
        JOIN strategy s
          ON s.target_tty = p.target_tty
         AND s.match_type = p.match_type
    ),
    walk(input_order, input_code, technical_name, path_id, target_tty,
         match_type, target_order, step, aui) AS (
        SELECT input_order, input_code, technical_name, path_id, target_tty,
               match_type, target_order, 0, start_aui
        FROM paths
        UNION ALL
        SELECT w.input_order, w.input_code, w.technical_name, w.path_id,
               w.target_tty, w.match_type, w.target_order, w.step + 1,
               e.target_aui
        FROM walk w
        JOIN mt4ds.rxnorm_tty_path_steps ps
          ON ps.path_id = w.path_id
         AND ps.step = w.step + 1
        JOIN mt4ds.rxnorm_tty_edges e
          ON e.source_aui = w.aui
         AND e.target_tty = ps.tty
        WHERE w.step < (
            SELECT MAX(ps2.step)
            FROM mt4ds.rxnorm_tty_path_steps ps2
            WHERE ps2.path_id = w.path_id
        )
    ),
    hits AS (
        SELECT w.input_order, w.input_code, w.technical_name,
               w.target_tty, w.match_type, w.target_order,
               w.step AS match_depth,
               e.target_aui, e.target_code, e.target_name,
               e.target_suppress
        FROM walk w
        JOIN mt4ds.rxnorm_tty_edges e
          ON e.target_aui = w.aui
        WHERE w.step = (
            SELECT MAX(ps2.step)
            FROM mt4ds.rxnorm_tty_path_steps ps2
            WHERE ps2.path_id = w.path_id
        )
    ),
    ranked AS (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY input_order, target_name, match_type
            ORDER BY match_depth
        ) AS rn
        FROM hits
    )
    SELECT input_order, input_code, technical_name, target_tty, match_type,
           target_order, match_depth, target_aui, target_code, target_name,
           target_suppress
    FROM ranked
    WHERE rn = 1
    ORDER BY input_order, target_order,
             CASE target_suppress WHEN 'N' THEN 0 ELSE 1 END,
             CASE WHEN regexp_matches(target_code, '^[0-9]+$') THEN 0 ELSE 1 END,
             try_cast(target_code AS BIGINT),
             target_code,
             target_name
    """
    try:
        rows = con.execute(query).fetchall()
    except Exception:
        return [], []

    selected_keys = {
        (
            result.code.code,
            result.name,
            result.match_type,
            result.match_depth,
        )
        for result in results
        if result.code.source == "RXNORM"
    }
    candidate_rows: list[tuple[object, ...]] = []
    path_rows: list[tuple[object, ...]] = []
    seen: set[tuple[str, str, str, int]] = set()
    next_id = start_candidate_id

    for row in rows:
        code = str(row[1])
        target_tty = str(row[3])
        match_type = str(row[4])
        target_order = int(row[5] or 0)
        match_depth = int(row[6] or 0)
        target_aui = str(row[7]) if row[7] is not None else None
        target_code = str(row[8]) if row[8] is not None else None
        target_name = str(row[9])
        target_suppress = str(row[10]) if row[10] is not None else None
        key = (code, target_name, match_type, match_depth)
        if key in selected_keys or key in seen:
            continue
        seen.add(key)

        candidate_id = next_id
        next_id += 1
        candidate_rows.append((
            candidate_id,
            "RXNORM",
            code,
            target_name,
            "RXNORM",
            match_type,
            match_depth,
            "rxnorm_tty",
            "RXNORM",
            target_code,
            match_depth,
            "RXNORM",
            target_code,
            (
                f"match_depth={match_depth};friendly_source=RXNORM;"
                f"match_type={match_type};target_tty={target_tty};"
                f"target_order={target_order};target_suppress={target_suppress}"
            ),
            policy_version,
        ))
        path_rows.append((
            candidate_id,
            0,
            "rxnorm_tty_candidate",
            "RXNORM",
            target_code,
            target_aui,
            None,
            "RXNORM",
            target_code,
            match_depth,
            target_name,
        ))

    return candidate_rows, path_rows


def _should_materialize_snomed_fallback_frontier(result: FriendlyNameResult) -> bool:
    if result.code.source not in _SNOMED_FALLBACK_FRONTIER_SOURCES:
        return False
    if result.code.source == "SNOMEDCT_US":
        return result.match_type in {"broader", "snomed_fallback"}
    return result.match_type == "snomed_fallback"


def _candidate_origin(result: FriendlyNameResult) -> str:
    if result.match_type == "original":
        return "original"
    if result.code.source == "RXNORM":
        return "rxnorm_tty"
    if result.code.source == "CVX":
        return "cvx_enrichment"
    if result.code.source == "SNOMEDCT_US":
        if result.match_type == "same_cui":
            return "same_cui_crosswalk"
        if result.match_type == "snomed_to_target_native_hierarchy":
            return "snomed_to_target_native_hierarchy"
        if result.match_type == "snomed_to_target_snomed_fallback":
            return "snomed_to_target_snomed_fallback"
        if result.match_type in {"broader", "snomed_fallback"}:
            return "direct_snomed_guarded_walk"
    if result.match_type == "snomed_fallback":
        return "snomed_fallback"
    if result.match_type in {"first_axis", "component", "loinc_common", "cvx_group"}:
        return "source_native_tier"
    if result.match_type == "exact":
        return "exact_same_cui"
    if result.match_type == "same_cui":
        return "same_cui_crosswalk"
    return "native_hierarchy"


def _first_non_input_step(result: FriendlyNameResult) -> ProvenanceStep | None:
    if not result.matched_via:
        return None
    for step in result.matched_via.steps:
        if step.op != "input":
            return step
    return None


def _rank_features(result: FriendlyNameResult) -> str:
    return (
        f"match_depth={result.match_depth};"
        f"frontier_depth={result.match_depth};"
        f"friendly_source={result.friendly_source};"
        f"friendly_source_priority={_friendly_source_priority(result.friendly_source)};"
        f"match_type={result.match_type}"
    )


def _friendly_source_priority(friendly_source: str) -> int:
    if friendly_source == "MEDLINEPLUS":
        return 0
    if friendly_source == "CHV":
        return 1
    return 2


def _sql_literal(value: object) -> str:
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _next_candidate_id(con) -> int:
    try:
        row = con.execute(
            "SELECT COALESCE(MAX(candidate_id), 0) + 1 FROM mt4ds.patient_friendly_candidates"
        ).fetchone()
        return int(row[0] or 1)
    except Exception:
        return 1


def _delete_existing_rows(
    con,
    codes: Sequence[CodeRef],
    policy_version: str,
) -> None:
    pairs = [(code.source, code.code, policy_version) for code in codes]
    if not pairs:
        return
    existing_candidate_ids: list[tuple[int]] = []
    for source, code, policy in pairs:
        existing_candidate_ids.extend(
            (int(row[0]),)
            for row in con.execute(
                """
                SELECT candidate_id
                FROM mt4ds.patient_friendly_candidates
                WHERE source = ? AND code = ? AND policy_version = ?
                """,
                [source, code, policy],
            ).fetchall()
            if row[0] is not None
        )
    if existing_candidate_ids:
        con.executemany(
            """
            DELETE FROM mt4ds.patient_friendly_candidate_paths
            WHERE candidate_id = ?
            """,
            existing_candidate_ids,
        )
    con.executemany(
        """
        DELETE FROM mt4ds.patient_friendly_resolutions
        WHERE source = ? AND code = ? AND policy_version = ?
        """,
        pairs,
    )
    con.executemany(
        """
        DELETE FROM mt4ds.patient_friendly_candidates
        WHERE source = ? AND code = ? AND policy_version = ?
        """,
        pairs,
    )


def _delete_existing_source_rows(
    con,
    source: str,
    policy_version: str,
) -> None:
    con.execute(
        """
        DELETE FROM mt4ds.patient_friendly_candidate_paths
        WHERE candidate_id IN (
          SELECT candidate_id
          FROM mt4ds.patient_friendly_candidates
          WHERE source = ? AND policy_version = ?
        )
        """,
        [source, policy_version],
    )
    con.execute(
        """
        DELETE FROM mt4ds.patient_friendly_resolutions
        WHERE source = ? AND policy_version = ?
        """,
        [source, policy_version],
    )
    con.execute(
        """
        DELETE FROM mt4ds.patient_friendly_candidates
        WHERE source = ? AND policy_version = ?
        """,
        [source, policy_version],
    )


def _manifest_value(con, key: str) -> str | None:
    try:
        row = con.execute(
            "SELECT value FROM mt4ds.prepare_manifest WHERE key = ?",
            [key],
        ).fetchone()
        if row and row[0] is not None:
            return str(row[0])
    except Exception:
        return None
    return None


def _ensure_materialized_tables(con) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS mt4ds")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS mt4ds.patient_friendly_candidates (
          candidate_id BIGINT,
          source VARCHAR,
          code VARCHAR,
          candidate_name VARCHAR,
          candidate_source VARCHAR,
          match_type VARCHAR,
          match_depth INTEGER,
          candidate_origin VARCHAR,
          walk_source VARCHAR,
          walk_code VARCHAR,
          walk_depth INTEGER,
          target_source VARCHAR,
          target_code VARCHAR,
          rank_features VARCHAR,
          policy_version VARCHAR,
          created_at TIMESTAMP DEFAULT current_timestamp
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS mt4ds.patient_friendly_candidate_paths (
          candidate_id BIGINT,
          step_order INTEGER,
          op VARCHAR,
          source VARCHAR,
          code VARCHAR,
          aui VARCHAR,
          cui VARCHAR,
          target_source VARCHAR,
          target_code VARCHAR,
          depth INTEGER,
          name VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS mt4ds.patient_friendly_resolutions (
          source VARCHAR,
          code VARCHAR,
          name VARCHAR,
          friendly_source VARCHAR,
          match_type VARCHAR,
          match_depth INTEGER,
          technical_name VARCHAR,
          selected_candidate_id BIGINT,
          policy_version VARCHAR,
          umls_release VARCHAR,
          prepared_schema_version VARCHAR,
          generated_at TIMESTAMP DEFAULT current_timestamp
        )
        """
    )
    for ddl in (
        """
        CREATE INDEX IF NOT EXISTS idx_mt4ds_pf_candidates_source_code
        ON mt4ds.patient_friendly_candidates(source, code, policy_version)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_mt4ds_pf_candidates_origin
        ON mt4ds.patient_friendly_candidates(candidate_origin)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_mt4ds_pf_paths_candidate
        ON mt4ds.patient_friendly_candidate_paths(candidate_id, step_order)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_mt4ds_pf_resolutions_source_code
        ON mt4ds.patient_friendly_resolutions(source, code, policy_version)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_mt4ds_pf_resolutions_policy
        ON mt4ds.patient_friendly_resolutions(policy_version)
        """,
    ):
        try:
            con.execute(ddl)
        except Exception:
            pass
