"""DuckDB-only LocalLite patient-friendly engine.

The engine is batch-first and keeps large data in DuckDB. It uses temp input
tables instead of large Python-side object graphs.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar
from uuid import uuid4

from medterm4ds.core.config import LocalLiteConfig
from medterm4ds.core.models import (
    CodeInfo,
    CodeMapping,
    CodeRef,
    CodeRelation,
    FriendlyNameResult,
    NameSearchResult,
    Provenance,
    ProvenanceStep,
    SourceStats,
)

logger = logging.getLogger(__name__)
T = TypeVar("T")

_SNOMED_FALLBACK_SOURCES = {"ICD10CM", "ICD10PCS", "LNC"}
_SNOMED_TARGET_PRIORITY = {"ICD10CM": 0, "RXNORM": 1, "LNC": 2, "CPT": 3}
_CPT_TARGET_PRIORITY = {"HCPCS": 0, "ICD10CM": 1, "SNOMEDCT_US": 2}
_SNOMED_TOP_LEVEL_GUARD_DEPTH = 3
_HIERARCHY_RELATIONSHIPS = {
    "parents": "parent",
    "children": "child",
    "ancestors": "ancestor",
    "descendants": "descendant",
}

_BROAD_CHV_NAMES = {
    "clinical findings",
    "clinical investigation",
    "cpt",
    "operation",
    "operations",
    "sign and symptom",
    "signs and symptoms",
    "symptoms and signs",
    "finding",
    "findings",
    "symptom",
    "symptoms",
}
_BROAD_MEDLINEPLUS_NAMES = {
    "anatomy",
    "body structure",
    "body structures",
    "clinical finding",
    "disease inflammatory",
    "finding",
    "findings",
    "physical finding",
    "procedure",
}
_BLACKLIST_LOINC = frozenset({
    "I",
    "A",
    "IgE",
    "IgG",
    "Specimen",
    "Activity",
    "Multisection",
    "Nuclear",
    "E",
    "G Ab",
})
_COMBO_SEP_HINTS = (" and ", "/", " + ", " with ")
_COMBO_TERM_STOPWORDS = {
    "and",
    "with",
    "only",
    "product",
    "tablet",
    "tablets",
    "injection",
    "oral",
    "inhaler",
    "solution",
    "powder",
    "spray",
    "ointment",
    "cream",
    "gel",
    "patch",
    "sustained",
    "release",
    "extended",
    "drug",
    "therapy",
    "combination",
    "strength",
    "strengths",
    "mg",
    "mcg",
    "iu",
    "units",
    "unit",
    "percent",
    "ml",
    "l",
    "meq",
}
_RXNORM_GROUP_TTYS = {
    "SCD",
    "SBD",
    "SCDF",
    "SBDF",
    "GPCK",
    "BPCK",
    "SBDG",
    "SCDG",
    "SBDC",
    "DFG",
}


@dataclass
class _Row:
    code: str
    source: str
    name: str
    friendly_source: str
    match_type: str
    match_depth: int = 0
    technical_name: str | None = None
    matched_via: Provenance | None = None

    def result(self) -> FriendlyNameResult:
        return FriendlyNameResult(
            code=CodeRef(source=self.source, code=self.code),
            name=self.name,
            friendly_source=self.friendly_source,
            match_type=self.match_type,
            match_depth=self.match_depth,
            technical_name=self.technical_name,
            matched_via=self.matched_via,
        )


class LocalLiteEngine:
    """Low-memory DuckDB engine for patient-friendly batch resolution."""

    def __init__(
        self,
        con,
        *,
        config: LocalLiteConfig | None = None,
        memory_limit: str | None = None,
        temp_directory: str | Path | None = None,
        threads: int | None = None,
        preserve_insertion_order: bool | None = None,
        query_chunk_size: int | None = None,
        progress: Callable[[str], None] | None = None,
        cvx_groups: Mapping[str, Sequence[str]] | None = None,
    ):
        if config:
            memory_limit = config.memory_limit if memory_limit is None else memory_limit
            temp_directory = config.temp_directory if temp_directory is None else temp_directory
            threads = config.threads if threads is None else threads
            if preserve_insertion_order is None:
                preserve_insertion_order = config.preserve_insertion_order
            query_chunk_size = config.query_chunk_size if query_chunk_size is None else query_chunk_size
        if preserve_insertion_order is None:
            preserve_insertion_order = False
        if query_chunk_size is None:
            query_chunk_size = 5000

        self.con = con
        self.cvx_groups = {str(k): list(v) for k, v in (cvx_groups or {}).items()}
        self.query_chunk_size = max(1, int(query_chunk_size))
        self.progress = progress
        self.cache_prepared = False
        self.con.execute(f"SET preserve_insertion_order={str(preserve_insertion_order).lower()}")
        if threads:
            self.con.execute(f"PRAGMA threads={int(threads)}")
        if memory_limit:
            self.con.execute(f"PRAGMA memory_limit='{memory_limit}'")
        if temp_directory:
            self.con.execute(f"PRAGMA temp_directory='{Path(temp_directory)}'")

    def prepare_cache(
        self,
        sources: Sequence[str] = (
            "ICD10CM",
            "ICD10PCS",
            "HCPCS",
            "SNOMEDCT_US",
            "RXNORM",
            "LNC",
            "CVX",
            "CPT",
        ),
        *,
        create_indexes: bool = True,
    ) -> None:
        """Prepare low-memory temp tables for repeated LocalLite queries.

        The temp tables intentionally shadow `mrconso` and `mrrel` so the rest
        of the engine can keep using the same SQL. The original database tables
        remain accessible through their fully-qualified catalog name.
        """
        if self.cache_prepared:
            return

        catalog = self._base_catalog_name()
        base_conso = f'"{catalog}".main.mrconso'
        base_rel = f'"{catalog}".main.mrrel'
        relevant_sources = tuple(_dedupe([*sources, "MEDLINEPLUS", "CHV"]))
        placeholders = ",".join(["?"] * len(relevant_sources))

        self.con.execute(
            f"""
            CREATE TEMP TABLE mrconso AS
            SELECT CODE, TTY, STR, AUI, SUPPRESS, SAB, CUI
            FROM {base_conso}
            WHERE SUPPRESS = 'N'
              AND CODE IS NOT NULL
              AND CODE != ''
              AND SAB IN ({placeholders})
            """,
            list(relevant_sources),
        )
        self.con.execute("CREATE TEMP TABLE mt4ds_cache_aui AS SELECT AUI FROM mrconso WHERE AUI IS NOT NULL")
        self.con.execute("CREATE TEMP TABLE mrrel (AUI1 VARCHAR, AUI2 VARCHAR, RELA VARCHAR, REL VARCHAR)")
        for predicate in (
            "r.REL = 'PAR'",
            "COALESCE(r.REL, '') != 'PAR' AND r.RELA IN ('isa', 'component_of', 'measured_by', 'mapped_from')",
        ):
            self.con.execute(
                f"""
                INSERT INTO mrrel
                SELECT r.AUI1, r.AUI2, r.RELA, r.REL
                FROM {base_rel} r
                WHERE {predicate}
                  AND r.AUI1 IN (SELECT AUI FROM mt4ds_cache_aui)
                  AND r.AUI2 IN (SELECT AUI FROM mt4ds_cache_aui)
                """
            )
        self.con.execute("DROP TABLE mt4ds_cache_aui")

        if create_indexes:
            for ddl in (
                "CREATE INDEX idx_mt4ds_mrconso_sab_code ON mrconso(SAB, CODE)",
                "CREATE INDEX idx_mt4ds_mrconso_aui ON mrconso(AUI)",
                "CREATE INDEX idx_mt4ds_mrconso_cui_sab ON mrconso(CUI, SAB)",
                "CREATE INDEX idx_mt4ds_mrrel_aui1 ON mrrel(AUI1)",
                "CREATE INDEX idx_mt4ds_mrrel_aui2 ON mrrel(AUI2)",
            ):
                try:
                    self.con.execute(ddl)
                except Exception as exc:
                    logger.debug("Skipping LocalLite cache index %s: %s", ddl, exc)

        self.cache_prepared = True

    def _base_catalog_name(self) -> str:
        rows = self.con.execute("PRAGMA database_list").fetchall()
        for _seq, name, file_path in rows:
            if file_path:
                return str(name)
        return str(rows[0][1])

    def _progress(self, message: str) -> None:
        if self.progress:
            self.progress(message)

    def get_patient_friendly_names(
        self,
        codes: Sequence[CodeRef],
        max_depth: int = 5,
    ) -> list[FriendlyNameResult]:
        if not codes:
            return []

        ordered = [CodeRef(source=c.source, code=c.code) for c in codes]
        grouped: dict[str, list[str]] = defaultdict(list)
        for ref in ordered:
            grouped[ref.source].append(ref.code)
        grouped = {source: _dedupe(values) for source, values in grouped.items()}

        snomed_codes = grouped.pop("SNOMEDCT_US", [])
        if snomed_codes:
            self._progress(f"mapping SNOMEDCT_US ({len(snomed_codes)} codes)")
        snomed_map = self._map_snomed_codes(snomed_codes) if snomed_codes else {}
        for _sn_code, (target_source, target_code, _is_broader) in snomed_map.items():
            grouped.setdefault(target_source, []).append(target_code)
        grouped = {source: _dedupe(values) for source, values in grouped.items()}

        non_snomed: dict[tuple[str, str], _Row] = {}
        for source, source_codes in grouped.items():
            source_chunks = list(_chunks(source_codes, self.query_chunk_size))
            for chunk_index, source_chunk in enumerate(source_chunks, 1):
                self._progress(
                    f"resolving {source} chunk {chunk_index}/{len(source_chunks)} "
                    f"({len(source_chunk)} codes)"
                )
                rows = self._resolve_source(source, source_chunk, max_depth)
                self._apply_snomed_fallback(source, rows, max_depth)
                for row in rows:
                    non_snomed[(row.source, row.code)] = row

        if snomed_codes:
            self._progress(f"resolving SNOMEDCT_US ({len(snomed_codes)} codes)")
        snomed_rows = self._resolve_snomed(snomed_codes, snomed_map, non_snomed, max_depth)
        snomed_by_code = {row.code: row for row in snomed_rows}

        output: list[FriendlyNameResult] = []
        for ref in ordered:
            if ref.source == "SNOMEDCT_US":
                row = snomed_by_code.get(ref.code) or self._make_original(ref.code, ref.source)
            else:
                row = non_snomed.get((ref.source, ref.code)) or self._make_original(ref.code, ref.source)
            output.append(row.result())
        return output

    def get_code_infos(self, codes: Sequence[CodeRef]) -> list[CodeInfo | None]:
        """Return canonical active atom info for input codes."""
        if not codes:
            return []

        ordered = [CodeRef(source=code.source, code=code.code) for code in codes]
        grouped: dict[str, list[str]] = defaultdict(list)
        for ref in ordered:
            grouped[ref.source].append(ref.code)

        lookup: dict[tuple[str, str], CodeInfo] = {}
        for source, source_codes in grouped.items():
            with self._temp_codes(source_codes) as temp:
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

    def get_source_stats(self, sources: Sequence[str] | None = None) -> list[SourceStats]:
        """Return active code and atom counts by source."""
        params: list[object] = []
        source_filter = ""
        if sources:
            normalized_sources = _dedupe(sources)
            placeholders = ",".join(["?"] * len(normalized_sources))
            source_filter = f"AND SAB IN ({placeholders})"
            params.extend(normalized_sources)
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

        filters = ["SUPPRESS = 'N'", "CODE IS NOT NULL", "CODE != ''", "STR IS NOT NULL"]
        filter_params: list[object] = []
        if sources:
            normalized_sources = _dedupe(sources)
            filters.append(f"SAB IN ({','.join(['?'] * len(normalized_sources))})")
            filter_params.extend(normalized_sources)
        if tty_filters:
            normalized_ttys = _dedupe([tty.upper() for tty in tty_filters])
            filters.append(f"TTY IN ({','.join(['?'] * len(normalized_ttys))})")
            filter_params.extend(normalized_ttys)

        lowered_query = stripped_query.lower()
        prefix_pattern = f"{lowered_query}%"
        contains_pattern = f"%{lowered_query}%"

        rows = self.con.execute(
            f"""
            WITH ranked AS (
                SELECT SAB, CODE, STR, CUI, AUI, TTY,
                       CASE
                           WHEN LOWER(STR) = ? THEN 'exact'
                           WHEN LOWER(STR) LIKE ? THEN 'prefix'
                           ELSE 'contains'
                       END AS match_type,
                       ROW_NUMBER() OVER (
                           PARTITION BY SAB, CODE
                           ORDER BY
                               CASE
                                   WHEN LOWER(STR) = ? THEN 0
                                   WHEN LOWER(STR) LIKE ? THEN 1
                                   ELSE 2
                               END,
                               CASE TTY
                                   WHEN 'PT' THEN 0
                                   WHEN 'MH' THEN 1
                                   WHEN 'LN' THEN 2
                                   ELSE 3
                               END,
                               LENGTH(STR),
                               AUI
                       ) AS atom_rn
                FROM mrconso
                WHERE {' AND '.join(filters)}
                  AND LOWER(STR) LIKE ?
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

    def get_code_relations(
        self,
        codes: Sequence[CodeRef],
        *,
        direction: str,
        max_depth: int = 1,
    ) -> list[CodeRelation]:
        """Return same-source hierarchy relationships for input codes."""
        if not codes:
            return []
        if max_depth < 1:
            raise ValueError("max_depth must be at least 1")

        direction = direction.strip().lower()
        if direction not in {"parents", "children", "ancestors", "descendants"}:
            raise ValueError("direction must be one of parents, children, ancestors, or descendants")
        if direction in {"parents", "children"}:
            max_depth = 1

        ordered = [CodeRef(source=code.source, code=code.code) for code in codes]
        grouped: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for ordinal, ref in enumerate(ordered):
            grouped[ref.source].append((ordinal, ref.code))

        rows: list[tuple[int, CodeRelation]] = []
        relationship = _HIERARCHY_RELATIONSHIPS[direction]
        upward = direction in {"parents", "ancestors"}
        for source, source_codes in grouped.items():
            for chunk in _chunks(source_codes, self.query_chunk_size):
                rows.extend(
                    self._get_source_code_relations(
                        source,
                        chunk,
                        relationship=relationship,
                        upward=upward,
                        max_depth=max_depth,
                    )
                )
        return [
            relation
            for _ordinal, relation in sorted(
                rows,
                key=lambda item: (
                    item[0],
                    item[1].depth,
                    item[1].target.code,
                    item[1].target_aui or "",
                ),
            )
        ]

    def get_code_mappings(
        self,
        codes: Sequence[CodeRef],
        *,
        target_sources: Sequence[str],
        max_results_per_code: int = 50,
        max_depth: int = 0,
        include_target_ancestors: bool = False,
        include_target_descendants: bool = False,
    ) -> list[CodeMapping]:
        """Return same-CUI active target mappings for input codes."""
        if not codes:
            return []
        if not target_sources:
            raise ValueError("target_sources must not be empty")
        if max_results_per_code < 1:
            raise ValueError("max_results_per_code must be at least 1")
        if max_depth < 0:
            raise ValueError("max_depth must be non-negative")

        ordered = [CodeRef(source=code.source, code=code.code) for code in codes]
        grouped: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for ordinal, ref in enumerate(ordered):
            grouped[ref.source].append((ordinal, ref.code))

        target_sources = _dedupe(target_sources)
        rows: list[tuple[int, CodeMapping]] = []
        for source, source_codes in grouped.items():
            for chunk in _chunks(source_codes, self.query_chunk_size):
                rows.extend(
                    self._get_source_code_mappings(
                        source,
                        chunk,
                        target_sources=target_sources,
                        max_results_per_code=max_results_per_code,
                    )
                )
                if max_depth > 0:
                    rows.extend(
                        self._get_source_ancestor_mappings(
                            source,
                            chunk,
                            target_sources=target_sources,
                            max_results_per_code=max_results_per_code,
                            max_depth=max_depth,
                        )
                    )
                    if include_target_ancestors:
                        rows.extend(
                            self._get_target_hierarchy_mappings(
                                source,
                                chunk,
                                target_sources=target_sources,
                                max_results_per_code=max_results_per_code,
                                max_depth=max_depth,
                                upward=True,
                            )
                        )
                    if include_target_descendants:
                        rows.extend(
                            self._get_target_hierarchy_mappings(
                                source,
                                chunk,
                                target_sources=target_sources,
                                max_results_per_code=max_results_per_code,
                                max_depth=max_depth,
                                upward=False,
                            )
                        )
        return [
            mapping
            for _ordinal, mapping in sorted(
                rows,
                key=lambda item: (
                    item[0],
                    item[1].match_depth,
                    item[1].match_type,
                    item[1].target.source,
                    item[1].target.code,
                    item[1].target_aui or "",
                ),
            )
        ]

    def _get_source_code_relations(
        self,
        source: str,
        code_ordinals: Sequence[tuple[int, str]],
        *,
        relationship: str,
        upward: bool,
        max_depth: int,
    ) -> list[tuple[int, CodeRelation]]:
        source_join = "r.AUI1 = s.source_aui" if upward else "r.AUI2 = s.source_aui"
        source_target = "r.AUI2" if upward else "r.AUI1"
        recursive_join = "r.AUI1 = w.target_aui" if upward else "r.AUI2 = w.target_aui"
        recursive_target = "r.AUI2" if upward else "r.AUI1"

        with self._temp_code_ordinals(code_ordinals) as temp:
            rows = self.con.execute(
                f"""
                WITH RECURSIVE
                base AS (
                    SELECT i.ordinal, i.code AS source_code, c.STR AS source_name,
                           c.CUI AS source_cui, c.AUI AS source_aui,
                           ROW_NUMBER() OVER (
                               PARTITION BY i.ordinal
                               ORDER BY
                                   CASE c.TTY
                                       WHEN 'PT' THEN 0
                                       WHEN 'MH' THEN 1
                                       WHEN 'LN' THEN 2
                                       ELSE 3
                                   END,
                                   c.AUI
                           ) AS rn
                    FROM {temp} i
                    JOIN mrconso c ON c.CODE = i.code
                    WHERE c.SAB = ?
                      AND c.SUPPRESS = 'N'
                ),
                seed AS (
                    SELECT ordinal, source_code, source_name, source_cui, source_aui
                    FROM base
                    WHERE rn = 1
                ),
                walk AS (
                    SELECT s.ordinal, s.source_code, s.source_name, s.source_cui,
                           s.source_aui, t.CODE AS target_code, t.STR AS target_name,
                           t.CUI AS target_cui, t.AUI AS target_aui, r.REL AS rel,
                           r.RELA AS rela, 1 AS depth,
                           s.source_aui || '>' || t.AUI AS path
                    FROM seed s
                    JOIN mrrel r ON {source_join}
                    JOIN mrconso t ON t.AUI = {source_target}
                    WHERE r.REL = 'PAR'
                      AND t.SAB = ?
                      AND t.SUPPRESS = 'N'

                    UNION ALL

                    SELECT w.ordinal, w.source_code, w.source_name, w.source_cui,
                           w.source_aui, t.CODE AS target_code, t.STR AS target_name,
                           t.CUI AS target_cui, t.AUI AS target_aui, r.REL AS rel,
                           r.RELA AS rela, w.depth + 1 AS depth,
                           w.path || '>' || t.AUI AS path
                    FROM walk w
                    JOIN mrrel r ON {recursive_join}
                    JOIN mrconso t ON t.AUI = {recursive_target}
                    WHERE w.depth < ?
                      AND r.REL = 'PAR'
                      AND t.SAB = ?
                      AND t.SUPPRESS = 'N'
                      AND strpos('>' || w.path || '>', '>' || t.AUI || '>') = 0
                ),
                ranked AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY ordinal, target_code
                               ORDER BY depth, target_aui
                           ) AS rn
                    FROM walk
                )
                SELECT ordinal, source_code, source_name, source_cui, source_aui,
                       target_code, target_name, target_cui, target_aui, rel, rela, depth
                FROM ranked
                WHERE rn = 1
                ORDER BY ordinal, depth, target_code, target_aui
                """,
                [source, source, max_depth, source],
            ).fetchall()

        return [
            (
                int(ordinal),
                CodeRelation(
                    source=CodeRef(source=source, code=source_code),
                    target=CodeRef(source=source, code=target_code),
                    relationship=relationship,
                    depth=int(depth),
                    source_display=source_name,
                    target_display=target_name,
                    rel=rel,
                    rela=rela,
                    source_cui=source_cui,
                    target_cui=target_cui,
                    source_aui=source_aui,
                    target_aui=target_aui,
                ),
            )
            for (
                ordinal,
                source_code,
                source_name,
                source_cui,
                source_aui,
                target_code,
                target_name,
                target_cui,
                target_aui,
                rel,
                rela,
                depth,
            ) in rows
        ]

    def _get_source_code_mappings(
        self,
        source: str,
        code_ordinals: Sequence[tuple[int, str]],
        *,
        target_sources: Sequence[str],
        max_results_per_code: int,
    ) -> list[tuple[int, CodeMapping]]:
        target_placeholders = ",".join(["?"] * len(target_sources))
        with self._temp_code_ordinals(code_ordinals) as temp:
            rows = self.con.execute(
                f"""
                WITH
                source_atoms AS (
                    SELECT i.ordinal, i.code AS source_code, c.STR AS source_name,
                           c.CUI AS source_cui, c.AUI AS source_aui,
                           ROW_NUMBER() OVER (
                               PARTITION BY i.ordinal
                               ORDER BY
                                   CASE c.TTY
                                       WHEN 'PT' THEN 0
                                       WHEN 'MH' THEN 1
                                       WHEN 'LN' THEN 2
                                       ELSE 3
                                   END,
                                   c.AUI
                           ) AS rn
                    FROM {temp} i
                    JOIN mrconso c ON c.CODE = i.code
                    WHERE c.SAB = ?
                      AND c.SUPPRESS = 'N'
                ),
                source_seed AS (
                    SELECT ordinal, source_code, source_name, source_cui, source_aui
                    FROM source_atoms
                    WHERE rn = 1
                ),
                target_ranked AS (
                    SELECT s.ordinal, s.source_code, s.source_name, s.source_cui,
                           s.source_aui, t.SAB AS target_source, t.CODE AS target_code,
                           t.STR AS target_name, t.CUI AS target_cui, t.AUI AS target_aui,
                           t.TTY AS target_tty,
                           ROW_NUMBER() OVER (
                               PARTITION BY s.ordinal, t.SAB, t.CODE
                               ORDER BY
                                   CASE t.TTY
                                       WHEN 'PT' THEN 0
                                       WHEN 'MH' THEN 1
                                       WHEN 'LN' THEN 2
                                       ELSE 3
                                   END,
                                   t.AUI
                           ) AS atom_rn
                    FROM source_seed s
                    JOIN mrconso t ON t.CUI = s.source_cui
                    WHERE t.SAB IN ({target_placeholders})
                      AND t.SUPPRESS = 'N'
                ),
                deduped_targets AS (
                    SELECT *
                    FROM target_ranked
                    WHERE atom_rn = 1
                ),
                capped_targets AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY ordinal
                               ORDER BY target_source, target_code, target_aui
                           ) AS result_rn
                    FROM deduped_targets
                )
                SELECT ordinal, source_code, source_name, source_cui, source_aui,
                       target_source, target_code, target_name, target_cui,
                       target_aui, target_tty
                FROM capped_targets
                WHERE result_rn <= ?
                ORDER BY ordinal, target_source, target_code, target_aui
                """,
                [source, *target_sources, max_results_per_code],
            ).fetchall()

        return [
            (
                int(ordinal),
                CodeMapping(
                    source=CodeRef(source=source, code=source_code),
                    target=CodeRef(source=target_source, code=target_code),
                    source_display=source_name,
                    target_display=target_name,
                    relationship="equivalent",
                    match_type="same_cui",
                    match_depth=0,
                    source_cui=source_cui,
                    target_cui=target_cui,
                    source_aui=source_aui,
                    target_aui=target_aui,
                    target_tty=target_tty,
                    matched_via=Provenance.from_steps(
                        "same_cui",
                        [
                            ProvenanceStep(
                                op="input_atom",
                                source=source,
                                code=source_code,
                                cui=source_cui,
                                aui=source_aui,
                                name=source_name,
                            ),
                            ProvenanceStep(
                                op="same_cui",
                                source=source,
                                code=source_code,
                                target_source=target_source,
                                target_code=target_code,
                                cui=source_cui,
                            ),
                            ProvenanceStep(
                                op="target_atom",
                                source=target_source,
                                code=target_code,
                                cui=target_cui,
                                aui=target_aui,
                                tty=target_tty,
                                name=target_name,
                            ),
                        ],
                    ),
                ),
            )
            for (
                ordinal,
                source_code,
                source_name,
                source_cui,
                source_aui,
                target_source,
                target_code,
                target_name,
                target_cui,
                target_aui,
                target_tty,
            ) in rows
        ]

    def _get_source_ancestor_mappings(
        self,
        source: str,
        code_ordinals: Sequence[tuple[int, str]],
        *,
        target_sources: Sequence[str],
        max_results_per_code: int,
        max_depth: int,
    ) -> list[tuple[int, CodeMapping]]:
        target_placeholders = ",".join(["?"] * len(target_sources))
        with self._temp_code_ordinals(code_ordinals) as temp:
            rows = self.con.execute(
                f"""
                WITH RECURSIVE
                source_atoms AS (
                    SELECT i.ordinal, i.code AS source_code, c.STR AS source_name,
                           c.CUI AS source_cui, c.AUI AS source_aui,
                           ROW_NUMBER() OVER (
                               PARTITION BY i.ordinal
                               ORDER BY
                                   CASE c.TTY
                                       WHEN 'PT' THEN 0
                                       WHEN 'MH' THEN 1
                                       WHEN 'LN' THEN 2
                                       ELSE 3
                                   END,
                                   c.AUI
                           ) AS rn
                    FROM {temp} i
                    JOIN mrconso c ON c.CODE = i.code
                    WHERE c.SAB = ?
                      AND c.SUPPRESS = 'N'
                ),
                source_seed AS (
                    SELECT ordinal, source_code, source_name, source_cui, source_aui
                    FROM source_atoms
                    WHERE rn = 1
                ),
                exact_target_sources AS (
                    SELECT DISTINCT s.ordinal, t.SAB AS target_source
                    FROM source_seed s
                    JOIN mrconso t ON t.CUI = s.source_cui
                    WHERE t.SAB IN ({target_placeholders})
                      AND t.SUPPRESS = 'N'
                ),
                source_walk AS (
                    SELECT s.ordinal, s.source_code, s.source_name, s.source_cui,
                           s.source_aui, p.CODE AS ancestor_code,
                           p.STR AS ancestor_name, p.CUI AS ancestor_cui,
                           p.AUI AS ancestor_aui, 1 AS source_depth,
                           s.source_aui || '>' || p.AUI AS path
                    FROM source_seed s
                    JOIN mrrel r ON r.AUI1 = s.source_aui
                    JOIN mrconso p ON p.AUI = r.AUI2
                    WHERE r.REL = 'PAR'
                      AND p.SAB = ?
                      AND p.SUPPRESS = 'N'

                    UNION ALL

                    SELECT w.ordinal, w.source_code, w.source_name, w.source_cui,
                           w.source_aui, p.CODE AS ancestor_code,
                           p.STR AS ancestor_name, p.CUI AS ancestor_cui,
                           p.AUI AS ancestor_aui, w.source_depth + 1 AS source_depth,
                           w.path || '>' || p.AUI AS path
                    FROM source_walk w
                    JOIN mrrel r ON r.AUI1 = w.ancestor_aui
                    JOIN mrconso p ON p.AUI = r.AUI2
                    WHERE w.source_depth < ?
                      AND r.REL = 'PAR'
                      AND p.SAB = ?
                      AND p.SUPPRESS = 'N'
                      AND strpos('>' || w.path || '>', '>' || p.AUI || '>') = 0
                ),
                target_ranked AS (
                    SELECT w.ordinal, w.source_code, w.source_name, w.source_cui,
                           w.source_aui, w.ancestor_code, w.ancestor_name,
                           w.ancestor_cui, w.ancestor_aui, w.source_depth,
                           t.SAB AS target_source, t.CODE AS target_code,
                           t.STR AS target_name, t.CUI AS target_cui,
                           t.AUI AS target_aui, t.TTY AS target_tty,
                           ROW_NUMBER() OVER (
                               PARTITION BY w.ordinal, t.SAB, t.CODE
                               ORDER BY
                                   w.source_depth,
                                   CASE t.TTY
                                       WHEN 'PT' THEN 0
                                       WHEN 'MH' THEN 1
                                       WHEN 'LN' THEN 2
                                       ELSE 3
                                   END,
                                   t.AUI
                           ) AS atom_rn
                    FROM source_walk w
                    JOIN mrconso t ON t.CUI = w.ancestor_cui
                    WHERE t.SAB IN ({target_placeholders})
                      AND t.SUPPRESS = 'N'
                      AND NOT EXISTS (
                          SELECT 1
                          FROM exact_target_sources e
                          WHERE e.ordinal = w.ordinal
                            AND e.target_source = t.SAB
                      )
                ),
                deduped_targets AS (
                    SELECT *
                    FROM target_ranked
                    WHERE atom_rn = 1
                ),
                capped_targets AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY ordinal
                               ORDER BY source_depth, target_source, target_code, target_aui
                           ) AS result_rn
                    FROM deduped_targets
                )
                SELECT ordinal, source_code, source_name, source_cui, source_aui,
                       ancestor_code, ancestor_name, ancestor_cui, ancestor_aui,
                       source_depth, target_source, target_code, target_name,
                       target_cui, target_aui, target_tty
                FROM capped_targets
                WHERE result_rn <= ?
                ORDER BY ordinal, source_depth, target_source, target_code, target_aui
                """,
                [
                    source,
                    *target_sources,
                    source,
                    max_depth,
                    source,
                    *target_sources,
                    max_results_per_code,
                ],
            ).fetchall()

        return [
            (
                int(ordinal),
                CodeMapping(
                    source=CodeRef(source=source, code=source_code),
                    target=CodeRef(source=target_source, code=target_code),
                    source_display=source_name,
                    target_display=target_name,
                    relationship="source-is-narrower-than-target",
                    match_type="source_ancestor_same_cui",
                    match_depth=int(source_depth),
                    source_cui=source_cui,
                    target_cui=target_cui,
                    source_aui=source_aui,
                    target_aui=target_aui,
                    target_tty=target_tty,
                    matched_via=Provenance.from_steps(
                        "source_ancestor_same_cui",
                        [
                            ProvenanceStep(
                                op="input_atom",
                                source=source,
                                code=source_code,
                                cui=source_cui,
                                aui=source_aui,
                                name=source_name,
                            ),
                            ProvenanceStep(
                                op="source_ancestor",
                                source=source,
                                code=ancestor_code,
                                cui=ancestor_cui,
                                aui=ancestor_aui,
                                depth=int(source_depth),
                                name=ancestor_name,
                            ),
                            ProvenanceStep(
                                op="same_cui",
                                source=source,
                                code=ancestor_code,
                                target_source=target_source,
                                target_code=target_code,
                                cui=ancestor_cui,
                            ),
                            ProvenanceStep(
                                op="target_atom",
                                source=target_source,
                                code=target_code,
                                cui=target_cui,
                                aui=target_aui,
                                tty=target_tty,
                                name=target_name,
                            ),
                        ],
                    ),
                ),
            )
            for (
                ordinal,
                source_code,
                source_name,
                source_cui,
                source_aui,
                ancestor_code,
                ancestor_name,
                ancestor_cui,
                ancestor_aui,
                source_depth,
                target_source,
                target_code,
                target_name,
                target_cui,
                target_aui,
                target_tty,
            ) in rows
        ]

    def _get_target_hierarchy_mappings(
        self,
        source: str,
        code_ordinals: Sequence[tuple[int, str]],
        *,
        target_sources: Sequence[str],
        max_results_per_code: int,
        max_depth: int,
        upward: bool,
    ) -> list[tuple[int, CodeMapping]]:
        target_placeholders = ",".join(["?"] * len(target_sources))
        direct_join = "r.AUI1 = e.exact_target_aui" if upward else "r.AUI2 = e.exact_target_aui"
        direct_target = "r.AUI2" if upward else "r.AUI1"
        recursive_join = "r.AUI1 = w.target_aui" if upward else "r.AUI2 = w.target_aui"
        recursive_target = "r.AUI2" if upward else "r.AUI1"
        relationship = "source-is-narrower-than-target" if upward else "source-is-broader-than-target"
        match_type = "target_ancestor" if upward else "target_descendant"
        step_op = match_type

        with self._temp_code_ordinals(code_ordinals) as temp:
            rows = self.con.execute(
                f"""
                WITH RECURSIVE
                source_atoms AS (
                    SELECT i.ordinal, i.code AS source_code, c.STR AS source_name,
                           c.CUI AS source_cui, c.AUI AS source_aui,
                           ROW_NUMBER() OVER (
                               PARTITION BY i.ordinal
                               ORDER BY
                                   CASE c.TTY
                                       WHEN 'PT' THEN 0
                                       WHEN 'MH' THEN 1
                                       WHEN 'LN' THEN 2
                                       ELSE 3
                                   END,
                                   c.AUI
                           ) AS rn
                    FROM {temp} i
                    JOIN mrconso c ON c.CODE = i.code
                    WHERE c.SAB = ?
                      AND c.SUPPRESS = 'N'
                ),
                source_seed AS (
                    SELECT ordinal, source_code, source_name, source_cui, source_aui
                    FROM source_atoms
                    WHERE rn = 1
                ),
                exact_targets AS (
                    SELECT s.ordinal, s.source_code, s.source_name, s.source_cui,
                           s.source_aui, t.SAB AS exact_target_source,
                           t.CODE AS exact_target_code, t.STR AS exact_target_name,
                           t.CUI AS exact_target_cui, t.AUI AS exact_target_aui,
                           t.TTY AS exact_target_tty,
                           ROW_NUMBER() OVER (
                               PARTITION BY s.ordinal, t.SAB, t.CODE
                               ORDER BY
                                   CASE t.TTY
                                       WHEN 'PT' THEN 0
                                       WHEN 'MH' THEN 1
                                       WHEN 'LN' THEN 2
                                       ELSE 3
                                   END,
                                   t.AUI
                           ) AS atom_rn
                    FROM source_seed s
                    JOIN mrconso t ON t.CUI = s.source_cui
                    WHERE t.SAB IN ({target_placeholders})
                      AND t.SUPPRESS = 'N'
                ),
                exact_seed AS (
                    SELECT *
                    FROM exact_targets
                    WHERE atom_rn = 1
                ),
                target_walk AS (
                    SELECT e.ordinal, e.source_code, e.source_name, e.source_cui,
                           e.source_aui, e.exact_target_source, e.exact_target_code,
                           e.exact_target_name, e.exact_target_cui, e.exact_target_aui,
                           e.exact_target_tty, t.CODE AS target_code,
                           t.STR AS target_name, t.CUI AS target_cui,
                           t.AUI AS target_aui, t.TTY AS target_tty,
                           1 AS target_depth,
                           e.exact_target_aui || '>' || t.AUI AS path
                    FROM exact_seed e
                    JOIN mrrel r ON {direct_join}
                    JOIN mrconso t ON t.AUI = {direct_target}
                    WHERE r.REL = 'PAR'
                      AND t.SAB = e.exact_target_source
                      AND t.SUPPRESS = 'N'

                    UNION ALL

                    SELECT w.ordinal, w.source_code, w.source_name, w.source_cui,
                           w.source_aui, w.exact_target_source, w.exact_target_code,
                           w.exact_target_name, w.exact_target_cui, w.exact_target_aui,
                           w.exact_target_tty, t.CODE AS target_code,
                           t.STR AS target_name, t.CUI AS target_cui,
                           t.AUI AS target_aui, t.TTY AS target_tty,
                           w.target_depth + 1 AS target_depth,
                           w.path || '>' || t.AUI AS path
                    FROM target_walk w
                    JOIN mrrel r ON {recursive_join}
                    JOIN mrconso t ON t.AUI = {recursive_target}
                    WHERE w.target_depth < ?
                      AND r.REL = 'PAR'
                      AND t.SAB = w.exact_target_source
                      AND t.SUPPRESS = 'N'
                      AND strpos('>' || w.path || '>', '>' || t.AUI || '>') = 0
                ),
                ranked AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY ordinal, exact_target_source, target_code
                               ORDER BY
                                   target_depth,
                                   CASE target_tty
                                       WHEN 'PT' THEN 0
                                       WHEN 'MH' THEN 1
                                       WHEN 'LN' THEN 2
                                       ELSE 3
                                   END,
                                   target_aui
                           ) AS atom_rn
                    FROM target_walk
                ),
                deduped_targets AS (
                    SELECT *
                    FROM ranked
                    WHERE atom_rn = 1
                ),
                capped_targets AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY ordinal
                               ORDER BY target_depth, exact_target_source, target_code, target_aui
                           ) AS result_rn
                    FROM deduped_targets
                )
                SELECT ordinal, source_code, source_name, source_cui, source_aui,
                       exact_target_source, exact_target_code, exact_target_name,
                       exact_target_cui, exact_target_aui, exact_target_tty,
                       target_code, target_name, target_cui, target_aui,
                       target_tty, target_depth
                FROM capped_targets
                WHERE result_rn <= ?
                ORDER BY ordinal, target_depth, exact_target_source, target_code, target_aui
                """,
                [source, *target_sources, max_depth, max_results_per_code],
            ).fetchall()

        return [
            (
                int(ordinal),
                CodeMapping(
                    source=CodeRef(source=source, code=source_code),
                    target=CodeRef(source=exact_target_source, code=target_code),
                    source_display=source_name,
                    target_display=target_name,
                    relationship=relationship,
                    match_type=match_type,
                    match_depth=int(target_depth),
                    source_cui=source_cui,
                    target_cui=target_cui,
                    source_aui=source_aui,
                    target_aui=target_aui,
                    target_tty=target_tty,
                    matched_via=Provenance.from_steps(
                        match_type,
                        [
                            ProvenanceStep(
                                op="input_atom",
                                source=source,
                                code=source_code,
                                cui=source_cui,
                                aui=source_aui,
                                name=source_name,
                            ),
                            ProvenanceStep(
                                op="same_cui",
                                source=source,
                                code=source_code,
                                target_source=exact_target_source,
                                target_code=exact_target_code,
                                cui=source_cui,
                            ),
                            ProvenanceStep(
                                op=step_op,
                                source=exact_target_source,
                                code=target_code,
                                cui=target_cui,
                                aui=target_aui,
                                depth=int(target_depth),
                                name=target_name,
                                metadata={"from_code": exact_target_code},
                            ),
                        ],
                    ),
                ),
            )
            for (
                ordinal,
                source_code,
                source_name,
                source_cui,
                source_aui,
                exact_target_source,
                exact_target_code,
                _exact_target_name,
                _exact_target_cui,
                _exact_target_aui,
                _exact_target_tty,
                target_code,
                target_name,
                target_cui,
                target_aui,
                target_tty,
                target_depth,
            ) in rows
        ]

    def _resolve_source(self, source: str, codes: Sequence[str], max_depth: int) -> list[_Row]:
        if not codes:
            return []
        if source == "RXNORM":
            return self._resolve_rxnorm(codes)
        if source == "LNC":
            return self._resolve_loinc(codes, max_depth)
        if source == "CPT":
            return self._resolve_cpt(codes, max_depth)
        if source == "CVX":
            return self._resolve_cvx(codes)
        return self._resolve_default(codes, source, max_depth)

    def _resolve_default(self, codes: Sequence[str], source: str, max_depth: int) -> list[_Row]:
        with self._temp_codes(codes) as temp:
            rows = self.con.execute(
                f"""
                WITH RECURSIVE
                base AS (
                    SELECT CODE, CUI, AUI, STR AS orig_name,
                           ROW_NUMBER() OVER (PARTITION BY CODE ORDER BY AUI) AS rn
                    FROM mrconso
                    WHERE SAB = ? AND SUPPRESS = 'N'
                      AND CODE IN (SELECT code FROM {temp})
                ),
                seed AS (
                    SELECT CODE, CUI, AUI, orig_name, 0 AS depth
                    FROM base WHERE rn = 1
                ),
                walk AS (
                    SELECT CODE, CUI, AUI, orig_name, depth
                    FROM seed
                    UNION
                    SELECT w.CODE, p.CUI, p.AUI, w.orig_name, w.depth + 1
                    FROM walk w
                    JOIN mrrel r ON r.AUI1 = w.AUI AND r.REL = 'PAR'
                    JOIN mrconso p ON p.AUI = r.AUI2
                    WHERE w.depth < ?
                      AND p.SAB = ? AND p.SUPPRESS = 'N'
                ),
                friendly AS (
                    SELECT w.CODE, w.orig_name, w.depth, mp.STR AS friendly_name,
                           'MEDLINEPLUS' AS friendly_source, 0 AS source_priority,
                           mp.TTY AS tty, w.CUI AS matched_cui
                    FROM walk w
                    JOIN mrconso mp ON mp.CUI = w.CUI
                    WHERE mp.SAB = 'MEDLINEPLUS' AND mp.SUPPRESS = 'N' AND mp.TTY != 'HT'
                    UNION ALL
                    SELECT w.CODE, w.orig_name, w.depth, chv.STR AS friendly_name,
                           'CHV' AS friendly_source, 1 AS source_priority,
                           chv.TTY AS tty, w.CUI AS matched_cui
                    FROM walk w
                    JOIN mrconso chv ON chv.CUI = w.CUI
                    WHERE chv.SAB = 'CHV' AND chv.SUPPRESS = 'N' AND chv.TTY != 'HT'
                ),
                ranked AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY CODE
                               ORDER BY depth, source_priority, LOWER(friendly_name)
                           ) AS rn
                    FROM friendly
                )
                SELECT b.CODE, b.orig_name, r.friendly_name, r.friendly_source,
                       CASE WHEN r.depth = 0 THEN 'exact'
                            WHEN r.depth IS NOT NULL THEN 'broader'
                            ELSE 'original' END AS match_type,
                       COALESCE(r.depth, 0) AS match_depth,
                       r.tty, r.matched_cui
                FROM (SELECT CODE, FIRST(orig_name) AS orig_name FROM base GROUP BY CODE) b
                LEFT JOIN ranked r ON r.CODE = b.CODE AND r.rn = 1
                """,
                [source, max_depth, source],
            ).fetchall()

        by_code: dict[str, _Row] = {}
        for code, orig_name, friendly_name, friendly_source, match_type, depth, tty, cui in rows:
            if friendly_name and not _is_broad_friendly_name(friendly_source, friendly_name):
                by_code[code] = _Row(
                    code=code,
                    source=source,
                    name=friendly_name,
                    friendly_source=friendly_source,
                    match_type=match_type,
                    match_depth=int(depth or 0),
                    technical_name=orig_name,
                    matched_via=self._provenance(
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
                by_code[code] = self._make_original(code, source, technical_name=orig_name)

        return [by_code.get(code) or self._make_original(code, source) for code in codes]

    def _apply_snomed_fallback(self, source: str, rows: list[_Row], max_depth: int) -> None:
        if source not in _SNOMED_FALLBACK_SOURCES:
            return
        fallback_codes = [
            row.code
            for row in rows
            if row.match_type == "original"
            or (row.match_type == "exact" and row.friendly_source == "CHV")
        ]
        replacements = self._resolve_default_via_snomed(fallback_codes, source, max_depth)
        for row in rows:
            replacement = replacements.get(row.code)
            if replacement:
                row.name = replacement.name
                row.friendly_source = replacement.friendly_source
                row.match_type = replacement.match_type
                row.match_depth = replacement.match_depth
                row.matched_via = replacement.matched_via

    def _resolve_default_via_snomed(
        self,
        codes: Sequence[str],
        source: str,
        max_depth: int,
    ) -> dict[str, _Row]:
        if not codes:
            return {}
        codes = _dedupe(codes)
        if len(codes) > self.query_chunk_size:
            result: dict[str, _Row] = {}
            chunks = list(_chunks(codes, self.query_chunk_size))
            for chunk_index, chunk in enumerate(chunks, 1):
                self._progress(
                    f"resolving {source} SNOMED fallback chunk {chunk_index}/{len(chunks)} "
                    f"({len(chunk)} codes)"
                )
                result.update(self._resolve_default_via_snomed(chunk, source, max_depth))
            return result
        with self._temp_codes(codes) as temp:
            rows = self.con.execute(
                f"""
                WITH RECURSIVE
                source_base AS (
                    SELECT CODE, CUI, AUI, STR AS source_name,
                           ROW_NUMBER() OVER (PARTITION BY CODE ORDER BY AUI) AS rn
                    FROM mrconso
                    WHERE SAB = ? AND SUPPRESS = 'N'
                      AND CODE IN (SELECT code FROM {temp})
                ),
                source_walk AS (
                    SELECT CODE, CUI, AUI, source_name, 0 AS src_depth
                    FROM source_base WHERE rn = 1
                    UNION
                    SELECT w.CODE, p.CUI, p.AUI, w.source_name, w.src_depth + 1
                    FROM source_walk w
                    JOIN mrrel r ON r.AUI1 = w.AUI AND r.REL = 'PAR'
                    JOIN mrconso p ON p.AUI = r.AUI2
                    WHERE w.src_depth < ?
                      AND p.SAB = ? AND p.SUPPRESS = 'N'
                ),
                snomed_seed AS (
                    SELECT DISTINCT w.CODE, w.source_name, w.src_depth,
                           s.CODE AS snomed_code, s.AUI AS snomed_aui, s.CUI AS snomed_cui
                    FROM source_walk w
                    JOIN mrconso s ON s.CUI = w.CUI
                    WHERE s.SAB = 'SNOMEDCT_US' AND s.SUPPRESS = 'N'
                ),
                snomed_walk AS (
                    SELECT CODE, source_name, src_depth, snomed_code, snomed_aui, snomed_cui,
                           0 AS snomed_depth
                    FROM snomed_seed
                    UNION
                    SELECT w.CODE, w.source_name, w.src_depth, p.CODE, p.AUI, p.CUI,
                           w.snomed_depth + 1
                    FROM snomed_walk w
                    JOIN mrrel r ON r.AUI1 = w.snomed_aui AND r.REL = 'PAR'
                    JOIN mrconso p ON p.AUI = r.AUI2
                    WHERE w.snomed_depth < ?
                      AND p.SAB = 'SNOMEDCT_US' AND p.SUPPRESS = 'N'
                ),
                friendly AS (
                    SELECT w.CODE, w.source_name, w.src_depth, w.snomed_depth,
                           w.snomed_code, mp.STR AS friendly_name,
                           'MEDLINEPLUS' AS friendly_source, 0 AS source_priority,
                           mp.TTY AS tty, w.snomed_cui AS cui
                    FROM snomed_walk w
                    JOIN mrconso mp ON mp.CUI = w.snomed_cui
                    WHERE mp.SAB = 'MEDLINEPLUS' AND mp.SUPPRESS = 'N' AND mp.TTY != 'HT'
                    UNION ALL
                    SELECT w.CODE, w.source_name, w.src_depth, w.snomed_depth,
                           w.snomed_code, chv.STR AS friendly_name,
                           'CHV' AS friendly_source, 1 AS source_priority,
                           chv.TTY AS tty, w.snomed_cui AS cui
                    FROM snomed_walk w
                    JOIN mrconso chv ON chv.CUI = w.snomed_cui
                    WHERE chv.SAB = 'CHV' AND chv.SUPPRESS = 'N' AND chv.TTY != 'HT'
                ),
                ranked AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY CODE
                               ORDER BY src_depth + snomed_depth, source_priority, LOWER(friendly_name)
                           ) AS rn
                    FROM friendly
                )
                SELECT CODE, source_name, snomed_code, friendly_name, friendly_source,
                       src_depth, snomed_depth, src_depth + snomed_depth AS match_depth,
                       tty, cui
                FROM ranked
                WHERE rn = 1
                """,
                [source, max_depth, source, max_depth],
            ).fetchall()

        depth_lookup = self._snomed_top_level_depths([row[2] for row in rows if row[2]])
        result: dict[str, _Row] = {}
        for (
            code,
            source_name,
            snomed_code,
            friendly_name,
            friendly_source,
            src_depth,
            snomed_depth,
            match_depth,
            tty,
            cui,
        ) in rows:
            if not friendly_name:
                continue
            if _is_broad_friendly_name(friendly_source, friendly_name):
                continue
            if _is_combo_chv_mismatch(source_name, friendly_name):
                continue
            if depth_lookup.get(snomed_code, 999) <= _SNOMED_TOP_LEVEL_GUARD_DEPTH:
                continue
            result[code] = _Row(
                code=code,
                source=source,
                name=friendly_name,
                friendly_source=friendly_source,
                match_type="exact" if int(match_depth or 0) == 0 else "broader",
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
                            target_code=snomed_code,
                            mode="broader",
                            depth=int(src_depth or 0),
                        ),
                        ProvenanceStep(
                            op="ancestor",
                            source="SNOMEDCT_US",
                            code=snomed_code,
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
        return result

    def _resolve_rxnorm(self, codes: Sequence[str]) -> list[_Row]:
        if not codes:
            return []
        with self._temp_codes(codes) as temp:
            rows = self.con.execute(
                f"""
                WITH RECURSIVE
                base AS (
                    SELECT c.CODE AS input_code, upper(c.TTY) AS start_tty,
                           c.STR AS orig_name, c.AUI AS start_aui,
                           ROW_NUMBER() OVER (PARTITION BY c.CODE ORDER BY c.AUI) AS base_rn
                    FROM mrconso c
                    WHERE c.SAB = 'RXNORM' AND c.SUPPRESS = 'N'
                      AND c.CODE IN (SELECT code FROM {temp})
                ),
                walk AS (
                    SELECT input_code, start_tty, orig_name, start_aui,
                           start_aui AS aui, input_code AS code, orig_name AS name,
                           start_tty AS tty, 0 AS depth
                    FROM base
                    UNION
                    SELECT w.input_code, w.start_tty, w.orig_name, w.start_aui,
                           n.AUI, n.CODE, n.STR, upper(n.TTY), w.depth + 1
                    FROM walk w
                    JOIN mrrel r ON r.AUI1 = w.aui AND r.RELA = 'isa'
                    JOIN mrconso n ON n.AUI = r.AUI2
                    WHERE w.depth < 6
                      AND n.SAB = 'RXNORM' AND n.SUPPRESS = 'N'
                ),
                candidates AS (
                    SELECT input_code, orig_name, code AS target_code,
                           name AS target_name, tty AS target_tty, depth,
                           CASE
                               WHEN start_tty IN ('SCD','SBD','SCDF','SBDF','GPCK','BPCK','SBDG','SCDG','SBDC','DFG')
                                    AND tty = 'SCDG' THEN 0
                               WHEN start_tty IN ('PIN','SCDC') AND tty = 'IN' THEN 1
                               WHEN start_tty IN ('PIN','SCDC') AND tty = 'MIN' THEN 2
                               WHEN start_tty NOT IN ('PIN','SCDC') AND tty = 'MIN' THEN 1
                               WHEN start_tty NOT IN ('PIN','SCDC') AND tty = 'IN' THEN 2
                               ELSE NULL
                           END AS priority,
                           CASE WHEN tty = 'SCDG' THEN 'group' ELSE 'ingredient' END AS match_type
                    FROM walk
                    WHERE NOT (tty = 'IN' AND start_tty = 'IN' AND code != input_code)
                ),
                ranked AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY input_code
                               ORDER BY priority,
                                        depth,
                                        CASE WHEN regexp_matches(target_code, '^[0-9]+$') THEN 0 ELSE 1 END,
                                        TRY_CAST(target_code AS BIGINT),
                                        target_code,
                                        target_name
                           ) AS rn
                    FROM candidates
                    WHERE priority IS NOT NULL
                ),
                base_summary AS (
                    SELECT input_code, FIRST(orig_name ORDER BY base_rn) AS orig_name,
                           BOOL_OR(start_tty IN ('IN','PIN')) AS has_in_or_pin
                    FROM base
                    GROUP BY input_code
                )
                SELECT b.input_code, b.orig_name, b.has_in_or_pin,
                       r.target_code, r.target_name, r.target_tty,
                       r.match_type, r.depth
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
                    matched_via=self._simple_provenance("rxnorm_tty", "RXNORM", code, orig_name),
                )

        return [by_code.get(code) or self._make_none(code, "RXNORM") for code in codes]

    def _resolve_rxnorm_tty(self, start_aui: str, target_tty: str) -> tuple[str, str, int, str] | None:
        row = self.con.execute(
            """
            WITH RECURSIVE walk AS (
                SELECT c.AUI, c.CODE, c.STR, c.TTY, 0 AS depth
                FROM mrconso c
                WHERE c.AUI = ? AND c.SAB = 'RXNORM' AND c.SUPPRESS = 'N'
                UNION
                SELECT n.AUI, n.CODE, n.STR, n.TTY, w.depth + 1
                FROM walk w
                JOIN mrrel r ON r.AUI1 = w.AUI AND r.RELA = 'isa'
                JOIN mrconso n ON n.AUI = r.AUI2
                WHERE w.depth < 6
                  AND n.SAB = 'RXNORM' AND n.SUPPRESS = 'N'
            ),
            ranked AS (
                SELECT CODE, STR, TTY, depth,
                       ROW_NUMBER() OVER (
                           ORDER BY depth,
                                    CASE WHEN regexp_matches(CODE, '^[0-9]+$') THEN 0 ELSE 1 END,
                                    TRY_CAST(CODE AS BIGINT),
                                    CODE,
                                    STR
                       ) AS rn
                FROM walk
                WHERE upper(TTY) = ?
            )
            SELECT CODE, STR, depth, TTY FROM ranked WHERE rn = 1
            """,
            [start_aui, target_tty],
        ).fetchone()
        if not row:
            return None
        return (row[0], row[1], int(row[2] or 0), row[3])

    def _resolve_loinc(self, codes: Sequence[str], max_depth: int) -> list[_Row]:
        with self._temp_codes(codes) as temp:
            rows = self.con.execute(
                f"""
                WITH
                base AS (
                    SELECT CODE, CUI, AUI, STR AS orig_name
                    FROM mrconso
                    WHERE SAB = 'LNC' AND SUPPRESS = 'N'
                      AND CODE IN (SELECT code FROM {temp})
                ),
                comp_parts AS (
                    SELECT b.CODE AS code, b.orig_name, p.CODE AS part_code, p.STR AS part_name
                    FROM base b
                    JOIN mrrel r ON r.AUI1 = b.AUI AND r.RELA IN ('component_of', 'measured_by')
                    JOIN mrconso p ON p.AUI = r.AUI2
                    WHERE p.SAB = 'LNC' AND p.SUPPRESS = 'N' AND p.TTY = 'LPDN'
                ),
                tier1 AS (
                    SELECT code, orig_name, part_code, part_name,
                           ROW_NUMBER() OVER (
                               PARTITION BY code ORDER BY LENGTH(part_name) DESC, part_name
                           ) AS rn
                    FROM comp_parts
                ),
                comp_cuis AS (
                    SELECT DISTINCT b.CODE AS code, b.orig_name, p.CODE AS part_code,
                           p.STR AS part_name, p.CUI AS part_cui
                    FROM base b
                    JOIN mrrel r ON r.AUI1 = b.AUI AND r.RELA IN ('component_of', 'measured_by')
                    JOIN mrconso p ON p.AUI = r.AUI2
                    WHERE p.SUPPRESS = 'N' AND p.CUI IS NOT NULL
                ),
                friendly AS (
                    SELECT cc.code, cc.orig_name, cc.part_code, cc.part_name,
                           mp.STR AS friendly_name, 'MEDLINEPLUS' AS friendly_source,
                           0 AS priority, mp.TTY AS tty, cc.part_cui AS cui
                    FROM comp_cuis cc
                    JOIN mrconso mp ON mp.CUI = cc.part_cui
                    WHERE mp.SAB = 'MEDLINEPLUS' AND mp.SUPPRESS = 'N' AND mp.TTY != 'HT'
                    UNION ALL
                    SELECT cc.code, cc.orig_name, cc.part_code, cc.part_name,
                           chv.STR AS friendly_name, 'CHV' AS friendly_source,
                           1 AS priority, chv.TTY AS tty, cc.part_cui AS cui
                    FROM comp_cuis cc
                    JOIN mrconso chv ON chv.CUI = cc.part_cui
                    WHERE chv.SAB = 'CHV' AND chv.SUPPRESS = 'N' AND chv.TTY != 'HT'
                ),
                ranked_friendly AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY code ORDER BY priority, LOWER(friendly_name)
                           ) AS rn
                    FROM friendly
                ),
                common AS (
                    SELECT b.CODE AS code, b.orig_name, lc.STR AS common_name
                    FROM base b
                    LEFT JOIN mrconso lc ON lc.CUI = b.CUI
                    WHERE lc.SAB = 'LNC' AND lc.SUPPRESS = 'N' AND lc.TTY = 'LC'
                )
                SELECT b.CODE, b.orig_name,
                       t.part_code, t.part_name,
                       f.part_code AS friendly_part_code, f.part_name AS friendly_part_name,
                       f.friendly_name, f.friendly_source, f.tty, f.cui,
                       c.common_name
                FROM (SELECT CODE, FIRST(orig_name) AS orig_name FROM base GROUP BY CODE) b
                LEFT JOIN tier1 t ON t.code = b.CODE AND t.rn = 1
                LEFT JOIN ranked_friendly f ON f.code = b.CODE AND f.rn = 1
                LEFT JOIN common c ON c.code = b.CODE
                """
            ).fetchall()

        by_code: dict[str, _Row] = {}
        for (
            code,
            orig_name,
            part_code,
            part_name,
            friendly_part_code,
            friendly_part_name,
            friendly_name,
            friendly_source,
            tty,
            cui,
            common_name,
        ) in rows:
            if part_name and part_name not in _BLACKLIST_LOINC and len(part_name) > 1:
                by_code[code] = _Row(
                    code=code,
                    source="LNC",
                    name=part_name,
                    friendly_source="LNC",
                    match_type="first_axis",
                    match_depth=0,
                    technical_name=orig_name,
                    matched_via=Provenance.from_steps(
                        "loinc_component",
                        [
                            ProvenanceStep(op="input", source="LNC", code=code),
                            ProvenanceStep(
                                op="component",
                                source="LNC",
                                code=part_code,
                                name=part_name,
                                tty="LPDN",
                                depth=1,
                            ),
                        ],
                    ),
                )
            elif friendly_name and not _is_broad_friendly_name(friendly_source, friendly_name):
                by_code[code] = _Row(
                    code=code,
                    source="LNC",
                    name=friendly_name,
                    friendly_source=friendly_source,
                    match_type="component",
                    match_depth=1,
                    technical_name=orig_name,
                    matched_via=Provenance.from_steps(
                        "loinc_component",
                        [
                            ProvenanceStep(op="input", source="LNC", code=code),
                            ProvenanceStep(
                                op="component",
                                source="LNC",
                                code=friendly_part_code,
                                name=friendly_part_name,
                                depth=1,
                            ),
                            ProvenanceStep(
                                op="friendly_atom",
                                source=friendly_source,
                                name=friendly_name,
                                tty=tty,
                                cui=cui,
                                depth=1,
                            ),
                        ],
                    ),
                )
            elif common_name:
                by_code[code] = _Row(
                    code=code,
                    source="LNC",
                    name=common_name,
                    friendly_source="LNC",
                    match_type="loinc_common",
                    match_depth=0,
                    technical_name=orig_name,
                    matched_via=self._simple_provenance("loinc_common", "LNC", code, common_name),
                )
            else:
                by_code[code] = self._make_original(code, "LNC", technical_name=orig_name)

        rows_out = [by_code.get(code) or self._make_original(code, "LNC") for code in codes]
        self._apply_snomed_fallback("LNC", rows_out, max_depth)
        return rows_out

    def _resolve_cpt(self, codes: Sequence[str], max_depth: int) -> list[_Row]:
        rows = self._resolve_default(codes, "CPT", max_depth)
        fallback_codes = [row.code for row in rows if row.match_type == "original"]
        if not fallback_codes:
            return rows

        mapping = self._map_cpt_targets(fallback_codes)
        target_groups: dict[str, list[str]] = defaultdict(list)
        for _cpt, (target_source, target_code) in mapping.items():
            target_groups[target_source].append(target_code)
        target_results: dict[tuple[str, str], _Row] = {}
        for target_source, target_codes in target_groups.items():
            if target_source == "SNOMEDCT_US":
                replacements = self._resolve_default_via_snomed(target_codes, "SNOMEDCT_US", max_depth)
                target_results.update({("SNOMEDCT_US", k): v for k, v in replacements.items()})
            else:
                for row in self._resolve_default(target_codes, target_source, max_depth):
                    self._apply_snomed_fallback(target_source, [row], max_depth)
                    target_results[(target_source, row.code)] = row

        by_code = {row.code: row for row in rows}
        for cpt_code, (target_source, target_code) in mapping.items():
            replacement = target_results.get((target_source, target_code))
            base = by_code.get(cpt_code)
            if not base or not replacement or replacement.match_type in {"original", "none"}:
                continue
            base.name = replacement.name
            base.friendly_source = replacement.friendly_source
            base.match_type = replacement.match_type
            base.match_depth = replacement.match_depth
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
        return rows

    def _map_cpt_targets(self, codes: Sequence[str]) -> dict[str, tuple[str, str]]:
        if not codes:
            return {}
        with self._temp_codes(codes) as temp:
            rows = self.con.execute(
                f"""
                SELECT DISTINCT c.CODE AS cpt_code, t.SAB, t.CODE
                FROM mrconso c
                JOIN mrconso t ON t.CUI = c.CUI
                WHERE c.SAB = 'CPT' AND c.SUPPRESS = 'N'
                  AND c.CODE IN (SELECT code FROM {temp})
                  AND t.SAB IN ('HCPCS', 'ICD10CM', 'SNOMEDCT_US')
                  AND t.SUPPRESS = 'N'
                ORDER BY c.CODE,
                         CASE t.SAB WHEN 'HCPCS' THEN 0 WHEN 'ICD10CM' THEN 1 ELSE 2 END,
                         t.CODE
                """
            ).fetchall()
        mapping: dict[str, tuple[str, str]] = {}
        for cpt_code, target_source, target_code in rows:
            current = mapping.get(cpt_code)
            candidate = (target_source, target_code)
            if current is None or _CPT_TARGET_PRIORITY[target_source] < _CPT_TARGET_PRIORITY[current[0]]:
                mapping[cpt_code] = candidate
        return mapping

    def _resolve_cvx(self, codes: Sequence[str]) -> list[_Row]:
        rows: list[_Row] = []
        for code in codes:
            groups = self.cvx_groups.get(code)
            if groups:
                name = " / ".join(sorted(dict.fromkeys(groups)))
                rows.append(
                    _Row(
                        code=code,
                        source="CVX",
                        name=name,
                        friendly_source="CVX",
                        match_type="cvx_group",
                        match_depth=0,
                        technical_name=self._technical_name(code, "CVX"),
                        matched_via=Provenance.from_steps(
                            "cvx_group",
                            [
                                ProvenanceStep(op="input", source="CVX", code=code),
                                ProvenanceStep(op="vaccine_group", source="CVX", code=code, name=name),
                            ],
                        ),
                    )
                )
            else:
                rows.append(self._make_original(code, "CVX"))
        return rows

    def _map_snomed_codes(self, codes: Sequence[str]) -> dict[str, tuple[str, str, bool]]:
        if not codes:
            return {}
        codes = _dedupe(codes)
        if len(codes) > self.query_chunk_size:
            mapping: dict[str, tuple[str, str, bool]] = {}
            chunks = list(_chunks(codes, self.query_chunk_size))
            for chunk_index, chunk in enumerate(chunks, 1):
                self._progress(
                    f"mapping SNOMEDCT_US chunk {chunk_index}/{len(chunks)} "
                    f"({len(chunk)} codes)"
                )
                mapping.update(self._map_snomed_codes(chunk))
            return mapping
        mapping: dict[str, tuple[str, str, bool]] = {}
        with self._temp_codes(codes) as temp:
            direct_rows = self.con.execute(
                f"""
                WITH candidates AS (
                    SELECT DISTINCT sn.CODE AS sn_code, target.SAB AS target_source,
                           target.CODE AS target_code
                    FROM mrrel r
                    JOIN mrconso sn ON sn.AUI = r.AUI1
                    JOIN mrconso target ON target.AUI = r.AUI2
                    WHERE r.RELA = 'mapped_from'
                      AND sn.SAB = 'SNOMEDCT_US' AND sn.SUPPRESS = 'N'
                      AND sn.CODE IN (SELECT code FROM {temp})
                      AND target.SAB IN ('ICD10CM', 'RXNORM', 'LNC', 'CPT')
                      AND target.SUPPRESS = 'N'
                    UNION
                    SELECT DISTINCT sn.CODE AS sn_code, target.SAB AS target_source,
                           target.CODE AS target_code
                    FROM mrconso sn
                    JOIN mrconso target ON target.CUI = sn.CUI
                    WHERE sn.SAB = 'SNOMEDCT_US' AND sn.SUPPRESS = 'N'
                      AND sn.CODE IN (SELECT code FROM {temp})
                      AND target.SAB IN ('ICD10CM', 'RXNORM', 'LNC', 'CPT')
                      AND target.SUPPRESS = 'N'
                )
                SELECT sn_code, target_source, target_code
                FROM candidates
                ORDER BY sn_code,
                         CASE target_source WHEN 'ICD10CM' THEN 0 WHEN 'RXNORM' THEN 1
                                            WHEN 'LNC' THEN 2 ELSE 3 END,
                         target_code
                """
            ).fetchall()
        for sn_code, target_source, target_code in direct_rows:
            current = mapping.get(sn_code)
            if current is None or _SNOMED_TARGET_PRIORITY[target_source] < _SNOMED_TARGET_PRIORITY[current[0]]:
                mapping[sn_code] = (target_source, target_code, False)

        unmatched = [code for code in codes if code not in mapping]
        if unmatched:
            for sn_code, target_source, target_code in self._map_snomed_broader(unmatched):
                mapping.setdefault(sn_code, (target_source, target_code, True))
        return mapping

    def _map_snomed_broader(self, codes: Sequence[str]) -> list[tuple[str, str, str]]:
        if len(codes) > self.query_chunk_size:
            rows: list[tuple[str, str, str]] = []
            chunks = list(_chunks(codes, self.query_chunk_size))
            for chunk_index, chunk in enumerate(chunks, 1):
                self._progress(
                    f"mapping broader SNOMEDCT_US chunk {chunk_index}/{len(chunks)} "
                    f"({len(chunk)} codes)"
                )
                rows.extend(self._map_snomed_broader(chunk))
            return rows
        with self._temp_codes(codes) as temp:
            rows = self.con.execute(
                f"""
                WITH RECURSIVE walk AS (
                    SELECT CODE AS input_code, AUI, CUI, 0 AS depth
                    FROM mrconso
                    WHERE SAB = 'SNOMEDCT_US' AND SUPPRESS = 'N'
                      AND CODE IN (SELECT code FROM {temp})
                    UNION
                    SELECT w.input_code, p.AUI, p.CUI, w.depth + 1
                    FROM walk w
                    JOIN mrrel r ON r.AUI1 = w.AUI AND r.REL = 'PAR'
                    JOIN mrconso p ON p.AUI = r.AUI2
                    WHERE w.depth < 2
                      AND p.SAB = 'SNOMEDCT_US' AND p.SUPPRESS = 'N'
                ),
                candidates AS (
                    SELECT DISTINCT w.input_code, target.SAB, target.CODE, w.depth,
                           ROW_NUMBER() OVER (
                               PARTITION BY w.input_code
                               ORDER BY w.depth,
                                        CASE target.SAB WHEN 'ICD10CM' THEN 0
                                                        WHEN 'RXNORM' THEN 1 ELSE 2 END,
                                        target.CODE
                           ) AS rn
                    FROM walk w
                    JOIN mrconso target ON target.CUI = w.CUI
                    WHERE w.depth > 0
                      AND target.SAB IN ('ICD10CM', 'RXNORM', 'CPT')
                      AND target.SUPPRESS = 'N'
                )
                SELECT input_code, SAB, CODE FROM candidates WHERE rn = 1
                """
            ).fetchall()
        return [(row[0], row[1], row[2]) for row in rows]

    def _resolve_snomed(
        self,
        snomed_codes: Sequence[str],
        snomed_map: Mapping[str, tuple[str, str, bool]],
        non_snomed: Mapping[tuple[str, str], _Row],
        max_depth: int,
    ) -> list[_Row]:
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
                            technical_name=self._technical_name(code, "SNOMEDCT_US"),
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
                            technical_name=self._technical_name(code, "SNOMEDCT_US"),
                            matched_via=target.matched_via,
                        )
                    fallback.append((code, is_broader, mapped_original))
            else:
                fallback.append((code, False, None))

        fallback_rows = self._resolve_default_via_snomed(
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
                rows.append(self._make_original(code, "SNOMEDCT_US"))
        return rows

    def _technical_name(self, code: str, source: str) -> str | None:
        row = self.con.execute(
            """
            SELECT STR
            FROM mrconso
            WHERE CODE = ? AND SAB = ? AND SUPPRESS = 'N'
            ORDER BY AUI
            LIMIT 1
            """,
            [code, source],
        ).fetchone()
        return row[0] if row else None

    def _make_original(
        self,
        code: str,
        source: str,
        *,
        technical_name: str | None = None,
    ) -> _Row:
        technical_name = technical_name or self._technical_name(code, source)
        if technical_name:
            return _Row(
                code=code,
                source=source,
                name=technical_name,
                friendly_source=source,
                match_type="original",
                match_depth=0,
                technical_name=technical_name,
                matched_via=self._simple_provenance("original", source, code, technical_name),
            )
        return self._make_none(code, source)

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

    def _snomed_top_level_depths(self, codes: Sequence[str]) -> dict[str, int]:
        if not codes or not self._table_exists("snomed_top_level_depth"):
            return {}
        with self._temp_codes(codes) as temp:
            rows = self.con.execute(
                f"""
                SELECT code, min_top_depth
                FROM snomed_top_level_depth
                WHERE code IN (SELECT code FROM {temp})
                """
            ).fetchall()
        return {row[0]: int(row[1]) for row in rows if row[1] is not None}

    def _table_exists(self, name: str) -> bool:
        row = self.con.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_name = ?
            LIMIT 1
            """,
            [name],
        ).fetchone()
        return bool(row)

    @contextmanager
    def _temp_codes(self, codes: Sequence[str]) -> Iterator[str]:
        table = f"_mt4ds_codes_{uuid4().hex}"
        self.con.execute(f"CREATE TEMP TABLE {table} (code VARCHAR)")
        try:
            self.con.executemany(
                f"INSERT INTO {table} VALUES (?)",
                [(str(code),) for code in _dedupe(codes)],
            )
            yield table
        finally:
            self.con.execute(f"DROP TABLE IF EXISTS {table}")

    @contextmanager
    def _temp_code_ordinals(self, code_ordinals: Sequence[tuple[int, str]]) -> Iterator[str]:
        table = f"_mt4ds_codes_{uuid4().hex}"
        self.con.execute(f"CREATE TEMP TABLE {table} (ordinal INTEGER, code VARCHAR)")
        try:
            self.con.executemany(
                f"INSERT INTO {table} VALUES (?, ?)",
                [(int(ordinal), str(code)) for ordinal, code in code_ordinals],
            )
            yield table
        finally:
            self.con.execute(f"DROP TABLE IF EXISTS {table}")


def _dedupe(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values))


def _chunks(values: Sequence[T], size: int) -> Iterator[list[T]]:
    for start in range(0, len(values), size):
        yield list(values[start:start + size])


def _is_broad_friendly_name(friendly_source: str | None, name: str | None) -> bool:
    if not friendly_source or not name:
        return False
    lowered = name.strip().lower()
    if friendly_source == "MEDLINEPLUS":
        return lowered in _BROAD_MEDLINEPLUS_NAMES
    if friendly_source == "CHV":
        return lowered in _BROAD_CHV_NAMES
    return False


def _normalize_term_tokens(name: str | None) -> set[str]:
    if not name:
        return set()
    tokens = re.sub(r"[^a-z0-9]+", " ", name.lower())
    return {
        token
        for token in tokens.split()
        if token and token not in _COMBO_TERM_STOPWORDS and len(token) > 2
    }


def _is_combo_chv_mismatch(source_name: str | None, chv_name: str | None) -> bool:
    if not source_name or not chv_name:
        return False
    if not any(sep in source_name.lower() for sep in _COMBO_SEP_HINTS):
        return False
    source_tokens = _normalize_term_tokens(source_name)
    chv_tokens = _normalize_term_tokens(chv_name)
    return bool(source_tokens and chv_tokens and source_tokens.isdisjoint(chv_tokens))
