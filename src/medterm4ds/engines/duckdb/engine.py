"""Local DuckDB terminology engine.

The engine is batch-first and keeps large data in DuckDB. It uses temp input
tables instead of large Python-side object graphs.
"""

from __future__ import annotations

import logging
import os
import re
import urllib.request
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar
from uuid import uuid4

from medterm4ds.core.config import LocalDuckDBConfig
from medterm4ds.core.models import (
    CodeInfo,
    CodeMapping,
    CodeRef,
    CodeRelation,
    CodeResolution,
    FriendlyNameResult,
    NameSearchResult,
    OptimizeResult,
    OptimizeRule,
    Provenance,
    ProvenanceStep,
    SourceStats,
)
from medterm4ds.engines.duckdb import hierarchy as _hierarchy
from medterm4ds.engines.duckdb import mappings as _mappings
from medterm4ds.engines.duckdb import patient_friendly as _patient_friendly
from medterm4ds.engines.duckdb import resolution as _resolution
from medterm4ds.sources.base import (
    BROAD_CHV_NAMES as _BROAD_CHV_NAMES,
)
from medterm4ds.sources.base import (
    BROAD_MEDLINEPLUS_NAMES as _BROAD_MEDLINEPLUS_NAMES,
)
from medterm4ds.sources.rxnorm import (
    RXNORM_BASE_TTY_PRIORITY as _RXNORM_BASE_TTY_PRIORITY,
)
from medterm4ds.sources.rxnorm import (
    RXNORM_GROUP_TTYS as _RXNORM_GROUP_TTYS,
)
from medterm4ds.sources.rxnorm import (
    RXNORM_KNOWN_TTYS as _RXNORM_KNOWN_TTYS,
)
from medterm4ds.sources.rxnorm import (
    find_tty_path as _rxnorm_find_tty_path,
)

logger = logging.getLogger(__name__)
T = TypeVar("T")

# SNOMED target priority for the engine's fallback path (raw mrrel/mrconso
# traversal with MRSTY-based routing). INTENTIONALLY DIFFERENT from
# sources.snomed.SNOMED_TARGET_PRIORITY:
#   - sources.snomed.SNOMED_TARGET_PRIORITY (5 entries) is used by the
#     prepared mt4ds.walk_edges path, where RXNORM and CVX have their own
#     dedicated resolution paths and should NOT be selected via SNOMED-target
#     mapping.
#   - engine._SNOMED_TARGET_PRIORITY (7 entries) is used by the fallback path
#     when prepared tables are unavailable. MRSTY-based routing (see
#     _SNOMED_TUI_TARGETS below) routes Pharmacologic Substance (T121) to
#     RXNORM and detects vaccines via CUI crosswalk to CVX. These targets
#     need priority entries or the fallback misroutes.
# Do NOT consolidate these two dicts without unifying the routing policies.
_SNOMED_FALLBACK_SOURCES = {"ICD10CM", "ICD10PCS", "LNC", "HCPCS", "CPT", "RXNORM", "CVX"}
_SNOMED_TARGET_PRIORITY = {
    "CVX": 0,
    "ICD10CM": 1,
    "ICD10PCS": 2,
    "LNC": 3,
    "RXNORM": 4,
    "CPT": 5,
    "HCPCS": 6,
}

# UMLS semantic types (TUI) → target source vocabularies that are semantically
# compatible. Used by _map_snomed_codes to filter crosswalk candidates so a
# SNOMED concept routes to a clinically appropriate target (e.g., Pharmacologic
# Substance → RXNORM, not LNC). CVX is intentionally absent: vaccines share
# generic substance TUIs and are detected via crosswalk existence instead.
_SNOMED_TUI_TARGETS: dict[str, tuple[str, ...]] = {
    # Conditions → ICD10CM
    "T019": ("ICD10CM",),  # Congenital Abnormality
    "T020": ("ICD10CM",),  # Acquired Abnormality
    "T037": ("ICD10CM",),  # Injury or Poisoning
    "T046": ("ICD10CM",),  # Pathologic Function
    "T047": ("ICD10CM",),  # Disease or Syndrome
    "T048": ("ICD10CM",),  # Mental or Behavioral Dysfunction
    "T049": ("ICD10CM",),  # Cell or Molecular Dysfunction
    "T190": ("ICD10CM",),  # Anatomical Abnormality
    "T191": ("ICD10CM",),  # Neoplastic Process
    # Labs → LNC
    "T034": ("LNC",),      # Laboratory or Test Result
    "T059": ("LNC",),      # Laboratory Procedure
    # Substances / Drugs → RXNORM
    # Restrictive: only pharmacologic-substance or clinical-drug TUIs trigger
    # RXNORM routing. Endogenous proteins (T116 alone, e.g., the PMS2 gene
    # product) and pure organic chemicals without pharmacologic semantics are
    # excluded — they may share a CUI with a drug but aren't drugs themselves.
    "T121": ("RXNORM",),   # Pharmacologic Substance
    "T123": ("RXNORM",),   # Biologically Active Substance
    "T200": ("RXNORM",),   # Clinical Drug
    # Procedures → CPT (and ICD10PCS for surgical, priority picks ICD10PCS first)
    "T060": ("CPT", "ICD10PCS"),  # Diagnostic Procedure
    "T061": ("CPT", "ICD10PCS"),  # Therapeutic or Preventive Procedure
    "T062": ("CPT", "ICD10PCS"),  # Research Activity
    "T063": ("CPT", "ICD10PCS"),  # Molecular Biology Research Technique
}

# All target SABs that the SNOMED crosswalk may consider when MRSTY is loaded.
# RXNORM and CVX require TUI-based filtering (otherwise the legacy priority-only
# fallback could misroute gene products to drugs via shared-CUI crosswalks).
_SNOMED_TARGET_SABS_WITH_MGSTY = (
    "ICD10CM", "ICD10PCS", "LNC", "RXNORM", "CVX", "CPT", "HCPCS",
)
_SNOMED_TARGET_SABS_LEGACY = (
    "ICD10CM", "ICD10PCS", "LNC", "CPT", "HCPCS",
)


def _snomed_tui_target_pairs_sql() -> str:
    """SQL producing (tui, target_source) rows for every TUI in the map.

    A TUI that maps to several targets (e.g., procedure TUIs → CPT and
    ICD10PCS) expands into multiple rows via UNION ALL.
    """
    pairs: list[tuple[str, str]] = []
    for tui, targets in _SNOMED_TUI_TARGETS.items():
        for target in targets:
            pairs.append((tui, target))
    return " UNION ALL ".join(
        f"SELECT '{tui}' AS tui, '{target}' AS target_source" for tui, target in pairs
    )


def _has_mrsty_table(con) -> bool:
    """Return True if the mrsty table is loaded and queryable."""
    try:
        con.execute("SELECT 1 FROM mrsty LIMIT 1").fetchone()
        return True
    except Exception:
        return False


_CPT_TARGET_PRIORITY = {"HCPCS": 0, "ICD10CM": 1, "SNOMEDCT_US": 2}
_SNOMED_TOP_LEVEL_GUARD_DEPTH = 3
_SNOMED_TOP_LEVEL_GUARD_EXEMPT_MATCH_TYPES = {"same_cui"}
_SNOMED_PARENT_LINKS_CACHE_TABLE = "_mt4ds_snomed_parent_links"
_SNOMED_FALLBACK_QUERY_CHUNK_SIZE = 25
_CVX_GROUP_URL = "https://www2.cdc.gov/vaccines/iis/iisstandards/downloads/VG.txt"
_CVX_GROUP_CACHE: dict[str, list[str]] | None = None
_HIERARCHY_RELATIONSHIPS = {
    "parents": "parent",
    "children": "child",
    "ancestors": "ancestor",
    "descendants": "descendant",
}
_REPLACEMENT_RELAS = (
    "replaced_by",
    "same_as",
    "possibly_replaced_by",
    "mapped_to",
    "moved_to",
)
_DEFAULT_OPTIMIZE_REL = {
    "ICD10CM": "isa",
    "ICD10PCS": "isa",
    "SNOMEDCT_US": "isa",
    "ATC": "isa",
}
_PAR_HIERARCHY_SOURCES = frozenset({"ICD10CM", "ICD10PCS", "HCPCS", "LNC"})
_RELA_ISA_HIERARCHY_SOURCES = frozenset({"ATC", "CPT", "MSH", "RXNORM"})
# UMLS-only hierarchy traversal policy: never infer parent/child relationships
# from code strings or ranges. Hierarchy comes only from source data normalized
# into mt4ds.walk_edges / mt4ds.hierarchy_edges.

# _BROAD_CHV_NAMES and _BROAD_MEDLINEPLUS_NAMES imported from sources.base (canonical).
_BROAD_CHV_NAME_SQL = ", ".join(f"'{name}'" for name in sorted(_BROAD_CHV_NAMES))
_BROAD_MEDLINEPLUS_NAME_SQL = ", ".join(f"'{name}'" for name in sorted(_BROAD_MEDLINEPLUS_NAMES))
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


class LocalDuckDBEngine:
    """Low-memory DuckDB engine for patient-friendly batch resolution."""

    def __init__(
        self,
        con,
        *,
        config: LocalDuckDBConfig | None = None,
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
        self._cvx_groups_auto = cvx_groups is None
        self.cvx_groups = {str(k): list(v) for k, v in (cvx_groups or {}).items()}
        self.query_chunk_size = max(1, int(query_chunk_size))
        self.progress = progress
        self.cache_prepared = False
        self._snomed_parent_links_cache_prepared = False
        self._prepared_tables_available: bool | None = None
        self._active_source_code_cache: dict[str, set[str]] = {}
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
        """Prepare low-memory temp tables for repeated local DuckDB queries.

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
        self.con.execute(
            f"""
            INSERT INTO mrrel
            SELECT r.AUI1, r.AUI2, r.RELA, r.REL
            FROM {base_rel} r
            WHERE r.AUI1 IN (SELECT AUI FROM mt4ds_cache_aui)
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
                    logger.debug("Skipping local DuckDB cache index %s: %s", ddl, exc)

        self.cache_prepared = True

    def _ensure_snomed_parent_links_cache(self) -> str | None:
        """Create a per-connection temp table for SNOMED child->parent edges."""
        if self._snomed_parent_links_cache_prepared:
            return _SNOMED_PARENT_LINKS_CACHE_TABLE
        try:
            self.con.execute(
                f"""
                CREATE TEMP TABLE IF NOT EXISTS {_SNOMED_PARENT_LINKS_CACHE_TABLE} AS
                SELECT DISTINCT r.AUI1 AS child_aui, r.AUI2 AS parent_aui
                FROM mrrel r
                JOIN mrconso child ON child.AUI = r.AUI1
                JOIN mrconso parent ON parent.AUI = r.AUI2
                WHERE r.REL = 'PAR'
                  AND COALESCE(r.RELA, 'isa') IN ('isa', 'inverse_isa')
                  AND child.SAB = 'SNOMEDCT_US'
                  AND parent.SAB = 'SNOMEDCT_US'
                  AND child.SUPPRESS = 'N'
                  AND parent.SUPPRESS = 'N'
                UNION
                SELECT DISTINCT r.AUI2 AS child_aui, r.AUI1 AS parent_aui
                FROM mrrel r
                JOIN mrconso parent ON parent.AUI = r.AUI1
                JOIN mrconso child ON child.AUI = r.AUI2
                WHERE r.REL = 'CHD'
                  AND COALESCE(r.RELA, 'isa') IN ('isa', 'inverse_isa')
                  AND child.SAB = 'SNOMEDCT_US'
                  AND parent.SAB = 'SNOMEDCT_US'
                  AND child.SUPPRESS = 'N'
                  AND parent.SUPPRESS = 'N'
                """
            )
            try:
                self.con.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS idx_{_SNOMED_PARENT_LINKS_CACHE_TABLE}_child
                    ON {_SNOMED_PARENT_LINKS_CACHE_TABLE}(child_aui)
                    """
                )
            except Exception as exc:
                logger.debug("Skipping SNOMED parent link cache index: %s", exc)
            self._snomed_parent_links_cache_prepared = True
            return _SNOMED_PARENT_LINKS_CACHE_TABLE
        except Exception as exc:
            logger.debug("Failed to create SNOMED parent link cache: %s", exc)
            return None

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

        # Patient-friendly hierarchy policy:
        # - ICD/CPT/HCPCS-like sources walk their own hierarchy first and
        #   stop at the first depth with a non-heading MEDLINEPLUS/CHV atom,
        #   preferring MEDLINEPLUS only within that depth frontier.
        # - LOINC keeps its source-native component/axis/common-name tiers, then
        #   participates in SNOMED fallback if those tiers miss.
        # - If source-native hierarchy misses, fall back through SNOMED and use
        #   the same first-frontier rule. SNOMED fallback accepts nodes at
        #   top-level depth >= 4 and does not expand into levels 1-3.
        # - RxNorm and CVX use separate source-native strategies.
        ordered = [CodeRef(source=c.source, code=c.code) for c in codes]
        if self._has_patient_friendly_prepared_tables({ref.source for ref in ordered}):
            try:
                return self._get_patient_friendly_names_prepared(ordered, max_depth=max_depth)
            except Exception as exc:
                logger.debug("Falling back to raw patient-friendly path: %s", exc)

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

    def _get_patient_friendly_names_prepared(
        self,
        codes: Sequence[CodeRef],
        *,
        max_depth: int,
    ) -> list[FriendlyNameResult]:
        from medterm4ds.services.patient_friendly_prepared import (
            get_non_rxnorm_patient_friendly,
        )
        from medterm4ds.services.rxnorm_tty_walk import get_rxnorm_patient_friendly

        rxnorm_items: list[tuple[int, CodeRef]] = []
        other_items: list[tuple[int, CodeRef]] = []
        for index, code in enumerate(codes):
            if code.source == "RXNORM":
                rxnorm_items.append((index, code))
            else:
                other_items.append((index, code))

        by_index: dict[int, FriendlyNameResult] = {}
        if rxnorm_items:
            rxnorm_rows = get_rxnorm_patient_friendly(
                [code for _index, code in rxnorm_items],
                self.con,
            )
            for (index, _code), row in zip(rxnorm_items, rxnorm_rows, strict=True):
                by_index[index] = row
        if other_items:
            other_rows = get_non_rxnorm_patient_friendly(
                [code for _index, code in other_items],
                self.con,
                max_depth=max_depth,
            )
            for (index, _code), row in zip(other_items, other_rows, strict=True):
                by_index[index] = row

        return [by_index[index] for index in range(len(codes))]

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

    def resolve_codes(self, codes: Sequence[CodeRef]) -> list[CodeResolution]:
        """Resolve active, historical, obsolete, and NDC inputs."""
        return [self._resolve_code(CodeRef(source=code.source, code=code.code)) for code in codes]

    def optimize_codes(
        self,
        codes: Sequence[CodeRef],
        *,
        relationship: str | None = None,
        output_format: str = "compact",
        include_codes: bool = False,
    ) -> OptimizeResult:
        """Optimize a source-specific valueset into hierarchy include/exclude rules."""
        if output_format not in {"compact", "flat"}:
            raise ValueError("output_format must be compact or flat")
        if not codes:
            return OptimizeResult(
                source="",
                relationship=relationship or "isa",
                rules=(),
                original_count=0,
                optimized_count=0,
                reduction=0.0,
            )
        refs = [CodeRef(source=code.source, code=code.code) for code in codes]
        sources = {ref.source for ref in refs}
        if len(sources) != 1:
            raise ValueError("optimize_codes requires all codes to use the same source")
        source = refs[0].source
        rel = relationship or _DEFAULT_OPTIMIZE_REL.get(source, "isa")
        if str(rel).lower() == "prefix":
            raise ValueError("prefix optimize is not supported; use UMLS hierarchy relationships")

        leaves = self._normalize_optimize_input(refs, rel)
        remaining = set(leaves)
        if not remaining:
            return OptimizeResult(
                source=source,
                relationship=rel,
                rules=(),
                original_count=len(refs),
                optimized_count=0,
                reduction=0.0,
            )

        ancestor_cache = self._related_code_map(
            source,
            sorted(remaining),
            relationship=rel,
            upward=True,
            max_depth=12,
        )
        candidate_set = set(remaining)
        for ancestors in ancestor_cache.values():
            candidate_set.update(ancestors)
        leaf_cache = self._leaf_descendants_for_candidates(source, sorted(candidate_set), rel)
        rules: list[OptimizeRule] = []

        while remaining:
            best_code: str | None = None
            best_covered: set[str] = set()
            best_excluded: set[str] = set()
            best_score = -1.0
            candidates = set(remaining)
            for code in remaining:
                candidates.update(ancestor_cache.get(code, set()))

            for candidate in sorted(candidates):
                descendant_leaves = leaf_cache.get(candidate, set())
                if not descendant_leaves:
                    descendant_leaves = {candidate}
                covered = descendant_leaves & remaining
                if not covered:
                    continue
                excluded = descendant_leaves - remaining
                mentions = 1 + len(excluded)
                score = len(covered) / mentions
                if (
                    score > best_score
                    or (
                        score == best_score
                        and (
                            len(excluded) < len(best_excluded)
                            or (best_code is not None and candidate > best_code)
                        )
                    )
                ):
                    best_code = candidate
                    best_covered = covered
                    best_excluded = excluded
                    best_score = score

            if best_code is None:
                best_code = min(remaining)
                best_covered = {best_code}
                best_excluded = set()

            if output_format == "flat":
                rules.append(
                    OptimizeRule(
                        include=CodeRef(source, best_code),
                        covered_codes=tuple(CodeRef(source, code) for code in sorted(best_covered)),
                    )
                )
                rules.extend(
                    OptimizeRule(include=CodeRef(source, code))
                    for code in sorted(best_excluded)
                )
            else:
                rules.append(
                    OptimizeRule(
                        include=CodeRef(source, best_code),
                        exclude=tuple(CodeRef(source, code) for code in sorted(best_excluded)),
                        covered_codes=tuple(CodeRef(source, code) for code in sorted(best_covered)),
                        excluded_codes=tuple(CodeRef(source, code) for code in sorted(best_excluded)),
                    )
                )
            remaining -= best_covered

        reduction = 0.0
        if refs:
            reduction = round((1 - (len(rules) / len(refs))) * 100, 2)
        return OptimizeResult(
            source=source,
            relationship=rel,
            rules=tuple(rules),
            original_count=len(refs),
            optimized_count=len(rules),
            reduction=reduction,
        )

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
            filters.append(f"{source_col} IN ({','.join(['?'] * len(normalized_sources))})")
            filter_params.extend(normalized_sources)
        if tty_filters:
            normalized_ttys = _dedupe([tty.upper() for tty in tty_filters])
            filters.append(f"{tty_col} IN ({','.join(['?'] * len(normalized_ttys))})")
            filter_params.extend(normalized_ttys)

        lowered_query = stripped_query.lower()
        prefix_pattern = f"{lowered_query}%"
        contains_pattern = f"%{lowered_query}%"

        rows = self.con.execute(
            f"""
            WITH ranked AS (
                SELECT {source_col} AS SAB, {code_col} AS CODE,
                       {name_col} AS STR, {cui_col} AS CUI,
                       {aui_col} AS AUI, {tty_col} AS TTY,
                       CASE
                           WHEN LOWER({name_col}) = ? THEN 'exact'
                           WHEN LOWER({name_col}) LIKE ? THEN 'prefix'
                           ELSE 'contains'
                       END AS match_type,
                       ROW_NUMBER() OVER (
                           PARTITION BY {source_col}, {code_col}
                           ORDER BY
                               CASE
                                   WHEN LOWER({name_col}) = ? THEN 0
                                   WHEN LOWER({name_col}) LIKE ? THEN 1
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
                  AND LOWER({name_col}) LIKE ?
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
        target_sources = _dedupe(target_sources)
        if (
            not include_target_ancestors
            and not include_target_descendants
            and (
                self._table_exists("crosswalk_edges")
                or self._table_exists("same_cui_edges")
            )
            and self._table_exists("best_atoms")
            and (max_depth == 0 or self._table_exists("walk_edges"))
        ):
            from medterm4ds.services.crosswalk_prepared import get_crosswalk_mappings

            unique_ordered: list[CodeRef] = []
            seen_refs: set[tuple[str, str]] = set()
            for ref in ordered:
                key = (ref.source, ref.code)
                if key not in seen_refs:
                    seen_refs.add(key)
                    unique_ordered.append(ref)
            prepared_mappings = get_crosswalk_mappings(
                unique_ordered,
                self.con,
                target_sources=target_sources,
                max_depth=max_depth,
            )
            prepared_rows = [
                (index, mapping)
                for index, ref in enumerate(ordered)
                for mapping in prepared_mappings
                if mapping.source.source == ref.source and mapping.source.code == ref.code
            ]
            prepared_rows = self._filter_snomed_top_level_mappings(prepared_rows)
            return _cap_mappings_per_input(prepared_rows, max_results_per_code)

        grouped: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for ordinal, ref in enumerate(ordered):
            grouped[ref.source].append((ordinal, ref.code))

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
                        for target_source in target_sources:
                            rows.extend(
                                self._get_target_hierarchy_mappings(
                                    source,
                                    chunk,
                                    target_sources=[target_source],
                                    max_results_per_code=max_results_per_code,
                                    max_depth=max_depth,
                                    upward=True,
                                )
                            )
                    if include_target_descendants:
                        for target_source in target_sources:
                            rows.extend(
                                self._get_target_hierarchy_mappings(
                                    source,
                                    chunk,
                                    target_sources=[target_source],
                                    max_results_per_code=max_results_per_code,
                                    max_depth=max_depth,
                                    upward=False,
                                )
                            )
        rows = self._filter_snomed_top_level_mappings(rows)
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

    def _filter_snomed_top_level_mappings(self, rows):
        return _mappings._filter_snomed_top_level_mappings(self, rows=rows)


    def _resolve_code(self, ref):
        return _resolution._resolve_code(self, ref=ref)


    def _active_source_code_set(self, source):
        return _resolution._active_source_code_set(self, source=source)


    def _resolve_ndc(self, ref):
        return _resolution._resolve_ndc(self, ref=ref)


    def _lookup_any_code(self, ref):
        return _resolution._lookup_any_code(self, ref=ref)


    def _replacement_candidates(self, historical):
        return _resolution._replacement_candidates(self, historical=historical)


    def _normalize_optimize_input(self, refs: Sequence[CodeRef], relationship: str) -> set[str]:
        source = refs[0].source
        input_codes = {ref.code for ref in refs}
        leaf_map = self._leaf_descendants_for_candidates(source, sorted(input_codes), relationship)
        leaves: set[str] = set()
        for code in sorted(input_codes):
            descendant_leaves = leaf_map.get(code, set())
            if descendant_leaves:
                leaves.update(descendant_leaves)
            else:
                leaves.add(code)
        return leaves

    def _leaf_descendants_for_candidates(
        self,
        source: str,
        codes: Sequence[str],
        relationship: str,
    ) -> dict[str, set[str]]:
        descendant_map = self._related_code_map(
            source,
            codes,
            relationship=relationship,
            upward=False,
            max_depth=12,
        )
        all_descendants = sorted({code for descendants in descendant_map.values() for code in descendants})
        if not all_descendants:
            return {code: set() for code in codes}
        child_map = self._related_code_map(
            source,
            all_descendants,
            relationship=relationship,
            upward=False,
            max_depth=1,
        )
        non_leaf = {code for code, children in child_map.items() if children}
        return {
            code: set(descendants) - non_leaf
            for code, descendants in descendant_map.items()
        }

    def _related_code_map(
        self,
        source: str,
        codes: Sequence[str],
        *,
        relationship: str,
        upward: bool,
        max_depth: int,
    ) -> dict[str, set[str]]:
        if not codes:
            return {}
        output = {str(code): set() for code in codes}
        frontier = {str(code): {str(code)} for code in codes}
        seen = {str(code): {str(code)} for code in codes}
        for _depth in range(max_depth):
            frontier_codes = sorted({code for values in frontier.values() for code in values})
            if not frontier_codes:
                break
            direct = self._direct_related_code_map(
                source,
                frontier_codes,
                relationship=relationship,
                upward=upward,
            )
            next_frontier = {origin: set() for origin in frontier}
            for origin, current_codes in frontier.items():
                for current in current_codes:
                    for target in direct.get(current, set()):
                        if target in seen[origin]:
                            continue
                        seen[origin].add(target)
                        output[origin].add(target)
                        next_frontier[origin].add(target)
            frontier = {origin: values for origin, values in next_frontier.items() if values}
        return output

    def _direct_related_code_map(
        self,
        source: str,
        codes: Sequence[str],
        *,
        relationship: str,
        upward: bool,
    ) -> dict[str, set[str]]:
        if not codes:
            return {}
        if _is_isa_relationship(relationship) and self._table_exists("walk_edges"):
            output = {str(code): set() for code in codes}
            for chunk in _chunks([str(code) for code in codes], self.query_chunk_size):
                code_placeholders = ",".join(["?"] * len(chunk))
                if upward:
                    source_code_sql = "from_code"
                    target_code_sql = "to_code"
                    filter_code_sql = "from_code"
                else:
                    source_code_sql = "to_code"
                    target_code_sql = "from_code"
                    filter_code_sql = "to_code"
                rows = self.con.execute(
                    f"""
                    SELECT DISTINCT {source_code_sql} AS source_code,
                           {target_code_sql} AS target_code
                    FROM mt4ds.walk_edges
                    WHERE source = ?
                      AND direction = 'parent'
                      AND {filter_code_sql} IN ({code_placeholders})
                    """,
                    [source, *chunk],
                ).fetchall()
                for source_code, target_code in rows:
                    output.setdefault(str(source_code), set()).add(str(target_code))
            return output

        if _is_isa_relationship(relationship):
            source_join, source_target = _source_hierarchy_join_sql(
                source,
                "c.AUI",
                upward=upward,
            )
            rel_filter_sql = ""
            rel_params: list[str] = []
        else:
            rel_values = _relationship_values(relationship)
            rel_placeholders = ",".join(["?"] * len(rel_values))
            source_join = "r.AUI1 = c.AUI" if upward else "r.AUI2 = c.AUI"
            source_target = "r.AUI2" if upward else "r.AUI1"
            rel_filter_sql = f"AND (r.RELA IN ({rel_placeholders}) OR r.REL IN ({rel_placeholders}))"
            rel_params = [*rel_values, *rel_values]
        output = {str(code): set() for code in codes}
        for chunk in _chunks([str(code) for code in codes], self.query_chunk_size):
            code_placeholders = ",".join(["?"] * len(chunk))
            rows = self.con.execute(
                f"""
                SELECT DISTINCT c.CODE AS source_code, t.CODE AS target_code
                FROM mrconso c
                JOIN mrrel r ON {source_join}
                JOIN mrconso t ON t.AUI = {source_target}
                WHERE c.SAB = ?
                  AND c.SUPPRESS = 'N'
                  AND c.CODE IN ({code_placeholders})
                  AND t.SAB = ?
                  AND t.SUPPRESS = 'N'
                  {rel_filter_sql}
                """,
                [
                    source,
                    *chunk,
                    source,
                    *rel_params,
                ],
            ).fetchall()
            for source_code, target_code in rows:
                output.setdefault(str(source_code), set()).add(str(target_code))
        return output

    def _get_source_code_relations(
        self,
        source: str,
        code_ordinals: Sequence[tuple[int, str]],
        *,
        relationship: str,
        upward: bool,
        max_depth: int,
    ) -> list[tuple[int, CodeRelation]]:
        return _hierarchy.get_source_code_relations(
            self,
            source,
            code_ordinals,
            relationship=relationship,
            upward=upward,
            max_depth=max_depth,
        )

    def _get_source_code_relations_prepared(
        self,
        source: str,
        code_ordinals: Sequence[tuple[int, str]],
        *,
        relationship: str,
        upward: bool,
        max_depth: int,
    ) -> list[tuple[int, CodeRelation]]:
        return _hierarchy.get_source_code_relations_prepared(
            self,
            source,
            code_ordinals,
            relationship=relationship,
            upward=upward,
            max_depth=max_depth,
        )

    def _source_display_lookup(
        self,
        source: str,
        codes: Sequence[str],
    ) -> dict[str, tuple[str, str, str]]:
        return _hierarchy.source_display_lookup(self, source, codes)


    def _get_source_code_mappings(self, source, code_ordinals, *, target_sources, max_results_per_code):
        return _mappings._get_source_code_mappings(self, source=source, code_ordinals=code_ordinals, target_sources=target_sources, max_results_per_code=max_results_per_code)


    def _get_source_code_mappings_prepared(self, source, code_ordinals, *, target_sources, max_results_per_code):
        return _mappings._get_source_code_mappings_prepared(self, source=source, code_ordinals=code_ordinals, target_sources=target_sources, max_results_per_code=max_results_per_code)


    def _get_source_ancestor_mappings(self, source, code_ordinals, *, target_sources, max_results_per_code, max_depth):
        return _mappings._get_source_ancestor_mappings(self, source=source, code_ordinals=code_ordinals, target_sources=target_sources, max_results_per_code=max_results_per_code, max_depth=max_depth)


    def _get_source_ancestor_mappings_prepared(self, source, code_ordinals, *, target_sources, max_results_per_code, max_depth):
        return _mappings._get_source_ancestor_mappings_prepared(self, source=source, code_ordinals=code_ordinals, target_sources=target_sources, max_results_per_code=max_results_per_code, max_depth=max_depth)


    def _get_target_hierarchy_mappings(self, source, code_ordinals, *, target_sources, max_results_per_code, max_depth, upward):
        return _mappings._get_target_hierarchy_mappings(self, source=source, code_ordinals=code_ordinals, target_sources=target_sources, max_results_per_code=max_results_per_code, max_depth=max_depth, upward=upward)


    def _get_target_hierarchy_mappings_prepared(self, source, code_ordinals, *, target_sources, max_results_per_code, max_depth, upward):
        return _mappings._get_target_hierarchy_mappings_prepared(self, source=source, code_ordinals=code_ordinals, target_sources=target_sources, max_results_per_code=max_results_per_code, max_depth=max_depth, upward=upward)


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

    def _resolve_default(self, codes, source, max_depth, *, filter_broad=False):
        return _patient_friendly._resolve_default(self, codes=codes, source=source, max_depth=max_depth, filter_broad=filter_broad)


    def _apply_snomed_fallback(self, source, rows, max_depth):
        return _patient_friendly._apply_snomed_fallback(self, source=source, rows=rows, max_depth=max_depth)


    def _resolve_default_via_snomed(self, codes, source, max_depth):
        return _patient_friendly._resolve_default_via_snomed(self, codes=codes, source=source, max_depth=max_depth)



    def _resolve_rxnorm(self, codes):
        return _patient_friendly._resolve_rxnorm(self, codes=codes)

    def _resolve_loinc(self, codes, max_depth):
        return _patient_friendly._resolve_loinc(self, codes=codes, max_depth=max_depth)


    def _resolve_cpt(self, codes, max_depth):
        return _patient_friendly._resolve_cpt(self, codes=codes, max_depth=max_depth)


    def _map_cpt_targets(self, codes):
        return _mappings._map_cpt_targets(self, codes=codes)


    def _resolve_cvx(self, codes):
        return _patient_friendly._resolve_cvx(self, codes=codes)


    def _map_snomed_codes(self, codes):
        return _mappings._map_snomed_codes(self, codes=codes)


    def _map_snomed_broader(self, codes):
        return _mappings._map_snomed_broader(self, codes=codes)


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

    def _display_name(self, code: str, source: str) -> str | None:
        if source == "LNC":
            row = self.con.execute(
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
        row = self.con.execute(
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

    def _technical_name(self, code: str, source: str) -> str | None:
        if source == "LNC":
            row = self.con.execute(
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
        row = self.con.execute(
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
        self,
        code: str,
        source: str,
        *,
        technical_name: str | None = None,
        display_name: str | None = None,
    ) -> _Row:
        display_name = display_name or self._display_name(code, source)
        technical_name = technical_name or self._technical_name(code, source) or display_name
        if display_name:
            return _Row(
                code=code,
                source=source,
                name=display_name,
                friendly_source=source,
                match_type="original",
                match_depth=0,
                technical_name=technical_name,
                matched_via=self._simple_provenance("original", source, code, display_name),
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

    def _has_prepared_tables(self) -> bool:
        """Check if mt4ds prepared tables are available (cached after first check)."""
        if self._prepared_tables_available is None:
            try:
                rows = self.con.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'mt4ds' AND table_name = 'best_atoms'"
                ).fetchall()
                self._prepared_tables_available = len(rows) > 0
            except Exception:
                self._prepared_tables_available = False
        return self._prepared_tables_available

    def _has_patient_friendly_prepared_tables(self, sources: set[str]) -> bool:
        if not self._prepared_schema_version_is_current():
            return False
        required = {
            "best_atoms",
            "patient_friendly_strategy",
        }
        if sources - {"RXNORM"}:
            required.update({
                "walk_edges",
                "friendly_atoms",
                "snomed_top_level_depth",
                "cvx_metadata",
            })
        if "RXNORM" in sources:
            required.update({
                "rxnorm_tty_paths",
                "rxnorm_tty_path_steps",
                "rxnorm_tty_edges",
            })
        if "SNOMEDCT_US" in sources or any(source in _SNOMED_FALLBACK_SOURCES for source in sources):
            required.update({
                "walk_edges",
                "friendly_atoms",
                "snomed_top_level_depth",
            })
        try:
            rows = self.con.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'mt4ds'
                  AND table_name IN ({})
                """.format(", ".join(["?"] * len(required))),
                list(required),
            ).fetchall()
        except Exception:
            return False
        available = {str(row[0]) for row in rows}
        if available != required:
            return False
        needs_crosswalk = (
            bool(sources - {"RXNORM"})
            or "SNOMEDCT_US" in sources
            or any(source in _SNOMED_FALLBACK_SOURCES for source in sources)
        )
        if needs_crosswalk and not (
            self._table_exists("crosswalk_edges")
            or self._table_exists("same_cui_edges")
        ):
            return False
        return True

    def _prepared_schema_version_is_current(self) -> bool:
        try:
            manifest_exists = self.con.execute(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'mt4ds'
                  AND table_name = 'prepare_manifest'
                LIMIT 1
                """
            ).fetchone()
            if not manifest_exists:
                return True
            row = self.con.execute(
                """
                SELECT value
                FROM mt4ds.prepare_manifest
                WHERE key = 'prepared_schema_version'
                """
            ).fetchone()
            if not row:
                return True
            from medterm4ds.engines.duckdb.prepared import PREPARED_SCHEMA_VERSION

            return str(row[0]) == PREPARED_SCHEMA_VERSION
        except Exception:
            return False

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

    @contextmanager
    def _temp_code_ancestors(self, code_ancestors: Sequence[tuple[str, str, int]]) -> Iterator[str]:
        table = f"_mt4ds_code_ancestors_{uuid4().hex}"
        self.con.execute(
            f"CREATE TEMP TABLE {table} (source_code VARCHAR, ancestor_code VARCHAR, depth INTEGER)"
        )
        try:
            self.con.executemany(
                f"INSERT INTO {table} VALUES (?, ?, ?)",
                [(str(code), str(ancestor), int(depth)) for code, ancestor, depth in code_ancestors],
            )
            yield table
        finally:
            self.con.execute(f"DROP TABLE IF EXISTS {table}")


def _dedupe(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values))


def _load_default_cvx_groups() -> dict[str, list[str]]:
    """Load CDC CVX vaccine groups on demand.

    Set MEDTERM4DS_DISABLE_CVX_GROUPS=1 to keep CVX resolution fully offline.
    MEDTERM4DS_CVX_GROUP_URL can point at a local test fixture or mirror.
    """
    global _CVX_GROUP_CACHE
    if os.environ.get("MEDTERM4DS_DISABLE_CVX_GROUPS"):
        return {}
    if _CVX_GROUP_CACHE is not None:
        return _CVX_GROUP_CACHE

    cache: dict[str, list[str]] = {}
    try:
        url = os.environ.get("MEDTERM4DS_CVX_GROUP_URL", _CVX_GROUP_URL)
        with urllib.request.urlopen(url, timeout=10) as response:
            text = response.read().decode("utf-8", errors="replace")
        for line in text.splitlines():
            parts = line.split("|")
            if len(parts) < 5:
                continue
            code = parts[1].strip()
            group = parts[3].strip()
            if code and group and group not in cache.setdefault(code, []):
                cache[code].append(group)
        for groups in cache.values():
            groups.sort()
    except Exception as exc:
        logger.debug("Failed to load CVX vaccine groups: %s", exc)

    _CVX_GROUP_CACHE = cache
    return cache


def _source_atom_order_sql(source: str) -> str:
    source = source.upper()
    if source == "SNOMEDCT_US":
        return """
            CASE upper(TTY)
                WHEN 'PT' THEN 0
                WHEN 'FN' THEN 1
                WHEN 'SY' THEN 2
                ELSE 3
            END,
            LENGTH(STR),
            AUI
        """
    if source in {"ICD10CM", "ICD10PCS"}:
        return """
            CASE upper(TTY)
                WHEN 'PT' THEN 0
                WHEN 'HT' THEN 1
                WHEN 'AB' THEN 2
                WHEN 'ET' THEN 3
                ELSE 4
            END,
            LENGTH(STR),
            AUI
        """
    if source == "CPT":
        return """
            CASE upper(TTY)
                WHEN 'ETCF' THEN 0
                WHEN 'ETCLIN' THEN 1
                WHEN 'PT' THEN 2
                WHEN 'SY' THEN 3
                ELSE 4
            END,
            CASE upper(TTY)
                WHEN 'SY' THEN LENGTH(STR)
                ELSE 0
            END,
            LENGTH(STR),
            AUI
        """
    if source == "CVX":
        return """
            CASE upper(TTY)
                WHEN 'PT' THEN 0
                WHEN 'SY' THEN 1
                WHEN 'AB' THEN 2
                ELSE 3
            END,
            LENGTH(STR),
            AUI
        """
    return "AUI"


def _source_hierarchy_atom_order_sql(source: str) -> str:
    source = source.upper()
    if source == "CPT":
        return """
            CASE upper(TTY)
                WHEN 'PT' THEN 0
                WHEN 'HT' THEN 1
                WHEN 'ETCLIN' THEN 2
                WHEN 'ETCF' THEN 3
                WHEN 'SY' THEN 4
                ELSE 5
            END,
            AUI
        """
    return _source_atom_order_sql(source)


def _source_technical_atom_order_sql(source: str) -> str:
    source = source.upper()
    if source == "SNOMEDCT_US":
        return """
            CASE upper(TTY)
                WHEN 'FN' THEN 0
                WHEN 'PT' THEN 1
                WHEN 'SY' THEN 2
                ELSE 3
            END,
            LENGTH(STR),
            AUI
        """
    return _source_atom_order_sql(source)


def _rxnorm_base_tty_order_sql(alias: str = "c") -> str:
    tty_expr = f"upper({alias}.TTY)"
    cases = " ".join(
        f"WHEN '{tty}' THEN {priority}"
        for tty, priority in _RXNORM_BASE_TTY_PRIORITY.items()
    )
    return f"CASE {tty_expr} {cases} ELSE 99 END"


def _rxnorm_tty_sql_rows() -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    """Build static rows for RxNorm TTY topology targets and path steps."""
    candidate_rows: list[tuple[object, ...]] = []
    path_step_rows: list[tuple[object, ...]] = []
    for start_tty in sorted(_RXNORM_KNOWN_TTYS):
        target_specs: list[tuple[str, int, str]] = []
        if start_tty in _RXNORM_GROUP_TTYS:
            target_specs.append(("SCDG", 0, "group"))
        # Patient-friendly RxNorm uses topology targets, not MEDLINEPLUS/CHV
        # and not generic isa traversal. MIN and IN stay themselves. PIN and
        # SCDC try IN first, then MIN. Other TTYs try MIN, then IN.
        if start_tty in {"IN", "MIN"}:
            ingredient_targets = (start_tty,)
        elif start_tty in {"PIN", "SCDC"}:
            ingredient_targets = ("IN", "MIN")
        else:
            ingredient_targets = ("MIN", "IN")
        target_specs.extend(
            (target_tty, target_order, "ingredient")
            for target_order, target_tty in enumerate(ingredient_targets, 1)
        )
        for target_tty, target_order, match_type in target_specs:
            path = _rxnorm_find_tty_path(start_tty, target_tty)
            if not path:
                continue
            path_depth = len(path) - 1
            candidate_rows.append((start_tty, target_tty, target_order, match_type, path_depth))
            for step, step_tty in enumerate(path[1:], 1):
                path_step_rows.append((start_tty, target_tty, step, step_tty, path_depth))
    return candidate_rows, path_step_rows


def _sql_values(rows: Sequence[Sequence[object]]) -> str:
    if not rows:
        raise ValueError("rows must not be empty")
    return ",\n                           ".join(
        "(" + ", ".join(_sql_literal(value) for value in row) + ")"
        for row in rows
    )


def _sql_literal(value: object) -> str:
    if isinstance(value, int):
        return str(value)
    text = str(value).replace("'", "''")
    return f"'{text}'"


def _chunks(values: Sequence[T], size: int) -> Iterator[list[T]]:
    for start in range(0, len(values), size):
        yield list(values[start:start + size])


def _ndc_candidates(code: str) -> list[str]:
    raw = str(code).strip()
    if not raw:
        return []
    if "-" in raw:
        parts = raw.split("-")
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            return []
        labeler, product, package = parts
        if (len(labeler), len(product), len(package)) == (4, 4, 2):
            return [f"0{labeler}{product}{package}"]
        if (len(labeler), len(product), len(package)) == (5, 3, 2):
            return [f"{labeler}0{product}{package}"]
        if (len(labeler), len(product), len(package)) == (5, 4, 1):
            return [f"{labeler}{product}0{package}"]
        if (len(labeler), len(product), len(package)) == (5, 4, 2):
            return [f"{labeler}{product}{package}"]
        return []
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11:
        return [digits]
    if len(digits) == 10:
        return _dedupe([
            f"0{digits[0:4]}{digits[4:8]}{digits[8:10]}",
            f"{digits[0:5]}0{digits[5:8]}{digits[8:10]}",
            f"{digits[0:5]}{digits[5:9]}0{digits[9:10]}",
        ])
    return []


def _relationship_values(relationship: str) -> list[str]:
    value = str(relationship or "isa")
    if value.upper() == "PAR" or value.lower() == "isa":
        return ["isa", "PAR"]
    return [value]


def _is_isa_relationship(relationship: str | None) -> bool:
    value = str(relationship or "isa")
    return value.lower() == "isa" or value.upper() == "PAR"


def _source_hierarchy_family(source: str) -> str:
    source = source.upper()
    if source in _PAR_HIERARCHY_SOURCES:
        return "par"
    if source in _RELA_ISA_HIERARCHY_SOURCES:
        return "rela_isa"
    return "generic"


def _source_hierarchy_join_sql(
    source: str,
    current_aui_expr: str,
    *,
    rel_alias: str = "r",
    upward: bool,
) -> tuple[str, str]:
    """Return an MRREL join predicate and target AUI expression for source hierarchy."""
    family = _source_hierarchy_family(source)
    if family == "par":
        if upward:
            return (
                f"(({rel_alias}.AUI1 = {current_aui_expr} AND {rel_alias}.REL = 'PAR') "
                f"OR ({rel_alias}.AUI2 = {current_aui_expr} AND {rel_alias}.REL = 'CHD'))",
                f"CASE WHEN {rel_alias}.AUI1 = {current_aui_expr} "
                f"THEN {rel_alias}.AUI2 ELSE {rel_alias}.AUI1 END",
            )
        return (
            f"(({rel_alias}.AUI2 = {current_aui_expr} AND {rel_alias}.REL = 'PAR') "
            f"OR ({rel_alias}.AUI1 = {current_aui_expr} AND {rel_alias}.REL = 'CHD'))",
            f"CASE WHEN {rel_alias}.AUI2 = {current_aui_expr} "
            f"THEN {rel_alias}.AUI1 ELSE {rel_alias}.AUI2 END",
        )
    if family == "rela_isa":
        if upward:
            return (
                f"{rel_alias}.AUI1 = {current_aui_expr} AND {rel_alias}.RELA = 'isa'",
                f"{rel_alias}.AUI2",
            )
        return (
            f"{rel_alias}.AUI2 = {current_aui_expr} AND {rel_alias}.RELA = 'isa'",
            f"{rel_alias}.AUI1",
        )
    if upward:
        return (
            f"(({rel_alias}.AUI1 = {current_aui_expr} "
            f"AND ({rel_alias}.RELA = 'isa' OR {rel_alias}.REL = 'PAR')) "
            f"OR ({rel_alias}.AUI2 = {current_aui_expr} AND {rel_alias}.REL = 'CHD'))",
            f"CASE WHEN {rel_alias}.AUI1 = {current_aui_expr} "
            f"THEN {rel_alias}.AUI2 ELSE {rel_alias}.AUI1 END",
        )
    return (
        f"(({rel_alias}.AUI2 = {current_aui_expr} "
        f"AND ({rel_alias}.RELA = 'isa' OR {rel_alias}.REL = 'PAR')) "
        f"OR ({rel_alias}.AUI1 = {current_aui_expr} AND {rel_alias}.REL = 'CHD'))",
        f"CASE WHEN {rel_alias}.AUI2 = {current_aui_expr} "
        f"THEN {rel_alias}.AUI1 ELSE {rel_alias}.AUI2 END",
    )


def _dedupe_relation_rows(rows: Sequence[tuple[int, CodeRelation]]) -> list[tuple[int, CodeRelation]]:
    deduped: dict[tuple[int, str], tuple[int, CodeRelation]] = {}
    for ordinal, relation in rows:
        key = (int(ordinal), relation.target.code)
        score = (relation.depth, relation.target_aui or "")
        current = deduped.get(key)
        if current is None:
            deduped[key] = (int(ordinal), relation)
            continue
        current_score = (
            current[1].depth,
            current[1].target_aui or "",
        )
        if score < current_score:
            deduped[key] = (int(ordinal), relation)
    return list(deduped.values())


def _cap_mappings_per_input(
    rows: Sequence[tuple[int, CodeMapping]],
    max_results_per_code: int,
) -> list[CodeMapping]:
    counts: dict[int, int] = defaultdict(int)
    output: list[CodeMapping] = []
    for ordinal, mapping in sorted(
        rows,
        key=lambda item: (
            item[0],
            item[1].match_depth,
            item[1].match_type,
            item[1].target.source,
            item[1].target.code,
            item[1].target_aui or "",
        ),
    ):
        if counts[int(ordinal)] >= max_results_per_code:
            continue
        counts[int(ordinal)] += 1
        output.append(mapping)
    return output


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


# Backward-compatible alias for pre-0.0.1 naming.
LocalLiteEngine = LocalDuckDBEngine
