"""Code resolution subsystem for the local DuckDB engine.

Extracted from engines/duckdb/engine.py (Phase 4 of Tier C refactor). These
functions resolve input codes to their canonical active form, including
historical/obsolete replacement, NDC normalization, and active-code lookup.

Functions take the engine instance as their first parameter to access the
DuckDB connection and helpers. Same pattern as hierarchy.py and mappings.py.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from medterm4ds.core.models import CodeInfo, CodeRef, CodeResolution, Provenance, ProvenanceStep


@dataclass(frozen=True)
class _ReplacementCandidate:
    code: CodeRef
    name: str | None
    cui: str | None
    aui: str | None
    tty: str | None
    suppress: str | None
    relationship: str | None

def _resolve_code(engine, ref: CodeRef) -> CodeResolution:
    if ref.source == "NDC":
        return engine._resolve_ndc(ref)

    active = engine.get_code_infos([ref])[0]
    if active is not None:
        return CodeResolution(
            input=ref,
            resolved=ref,
            status="active",
            match_type="active_exact",
            input_display=active.name,
            resolved_display=active.name,
            input_cui=active.cui,
            resolved_cui=active.cui,
            input_aui=active.aui,
            resolved_aui=active.aui,
            input_suppress=active.suppress,
            resolved_suppress=active.suppress,
            matched_via=Provenance.from_steps(
                "active_exact",
                [
                    ProvenanceStep(op="input", source=ref.source, code=ref.code),
                    ProvenanceStep(
                        op="active_atom",
                        source=ref.source,
                        code=ref.code,
                        cui=active.cui,
                        aui=active.aui,
                        tty=active.tty,
                        name=active.name,
                    ),
                ],
            ),
        )

    historical = engine._lookup_any_code(ref)
    if historical is None:
        return CodeResolution(
            input=ref,
            resolved=None,
            status="not_found",
            match_type="not_found",
            matched_via=Provenance.from_steps(
                "not_found",
                [ProvenanceStep(op="input", source=ref.source, code=ref.code)],
            ),
        )

    replacements = engine._replacement_candidates(historical)
    if len(replacements) == 1:
        replacement = replacements[0]
        return CodeResolution(
            input=ref,
            resolved=replacement.code,
            status="replaced",
            match_type="historical_replacement",
            input_display=historical.name,
            resolved_display=replacement.name,
            input_cui=historical.cui,
            resolved_cui=replacement.cui,
            input_aui=historical.aui,
            resolved_aui=replacement.aui,
            input_suppress=historical.suppress,
            resolved_suppress=replacement.suppress,
            replacement_relationship=replacement.relationship,
            candidates=(replacement.code,),
            matched_via=Provenance.from_steps(
                "historical_replacement",
                [
                    ProvenanceStep(op="input", source=ref.source, code=ref.code),
                    ProvenanceStep(
                        op="historical_atom",
                        source=ref.source,
                        code=ref.code,
                        cui=historical.cui,
                        aui=historical.aui,
                        tty=historical.tty,
                        name=historical.name,
                        metadata={"suppress": historical.suppress},
                    ),
                    ProvenanceStep(
                        op="replacement",
                        source=ref.source,
                        code=ref.code,
                        target_source=replacement.code.source,
                        target_code=replacement.code.code,
                        mode=replacement.relationship,
                        name=replacement.name,
                    ),
                ],
            ),
        )
    if len(replacements) > 1:
        return CodeResolution(
            input=ref,
            resolved=None,
            status="ambiguous",
            match_type="multiple_historical_replacements",
            input_display=historical.name,
            input_cui=historical.cui,
            input_aui=historical.aui,
            input_suppress=historical.suppress,
            candidates=tuple(replacement.code for replacement in replacements),
            matched_via=Provenance.from_steps(
                "multiple_historical_replacements",
                [
                    ProvenanceStep(op="input", source=ref.source, code=ref.code),
                    ProvenanceStep(
                        op="historical_atom",
                        source=ref.source,
                        code=ref.code,
                        cui=historical.cui,
                        aui=historical.aui,
                        tty=historical.tty,
                        name=historical.name,
                        metadata={"suppress": historical.suppress},
                    ),
                ],
            ),
        )

    status = "historical" if historical.suppress in {"O", "E"} else "suppressed"
    return CodeResolution(
        input=ref,
        resolved=ref,
        status=status,
        match_type="historical_exact",
        input_display=historical.name,
        resolved_display=historical.name,
        input_cui=historical.cui,
        resolved_cui=historical.cui,
        input_aui=historical.aui,
        resolved_aui=historical.aui,
        input_suppress=historical.suppress,
        resolved_suppress=historical.suppress,
        matched_via=Provenance.from_steps(
            "historical_exact",
            [
                ProvenanceStep(op="input", source=ref.source, code=ref.code),
                ProvenanceStep(
                    op="historical_atom",
                    source=ref.source,
                    code=ref.code,
                    cui=historical.cui,
                    aui=historical.aui,
                    tty=historical.tty,
                    name=historical.name,
                    metadata={"suppress": historical.suppress},
                ),
            ],
        ),
    )

def _active_source_code_set(engine, source: str) -> set[str]:
    cached = engine._active_source_code_cache.get(source)
    if cached is not None:
        return cached
    rows = engine.con.execute(
        """
        SELECT DISTINCT CODE
        FROM mrconso
        WHERE SAB = ?
          AND SUPPRESS = 'N'
          AND CODE IS NOT NULL
          AND CODE != ''
        """,
        [source],
    ).fetchall()
    active_codes = {str(row[0]) for row in rows}
    engine._active_source_code_cache[source] = active_codes
    return active_codes

def _resolve_ndc(engine, ref: CodeRef) -> CodeResolution:
    from medterm4ds.engines.duckdb.engine import _ndc_candidates
    candidates = _ndc_candidates(ref.code)
    if not candidates:
        return CodeResolution(
            input=ref,
            resolved=None,
            status="not_found",
            match_type="invalid_ndc",
            matched_via=Provenance.from_steps(
                "invalid_ndc",
                [ProvenanceStep(op="input", source=ref.source, code=ref.code)],
            ),
        )

    rows = []
    if engine._table_exists("mrsat"):
        placeholders = ",".join(["?"] * len(candidates))
        rows = engine.con.execute(
            f"""
            WITH ranked AS (
                SELECT s.ATV AS ndc, c.CODE, c.STR, c.CUI, c.AUI, c.TTY, c.SUPPRESS,
                       ROW_NUMBER() OVER (
                           PARTITION BY s.ATV, c.CODE
                           ORDER BY
                               CASE WHEN c.SUPPRESS = 'N' THEN 0 ELSE 1 END,
                               CASE c.TTY
                                   WHEN 'SCD' THEN 0
                                   WHEN 'SBD' THEN 1
                                   WHEN 'GPCK' THEN 2
                                   WHEN 'BPCK' THEN 3
                                   WHEN 'PSN' THEN 4
                                   ELSE 5
                               END,
                               c.AUI
                       ) AS rn
                FROM mrsat s
                JOIN mrconso c ON c.SAB = 'RXNORM' AND c.CODE = s.CODE
                WHERE s.SAB = 'RXNORM'
                  AND s.ATN = 'NDC'
                  AND s.ATV IN ({placeholders})
            )
            SELECT ndc, CODE, STR, CUI, AUI, TTY, SUPPRESS
            FROM ranked
            WHERE rn = 1
            ORDER BY
                CASE WHEN SUPPRESS = 'N' THEN 0 ELSE 1 END,
                ndc,
                CODE
            """,
            candidates,
        ).fetchall()

    active_rows = [row for row in rows if row[6] == "N"]
    selected_rows = active_rows or rows
    if len({row[1] for row in selected_rows}) == 1 and selected_rows:
        ndc, rxcui, name, cui, aui, tty, suppress = selected_rows[0]
        status = "ndc_resolved" if suppress == "N" else "historical"
        return CodeResolution(
            input=ref,
            resolved=CodeRef("RXNORM", rxcui),
            status=status,
            match_type="ndc_to_rxcui",
            input_display=ndc,
            resolved_display=name,
            resolved_cui=cui,
            resolved_aui=aui,
            resolved_suppress=suppress,
            normalized_code=ndc,
            candidates=(CodeRef("RXNORM", rxcui),),
            matched_via=Provenance.from_steps(
                "ndc_to_rxcui",
                [
                    ProvenanceStep(op="input", source=ref.source, code=ref.code),
                    ProvenanceStep(op="normalize_ndc", source="NDC", code=ndc),
                    ProvenanceStep(
                        op="rxnorm_ndc_attribute",
                        source="NDC",
                        code=ndc,
                        target_source="RXNORM",
                        target_code=rxcui,
                        cui=cui,
                        aui=aui,
                        tty=tty,
                        name=name,
                        metadata={"suppress": suppress},
                    ),
                ],
            ),
        )
    if selected_rows:
        candidate_refs = tuple(CodeRef("RXNORM", row[1]) for row in selected_rows)
        return CodeResolution(
            input=ref,
            resolved=None,
            status="ambiguous",
            match_type="multiple_ndc_rxcui_candidates",
            normalized_code=selected_rows[0][0],
            candidates=candidate_refs,
            matched_via=Provenance.from_steps(
                "multiple_ndc_rxcui_candidates",
                [
                    ProvenanceStep(op="input", source=ref.source, code=ref.code),
                    *[
                        ProvenanceStep(
                            op="rxnorm_ndc_candidate",
                            source="NDC",
                            code=row[0],
                            target_source="RXNORM",
                            target_code=row[1],
                            name=row[2],
                            metadata={"suppress": row[6]},
                        )
                        for row in selected_rows[:20]
                    ],
                ],
            ),
        )

    return CodeResolution(
        input=ref,
        resolved=None,
        status="not_found",
        match_type="ndc_not_found",
        normalized_code=candidates[0],
        matched_via=Provenance.from_steps(
            "ndc_not_found",
            [
                ProvenanceStep(op="input", source=ref.source, code=ref.code),
                *[
                    ProvenanceStep(op="normalize_ndc_candidate", source="NDC", code=candidate)
                    for candidate in candidates
                ],
            ],
        ),
    )

def _lookup_any_code(engine, ref: CodeRef) -> CodeInfo | None:
    if engine._table_exists("atoms"):
        rows = engine.con.execute(
            """
            SELECT code, name, cui, aui, tty, suppress
            FROM mt4ds.atoms
            WHERE source = ?
              AND code = ?
            ORDER BY
                CASE suppress
                    WHEN 'N' THEN 0
                    WHEN 'O' THEN 1
                    WHEN 'E' THEN 2
                    ELSE 3
                END,
                CASE tty
                    WHEN 'PT' THEN 0
                    WHEN 'MH' THEN 1
                    WHEN 'LN' THEN 2
                    ELSE 3
                END,
                aui
            LIMIT 1
            """,
            [ref.source, ref.code],
        ).fetchone()
        if rows is not None:
            code, name, cui, aui, tty, suppress = rows
            return CodeInfo(
                code=CodeRef(ref.source, code),
                name=name,
                cui=cui,
                aui=aui,
                tty=tty,
                suppress=suppress,
            )

    rows = engine.con.execute(
        """
        SELECT CODE, STR, CUI, AUI, TTY, SUPPRESS
        FROM mrconso
        WHERE SAB = ?
          AND CODE = ?
        ORDER BY
            CASE SUPPRESS
                WHEN 'N' THEN 0
                WHEN 'O' THEN 1
                WHEN 'E' THEN 2
                ELSE 3
            END,
            CASE TTY
                WHEN 'PT' THEN 0
                WHEN 'MH' THEN 1
                WHEN 'LN' THEN 2
                ELSE 3
            END,
            AUI
        LIMIT 1
        """,
        [ref.source, ref.code],
    ).fetchone()
    if rows is None:
        return None
    code, name, cui, aui, tty, suppress = rows
    return CodeInfo(
        code=CodeRef(ref.source, code),
        name=name,
        cui=cui,
        aui=aui,
        tty=tty,
        suppress=suppress,
    )

def _replacement_candidates(engine, historical: CodeInfo) -> list[_ReplacementCandidate]:
    from medterm4ds.engines.duckdb.engine import _REPLACEMENT_RELAS
    if engine._table_exists("code_replacements") and engine._table_exists("best_atoms"):
        rows = engine.con.execute(
            """
            SELECT b.code, b.name, b.cui, b.aui, b.tty, b.suppress, r.rela
            FROM mt4ds.code_replacements r
            JOIN mt4ds.best_atoms b
              ON b.source = r.source
             AND b.code = r.new_code
            WHERE r.source = ?
              AND r.old_code = ?
              AND b.is_active
            ORDER BY
                CASE r.rela
                    WHEN 'same_as' THEN 0
                    WHEN 'replaced_by' THEN 1
                    ELSE 2
                END,
                b.code
            LIMIT 25
            """,
            [historical.code.source, historical.code.code],
        ).fetchall()
        return [
            _ReplacementCandidate(
                code=CodeRef(historical.code.source, code),
                name=name,
                cui=cui,
                aui=aui,
                tty=tty,
                suppress=suppress,
                relationship=rela,
            )
            for code, name, cui, aui, tty, suppress, rela in rows
        ]

    if not historical.aui:
        return []
    rela_placeholders = ",".join(["?"] * len(_REPLACEMENT_RELAS))
    params: list[object] = [
        historical.aui,
        historical.code.source,
        *_REPLACEMENT_RELAS,
        historical.aui,
        historical.code.source,
        *_REPLACEMENT_RELAS,
    ]
    rows = engine.con.execute(
        f"""
        WITH candidates AS (
            SELECT c.CODE, c.STR, c.CUI, c.AUI, c.TTY, c.SUPPRESS, r.RELA
            FROM mrrel r
            JOIN mrconso c ON c.AUI = r.AUI2
            WHERE r.AUI1 = ?
              AND c.SAB = ?
              AND c.SUPPRESS = 'N'
              AND r.RELA IN ({rela_placeholders})
            UNION ALL
            SELECT c.CODE, c.STR, c.CUI, c.AUI, c.TTY, c.SUPPRESS, r.RELA
            FROM mrrel r
            JOIN mrconso c ON c.AUI = r.AUI1
            WHERE r.AUI2 = ?
              AND c.SAB = ?
              AND c.SUPPRESS = 'N'
              AND r.RELA IN ({rela_placeholders})
        ),
        ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY CODE
                       ORDER BY
                           CASE RELA
                               WHEN 'same_as' THEN 0
                               WHEN 'replaced_by' THEN 1
                               ELSE 2
                           END,
                           CASE TTY
                               WHEN 'PT' THEN 0
                               WHEN 'MH' THEN 1
                               WHEN 'LN' THEN 2
                               ELSE 3
                           END,
                           AUI
                   ) AS rn
            FROM candidates
        )
        SELECT CODE, STR, CUI, AUI, TTY, SUPPRESS, RELA
        FROM ranked
        WHERE rn = 1
        ORDER BY CODE
        LIMIT 25
        """,
        params,
    ).fetchall()
    output: list[_ReplacementCandidate] = []
    for code, name, cui, aui, tty, suppress, rela in rows:
        output.append(_ReplacementCandidate(
            code=CodeRef(historical.code.source, code),
            name=name,
            cui=cui,
            aui=aui,
            tty=tty,
            suppress=suppress,
            relationship=rela,
        ))
    return output
