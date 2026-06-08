"""Source-to-source crosswalk over normalized mt4ds tables.

Provides direct access to same-CUI cross-source mappings using
``mt4ds.crosswalk_edges`` when available, with ``mt4ds.same_cui_edges`` as a
compatibility fallback.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from medterm4ds.core.models import CodeMapping, CodeRef
from medterm4ds.services.prepared_primitives import (
    same_cui_crosswalk_sql,
    temp_codes,
)


def get_same_cui_mappings(
    codes: Sequence[CodeRef],
    con,
    *,
    target_sources: Sequence[str] | None = None,
) -> list[CodeMapping]:
    """Same-CUI crosswalk using canonical mt4ds.crosswalk_edges when present.

    Falls back to legacy ``mt4ds.same_cui_edges`` for older prepared DBs.

    Parameters
    ----------
    codes:
        Input codes to crosswalk.
    con:
        DuckDB connection with ``mt4ds.crosswalk_edges`` or legacy
        ``mt4ds.same_cui_edges`` available.
    target_sources:
        If provided, only return mappings to these target sources.

    Returns
    -------
    list[CodeMapping]
        Cross-source mappings linked by shared CUI.
    """
    if not codes:
        return []

    # Group by source for batch queries
    grouped: dict[str, list[str]] = defaultdict(list)
    for code in codes:
        grouped[code.source].append(code.code)

    results: list[CodeMapping] = []
    edge_table, match_filter = same_cui_crosswalk_sql(con)

    for source, source_codes in grouped.items():
        deduped = list(dict.fromkeys(source_codes))

        with temp_codes(con, deduped, prefix="_mt4ds_xw_codes") as temp:
            if target_sources:
                rows = con.execute(
                    f"""
                    SELECT sce.source, sce.code, sce.cui,
                           sce.target_source, sce.target_code,
                           sce.target_aui, sce.target_cui, sce.target_tty
                    FROM {edge_table} sce
                    WHERE sce.source = ?
                      AND sce.code IN (SELECT code FROM {temp})
                      AND sce.target_source IN (SELECT unnest(?))
                      {match_filter}
                    """,
                    [source, list(target_sources)],
                ).fetchall()
            else:
                rows = con.execute(
                    f"""
                    SELECT sce.source, sce.code, sce.cui,
                           sce.target_source, sce.target_code,
                           sce.target_aui, sce.target_cui, sce.target_tty
                    FROM {edge_table} sce
                    WHERE sce.source = ?
                      AND sce.code IN (SELECT code FROM {temp})
                      {match_filter}
                    """,
                    [source],
                ).fetchall()

        for (
            src_source, src_code, src_cui,
            tgt_source, tgt_code, tgt_aui, tgt_cui, tgt_tty,
        ) in rows:
            results.append(
                CodeMapping(
                    source=CodeRef(source=src_source, code=src_code),
                    target=CodeRef(source=tgt_source, code=tgt_code),
                    relationship="equivalent",
                    match_type="same_cui",
                    source_cui=src_cui,
                    target_cui=tgt_cui,
                    target_aui=tgt_aui,
                    target_tty=tgt_tty,
                )
            )

    return results
