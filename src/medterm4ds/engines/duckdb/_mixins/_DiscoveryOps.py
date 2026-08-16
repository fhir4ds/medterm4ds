"""Source inventory, sample codes, and active-name search."""


from __future__ import annotations

from medterm4ds.engines.duckdb._engine_base import *  # noqa: F401,F403
from collections.abc import Sequence
from medterm4ds.core.models import CodeRef, NameSearchResult, SourceStats


class _DiscoveryOps:
    """Source inventory, sample codes, and active-name search.

    Mixin for LocalDuckDBEngine — methods share state via ``self`` (``self.con``,
    ``self.cache_prepared``, ``self.query_chunk_size``, etc.). Not intended to be
    instantiated on its own.
    """

    def get_source_stats(self, sources: Sequence[str] | None = None) -> list[SourceStats]:
        """Return active code and atom counts by source."""
        params: list[object] = []
        source_filter = ""
        if sources:
            normalized_sources = _dedupe(sources)
            placeholders = ",".join(["?"] * len(normalized_sources))
            source_filter = f"AND SAB IN ({placeholders})"
            params.extend(normalized_sources)
        if self._table_exists("atoms"):
            if sources:
                source_filter = f"AND source IN ({placeholders})"
            rows = self.con.execute(
                f"""
                SELECT source, COUNT(DISTINCT code) AS code_count, COUNT(*) AS atom_count
                FROM mt4ds.atoms
                WHERE is_active = true
                  AND code IS NOT NULL
                  AND code != ''
                  {source_filter}
                GROUP BY source
                ORDER BY source
                """,
                params,
            ).fetchall()
        else:
            rows = self.con.execute(
                f"""
                SELECT SAB, COUNT(DISTINCT CODE) AS code_count, COUNT(*) AS atom_count
                FROM mrconso
                WHERE SUPPRESS = 'N'
                  AND CODE IS NOT NULL
                  AND CODE != ''
                  {source_filter}
                GROUP BY SAB
                ORDER BY SAB
                """,
                params,
            ).fetchall()
        return [
            SourceStats(source=source, code_count=int(code_count), atom_count=int(atom_count))
            for source, code_count, atom_count in rows
        ]



    def sample_source_codes(
        self,
        sources: Sequence[str],
        *,
        per_source: int = 10,
    ) -> list[CodeRef]:
        """Return sample active codes by source."""
        if per_source < 1:
            raise ValueError("per_source must be at least 1")
        if not sources:
            return []
        normalized_sources = _dedupe(sources)
        placeholders = ",".join(["?"] * len(normalized_sources))
        if self._table_exists("best_atoms"):
            rows = self.con.execute(
                f"""
                WITH ranked AS (
                    SELECT source, code,
                           ROW_NUMBER() OVER (PARTITION BY source ORDER BY code) AS rn
                    FROM (
                        SELECT source, code
                        FROM mt4ds.best_atoms
                        WHERE is_active = true
                          AND rank = 1
                          AND code IS NOT NULL
                          AND code != ''
                          AND source IN ({placeholders})
                        GROUP BY source, code
                    )
                )
                SELECT source, code
                FROM ranked
                WHERE rn <= ?
                ORDER BY source, code
                """,
                [*normalized_sources, per_source],
            ).fetchall()
        else:
            rows = self.con.execute(
                f"""
                WITH ranked AS (
                    SELECT SAB, CODE,
                           ROW_NUMBER() OVER (PARTITION BY SAB ORDER BY CODE) AS rn
                    FROM (
                        SELECT SAB, CODE
                        FROM mrconso
                        WHERE SUPPRESS = 'N'
                          AND CODE IS NOT NULL
                          AND CODE != ''
                          AND SAB IN ({placeholders})
                        GROUP BY SAB, CODE
                    )
                )
                SELECT SAB, CODE
                FROM ranked
                WHERE rn <= ?
                ORDER BY SAB, CODE
                """,
                [*normalized_sources, per_source],
            ).fetchall()
        return [CodeRef(source=source, code=code) for source, code in rows]



    def search_names(
        self,
        query: str,
        *,
        sources: Sequence[str] | None = None,
        tty_filters: Sequence[str] | None = None,
        limit: int = 25,
    ) -> list[NameSearchResult]:
        """Search active atom names."""
        stripped_query = query.strip()
        if not stripped_query:
            raise ValueError("query must not be empty")
        if limit < 1:
            raise ValueError("limit must be at least 1")

        use_prepared_atoms = self._table_exists("atoms")
        if use_prepared_atoms:
            table_name = "mt4ds.atoms"
            source_col = "source"
            code_col = "code"
            name_col = "name"
            cui_col = "cui"
            aui_col = "aui"
            tty_col = "tty"
            filters = [
                "is_active = true",
                "code IS NOT NULL",
                "code != ''",
                "name IS NOT NULL",
            ]
        else:
            table_name = "mrconso"
            source_col = "SAB"
            code_col = "CODE"
            name_col = "STR"
            cui_col = "CUI"
            aui_col = "AUI"
            tty_col = "TTY"
            filters = ["SUPPRESS = 'N'", "CODE IS NOT NULL", "CODE != ''", "STR IS NOT NULL"]
        filter_params: list[object] = []
        if sources:
            normalized_sources = _dedupe(sources)
            # QC-424 (MEDIUM): a typo'd source filter previously returned
            # silent 0-row success — indistinguishable from a legitimate
            # no-match query. Probe the exact table this search reads (with
            # no active filter — a source whose atoms are all suppressed
            # still EXISTS). Reject only when NO requested source is
            # present: candidate lists (domain wrappers, FHIR $expand
            # defaults) legitimately over-include sources a given database
            # may not carry, and those must keep returning the present ones.
            probe_placeholders = ",".join(["?"] * len(normalized_sources))
            found_sources = {
                row[0]
                for row in self.con.execute(
                    f"SELECT DISTINCT {source_col} FROM {table_name} "
                    f"WHERE {source_col} IN ({probe_placeholders})",
                    list(normalized_sources),
                ).fetchall()
            }
            if not found_sources:
                raise ValueError(
                    "source(s) not found in this database: "
                    + ", ".join(repr(s) for s in normalized_sources)
                )
            filters.append(f"{source_col} IN ({','.join(['?'] * len(normalized_sources))})")
            filter_params.extend(normalized_sources)
        if tty_filters:
            normalized_ttys = _dedupe([tty.upper() for tty in tty_filters])
            filters.append(f"{tty_col} IN ({','.join(['?'] * len(normalized_ttys))})")
            filter_params.extend(normalized_ttys)

        lowered_query = stripped_query.lower()
        # QC-218: escape LIKE metacharacters so user input is matched
        # literally. A bare '%' query previously matched ~all 10M active
        # atoms; the ranking CTE materialized and sorted the full match set
        # (38.8s at limit=5) and concurrent wildcard queries exhausted
        # duckdb temp storage. DuckDB has no default LIKE escape character,
        # so each LIKE below carries an explicit ESCAPE '\'.
        escaped_query = (
            lowered_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        prefix_pattern = f"{escaped_query}%"
        contains_pattern = f"%{escaped_query}%"

        rows = self.con.execute(
            f"""
            WITH ranked AS (
                SELECT {source_col} AS SAB, {code_col} AS CODE,
                       {name_col} AS STR, {cui_col} AS CUI,
                       {aui_col} AS AUI, {tty_col} AS TTY,
                       CASE
                           WHEN LOWER({name_col}) = ? THEN 'exact'
                           WHEN LOWER({name_col}) LIKE ? ESCAPE '\\' THEN 'prefix'
                           ELSE 'contains'
                       END AS match_type,
                       ROW_NUMBER() OVER (
                           PARTITION BY {source_col}, {code_col}
                           ORDER BY
                               CASE
                                   WHEN LOWER({name_col}) = ? THEN 0
                                   WHEN LOWER({name_col}) LIKE ? ESCAPE '\\' THEN 1
                                   ELSE 2
                               END,
                               CASE {tty_col}
                                   WHEN 'PT' THEN 0
                                   WHEN 'MH' THEN 1
                                   WHEN 'LN' THEN 2
                                   ELSE 3
                               END,
                               LENGTH({name_col}),
                               {aui_col}
                       ) AS atom_rn
                FROM {table_name}
                WHERE {' AND '.join(filters)}
                  AND LOWER({name_col}) LIKE ? ESCAPE '\\'
            ),
            deduped AS (
                SELECT *
                FROM ranked
                WHERE atom_rn = 1
            )
            SELECT SAB, CODE, STR, CUI, AUI, TTY, match_type
            FROM deduped
            ORDER BY
                CASE match_type
                    WHEN 'exact' THEN 0
                    WHEN 'prefix' THEN 1
                    ELSE 2
                END,
                LENGTH(STR),
                SAB,
                CODE
            LIMIT ?
            """,
            [
                lowered_query,
                prefix_pattern,
                lowered_query,
                prefix_pattern,
                *filter_params,
                contains_pattern,
                limit,
            ],
        ).fetchall()
        return [
            NameSearchResult(
                code=CodeRef(source=source, code=code),
                name=name,
                cui=cui,
                aui=aui,
                tty=tty,
                match_type=match_type,
            )
            for source, code, name, cui, aui, tty, match_type in rows
        ]

