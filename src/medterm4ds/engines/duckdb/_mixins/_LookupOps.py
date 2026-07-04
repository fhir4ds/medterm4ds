"""Exact-code lookup (get_code_infos, get_code_ttys) and display-name/provenance builders."""


from __future__ import annotations

from medterm4ds.engines.duckdb._engine_base import *  # noqa: F401,F403
from medterm4ds.engines.duckdb import hierarchy as _hierarchy
from medterm4ds.engines.duckdb import patient_friendly as _patient_friendly
from medterm4ds.engines.duckdb import resolution as _resolution
from collections.abc import Sequence
from medterm4ds.core.models import CodeInfo, CodeRef


class _LookupOps:
    """Exact-code lookup (get_code_infos, get_code_ttys) and display-name/provenance builders.

    Mixin for LocalDuckDBEngine — methods share state via ``self`` (``self.con``,
    ``self.cache_prepared``, ``self.query_chunk_size``, etc.). Not intended to be
    instantiated on its own.
    """

    def get_code_infos(self, codes: Sequence[CodeRef]) -> list[CodeInfo | None]:
        """Return canonical active atom info for input codes."""
        if not codes:
            return []

        ordered = [CodeRef(source=code.source, code=code.code) for code in codes]
        grouped: dict[str, list[str]] = defaultdict(list)
        for ref in ordered:
            grouped[ref.source].append(ref.code)

        use_prepared = self._has_prepared_tables()

        lookup: dict[tuple[str, str], CodeInfo] = {}
        for source, source_codes in grouped.items():
            with self._temp_codes(source_codes) as temp:
                if use_prepared:
                    rows = self.con.execute(
                        f"""
                        SELECT code, name, cui, aui, tty, suppress
                        FROM mt4ds.best_atoms
                        WHERE source = ?
                          AND rank = 1
                          AND is_active = true
                          AND code IN (SELECT code FROM {temp})
                        """,
                        [source],
                    ).fetchall()
                else:
                    rows = self.con.execute(
                        f"""
                        WITH ranked AS (
                            SELECT CODE, STR, CUI, AUI, TTY, SUPPRESS,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY CODE
                                       ORDER BY
                                           CASE WHEN SUPPRESS = 'N' THEN 0 ELSE 1 END,
                                           CASE TTY
                                               WHEN 'PT' THEN 0
                                               WHEN 'MH' THEN 1
                                               WHEN 'LN' THEN 2
                                               ELSE 3
                                           END,
                                           AUI
                                   ) AS rn
                            FROM mrconso
                            WHERE SAB = ?
                              AND SUPPRESS = 'N'
                              AND CODE IN (SELECT code FROM {temp})
                        )
                        SELECT CODE, STR, CUI, AUI, TTY, SUPPRESS
                        FROM ranked
                        WHERE rn = 1
                        """,
                        [source],
                    ).fetchall()
            for code, name, cui, aui, tty, suppress in rows:
                lookup[(source, code)] = CodeInfo(
                    code=CodeRef(source=source, code=code),
                    name=name,
                    cui=cui,
                    aui=aui,
                    tty=tty,
                    suppress=suppress,
                )

        return [lookup.get((ref.source, ref.code)) for ref in ordered]



    def get_code_ttys(self, codes: Sequence[CodeRef]) -> list[CodeInfo]:
        """Return active atoms and TTYs for input codes."""
        if not codes:
            return []
        ordered = [CodeRef(source=code.source, code=code.code) for code in codes]
        grouped: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for ordinal, ref in enumerate(ordered):
            grouped[ref.source].append((ordinal, ref.code))

        rows: list[tuple[int, CodeInfo]] = []
        for source, code_ordinals in grouped.items():
            with self._temp_code_ordinals(code_ordinals) as temp:
                if self._table_exists("atoms"):
                    source_rows = self.con.execute(
                        f"""
                        SELECT i.ordinal, a.code, a.name, a.cui, a.aui, a.tty, a.suppress
                        FROM {temp} i
                        JOIN mt4ds.atoms a ON a.code = i.code
                        WHERE a.source = ?
                          AND a.is_active = true
                        ORDER BY i.ordinal,
                                 CASE a.tty
                                     WHEN 'PT' THEN 0
                                     WHEN 'MH' THEN 1
                                     WHEN 'LN' THEN 2
                                     ELSE 3
                                 END,
                                 a.tty,
                                 a.aui
                        """,
                        [source],
                    ).fetchall()
                else:
                    source_rows = self.con.execute(
                        f"""
                        SELECT i.ordinal, c.CODE, c.STR, c.CUI, c.AUI, c.TTY, c.SUPPRESS
                        FROM {temp} i
                        JOIN mrconso c ON c.CODE = i.code
                        WHERE c.SAB = ?
                          AND c.SUPPRESS = 'N'
                        ORDER BY i.ordinal,
                                 CASE c.TTY
                                     WHEN 'PT' THEN 0
                                     WHEN 'MH' THEN 1
                                     WHEN 'LN' THEN 2
                                     ELSE 3
                                 END,
                                 c.TTY,
                                 c.AUI
                        """,
                        [source],
                    ).fetchall()
            rows.extend(
                (
                    int(ordinal),
                    CodeInfo(
                        code=CodeRef(source=source, code=code),
                        name=name,
                        cui=cui,
                        aui=aui,
                        tty=tty,
                        suppress=suppress,
                    ),
                )
                for ordinal, code, name, cui, aui, tty, suppress in source_rows
            )
        return [info for _ordinal, info in sorted(rows, key=lambda item: item[0])]



    def _resolve_code(self, ref):
        return _resolution._resolve_code(self, ref=ref)




    def _lookup_any_code(self, ref):
        return _resolution._lookup_any_code(self, ref=ref)




    def _source_display_lookup(
        self,
        source: str,
        codes: Sequence[str],
    ) -> dict[str, tuple[str, str, str]]:
        return _hierarchy.source_display_lookup(self, source, codes)




    def _display_name(self, code, source):
        return _patient_friendly._display_name(self, code=code, source=source)




    def _technical_name(self, code, source):
        return _patient_friendly._technical_name(self, code=code, source=source)




    def _make_original(self, code, source, *, technical_name=None, display_name=None):
        return _patient_friendly._make_original(self, code=code, source=source, technical_name=technical_name, display_name=display_name)




    def _make_none(self, code: str, source: str) -> _Row:
        return _Row(
            code=code,
            source=source,
            name=code,
            friendly_source=source,
            match_type="none",
            match_depth=0,
            matched_via=Provenance.from_steps(
                "none",
                [ProvenanceStep(op="input", source=source, code=code)],
            ),
        )



    def _simple_provenance(self, strategy: str, source: str, code: str, name: str) -> Provenance:
        return Provenance.from_steps(
            strategy,
            [
                ProvenanceStep(op="input", source=source, code=code),
                ProvenanceStep(op="friendly_name", source=source, code=code, name=name),
            ],
        )



    def _provenance(
        self,
        strategy: str,
        code: CodeRef,
        *,
        friendly_source: str,
        friendly_name: str,
        depth: int,
        tty: str | None = None,
        cui: str | None = None,
    ) -> Provenance:
        steps = [
            ProvenanceStep(op="input", source=code.source, code=code.code),
        ]
        if depth > 0:
            steps.append(ProvenanceStep(op="ancestor", source=code.source, code=code.code, depth=depth))
        steps.append(
            ProvenanceStep(
                op="friendly_atom",
                source=friendly_source,
                name=friendly_name,
                tty=tty,
                cui=cui,
                depth=depth,
            )
        )
        return Provenance.from_steps(strategy, steps)

