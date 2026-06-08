"""RxNorm patient-friendly resolution via bounded TTY-path traversal.

Uses the prepared mt4ds tables (best_atoms, rxnorm_tty_paths,
rxnorm_tty_path_steps, rxnorm_tty_edges, patient_friendly_strategy)
to resolve RxNorm codes to patient-friendly names without any raw
mrrel/mrconso joins.

This module is Phase 5 of the terminology normalization refactor.
It is designed to work with both real UMLS data and synthetic test data.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from medterm4ds.core.models import (
    CodeRef,
    FriendlyNameResult,
    Provenance,
    ProvenanceStep,
)

logger = logging.getLogger(__name__)

# TTYs that resolve to themselves as ingredient matches
_SELF_INGREDIENT_TTYS = frozenset({"IN", "MIN"})

# Strategy label used in provenance
_STRATEGY = "rxnorm_tty_traversal"


def _sql_literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def get_rxnorm_patient_friendly(
    codes: Sequence[CodeRef],
    con,
) -> list[FriendlyNameResult]:
    """Resolve RxNorm patient-friendly names using bounded TTY path traversal.

    Uses mt4ds.rxnorm_tty_paths, rxnorm_tty_path_steps, rxnorm_tty_edges,
    and patient_friendly_strategy for bounded traversal. No raw mrrel joins.

    Parameters
    ----------
    codes : Sequence[CodeRef]
        RxNorm codes to resolve. All must have source='RXNORM'.
    con : duckdb.DuckDBPyConnection
        Open DuckDB connection with mt4ds schema populated.

    Returns
    -------
    list[FriendlyNameResult]
        One result per input code, preserving input order.
    """
    if not codes:
        return []

    # Build input VALUES clause
    input_values = ",\n    ".join(
        f"({_sql_literal(c.code)}, {i})" for i, c in enumerate(codes)
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
          ON a.source = 'RXNORM' AND a.code = i.code AND a.rank = 1
    ),
    strategy AS (
        SELECT DISTINCT target_tty, match_type
        FROM mt4ds.patient_friendly_strategy
        WHERE source = 'RXNORM' AND walk_kind = 'tty_traversal'
    ),
    paths AS (
        SELECT b.input_order, b.code AS input_code, b.aui AS start_aui,
               b.tty AS start_tty, b.technical_name,
               p.path_id, p.target_tty, p.match_type, p.target_order, p.path_depth AS max_depth
        FROM base b
        JOIN mt4ds.rxnorm_tty_paths p ON p.start_tty = b.tty
        JOIN strategy s ON s.target_tty = p.target_tty AND s.match_type = p.match_type
    ),
    walk(input_order, input_code, technical_name, path_id, target_tty,
         match_type, target_order, step, aui, target_code, target_name,
         target_suppress) AS (
        SELECT input_order, input_code, technical_name,
               path_id, target_tty, match_type, target_order, 0, start_aui,
               CAST(NULL AS VARCHAR), CAST(NULL AS VARCHAR), CAST(NULL AS VARCHAR)
        FROM paths
        UNION ALL
        SELECT w.input_order, w.input_code, w.technical_name,
               w.path_id, w.target_tty, w.match_type, w.target_order,
               w.step + 1, e.target_aui, e.target_code, e.target_name,
               e.target_suppress
        FROM walk w
        JOIN mt4ds.rxnorm_tty_path_steps ps
          ON ps.path_id = w.path_id AND ps.step = w.step + 1
        JOIN mt4ds.rxnorm_tty_edges e
          ON e.source_aui = w.aui AND e.target_tty = ps.tty
        WHERE w.step < (
            SELECT MAX(ps2.step)
            FROM mt4ds.rxnorm_tty_path_steps ps2
            WHERE ps2.path_id = w.path_id
        )
    ),
    hits AS (
        SELECT w.input_order, w.input_code, w.technical_name,
               w.path_id, w.target_tty, w.match_type, w.target_order,
               w.step AS match_depth,
               w.target_code, w.target_name, w.target_suppress
        FROM walk w
        WHERE w.step = (
            SELECT MAX(ps2.step)
            FROM mt4ds.rxnorm_tty_path_steps ps2
            WHERE ps2.path_id = w.path_id
        )
          AND w.step > 0
        UNION ALL
        SELECT p.input_order, p.input_code, p.technical_name,
               p.path_id, p.target_tty, p.match_type, p.target_order,
               0 AS match_depth,
               p.input_code AS target_code, p.technical_name AS target_name,
               'N' AS target_suppress
        FROM paths p
        WHERE p.max_depth = 0
    ),
    ranked AS (
        SELECT *,
            row_number() OVER (
                PARTITION BY input_order
                ORDER BY target_order,
                    CASE target_suppress WHEN 'N' THEN 0 ELSE 1 END,
                    CASE WHEN regexp_matches(target_code, '^[0-9]+$') THEN 0 ELSE 1 END,
                    try_cast(target_code AS BIGINT),
                    target_code,
                    target_name
            ) AS rn
        FROM hits
    )
    SELECT input_order, input_code, technical_name,
           target_code, target_name, target_tty,
           match_type, match_depth
    FROM ranked
    WHERE rn = 1
    ORDER BY input_order
    """

    rows = con.execute(query).fetchall()

    # Build a map from input_order to query result
    hit_map: dict[int, tuple] = {}
    for row in rows:
        hit_map[int(row[0])] = row

    # Now handle special cases for codes not found or IN/MIN self-resolve
    base_map = _get_base_info(codes, con)

    results: list[FriendlyNameResult] = []
    for i, code_ref in enumerate(codes):
        hit = hit_map.get(i)
        if hit is not None:
            (
                _input_order,
                _input_code,
                technical_name,
                target_code,
                target_name,
                target_tty,
                match_type,
                match_depth,
            ) = hit
            if target_code and target_name:
                results.append(
                    FriendlyNameResult(
                        code=code_ref,
                        name=target_name,
                        friendly_source="RXNORM",
                        match_type=match_type,
                        match_depth=int(match_depth or 0),
                        technical_name=technical_name,
                        matched_via=Provenance.from_steps(
                            _STRATEGY,
                            [
                                ProvenanceStep(
                                    op="input",
                                    source="RXNORM",
                                    code=code_ref.code,
                                ),
                                ProvenanceStep(
                                    op="tty_traversal",
                                    source="RXNORM",
                                    code=code_ref.code,
                                    target_source="RXNORM",
                                    target_code=target_code,
                                    tty=target_tty,
                                    depth=int(match_depth or 0),
                                    name=target_name,
                                ),
                            ],
                        ),
                    )
                )
                continue

        # No hit from query -- apply fallback rules
        base_info = base_map.get(i)
        if base_info is None:
            # Code not found in best_atoms at all
            results.append(_make_original(code_ref, _STRATEGY))
            continue

        tty, technical_name = base_info

        # IN and MIN resolve to themselves
        if tty in _SELF_INGREDIENT_TTYS:
            results.append(
                FriendlyNameResult(
                    code=code_ref,
                    name=technical_name or code_ref.code,
                    friendly_source="RXNORM",
                    match_type="ingredient",
                    match_depth=0,
                    technical_name=technical_name,
                    matched_via=Provenance.from_steps(
                        _STRATEGY,
                        [
                            ProvenanceStep(
                                op="input",
                                source="RXNORM",
                                code=code_ref.code,
                                tty=tty,
                            ),
                            ProvenanceStep(
                                op="self_ingredient",
                                source="RXNORM",
                                code=code_ref.code,
                                tty=tty,
                                name=technical_name,
                            ),
                        ],
                    ),
                )
            )
        else:
            results.append(_make_original(code_ref, _STRATEGY, technical_name=technical_name))

    return results


def _get_base_info(
    codes: Sequence[CodeRef],
    con,
) -> dict[int, tuple[str, str]]:
    """Look up base atom TTY and name for fallback logic.

    Returns dict mapping input index to (tty, name).
    """
    if not codes:
        return {}

    input_values = ",\n    ".join(
        f"({_sql_literal(c.code)}, {i})" for i, c in enumerate(codes)
    )

    query = f"""
    WITH input_codes(code, input_order) AS (
        VALUES {input_values}
    )
    SELECT i.input_order, a.tty, a.name
    FROM input_codes i
    JOIN mt4ds.best_atoms a
      ON a.source = 'RXNORM' AND a.code = i.code AND a.rank = 1
    """

    try:
        rows = con.execute(query).fetchall()
    except Exception:
        logger.exception("RxNorm base info query failed")
        return {}

    return {int(row[0]): (str(row[1]), str(row[2])) for row in rows}


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
        friendly_source="RXNORM",
        match_type="original",
        match_depth=0,
        technical_name=technical_name,
        matched_via=Provenance.from_steps(
            strategy,
            [
                ProvenanceStep(
                    op="input",
                    source="RXNORM",
                    code=code_ref.code,
                ),
            ],
        ),
    )
