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


@dataclass(frozen=True)
class _ReplacementCandidate:
    code: CodeRef
    name: str | None
    cui: str | None
    aui: str | None
    tty: str | None
    suppress: str | None
    relationship: str | None


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


    def _resolve_code(self, ref: CodeRef) -> CodeResolution:
        if ref.source == "NDC":
            return self._resolve_ndc(ref)

        active = self.get_code_infos([ref])[0]
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

        historical = self._lookup_any_code(ref)
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

        replacements = self._replacement_candidates(historical)
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

    def _active_source_code_set(self, source: str) -> set[str]:
        cached = self._active_source_code_cache.get(source)
        if cached is not None:
            return cached
        rows = self.con.execute(
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
        self._active_source_code_cache[source] = active_codes
        return active_codes

    def _resolve_ndc(self, ref: CodeRef) -> CodeResolution:
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
        if self._table_exists("mrsat"):
            placeholders = ",".join(["?"] * len(candidates))
            rows = self.con.execute(
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

    def _lookup_any_code(self, ref: CodeRef) -> CodeInfo | None:
        if self._table_exists("atoms"):
            rows = self.con.execute(
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

        rows = self.con.execute(
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

    def _replacement_candidates(self, historical: CodeInfo) -> list[_ReplacementCandidate]:
        if self._table_exists("code_replacements") and self._table_exists("best_atoms"):
            rows = self.con.execute(
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
        rows = self.con.execute(
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

    def _resolve_default(
        self,
        codes: Sequence[str],
        source: str,
        max_depth: int,
        *,
        filter_broad: bool = False,
    ) -> list[_Row]:
        atom_order_sql = _source_atom_order_sql(source)
        hierarchy_atom_order_sql = _source_hierarchy_atom_order_sql(source)
        hierarchy_join, hierarchy_target = _source_hierarchy_join_sql(
            source,
            "w.AUI",
            upward=True,
        )
        with self._temp_codes(codes) as temp:
            rows = self.con.execute(
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
                by_code[code] = self._make_original(
                    code,
                    source,
                    technical_name=orig_name,
                    display_name=orig_name,
                )

        return [by_code.get(code) or self._make_original(code, source) for code in codes]

    def _apply_snomed_fallback(
        self,
        source: str,
        rows: list[_Row],
        max_depth: int,
    ) -> None:
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
        replacements = self._resolve_default_via_snomed(fallback_codes, source, max_depth)
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
        self,
        codes: Sequence[str],
        source: str,
        max_depth: int,
    ) -> dict[str, _Row]:
        if not codes:
            return {}
        codes = _dedupe(codes)
        effective_chunk_size = min(self.query_chunk_size, _SNOMED_FALLBACK_QUERY_CHUNK_SIZE)
        if len(codes) > effective_chunk_size:
            result: dict[str, _Row] = {}
            chunks = list(_chunks(codes, effective_chunk_size))
            for chunk_index, chunk in enumerate(chunks, 1):
                self._progress(
                    f"resolving {source} SNOMED fallback chunk {chunk_index}/{len(chunks)} "
                    f"({len(chunk)} codes)"
                )
                result.update(self._resolve_default_via_snomed(chunk, source, max_depth))
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

        if self._table_exists("snomed_top_level_depth"):
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

        with self._temp_codes(codes) as temp:
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
                rows = self.con.execute(query, [max_depth, source]).fetchall()
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
                rows = self.con.execute(query, params).fetchall()

        walk_code_index = 4
        depth_lookup = self._snomed_top_level_depths([row[walk_code_index] for row in rows if row[walk_code_index]])

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


    def _resolve_rxnorm(self, codes: Sequence[str]) -> list[_Row]:
        if not codes:
            return []
        # RxNorm paths sometimes require walking through suppressed intermediate
        # AUIs (e.g. suppressed quantified-form / component nodes) before
        # reaching a target. Final selection prefers active candidates while
        # still allowing suppressed atoms to preserve recall.
        candidate_rows, path_step_rows = _rxnorm_tty_sql_rows()
        candidate_values = _sql_values(candidate_rows)
        path_step_values = _sql_values(path_step_rows)
        with self._temp_codes(codes) as temp:
            rows = self.con.execute(
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
                    matched_via=self._simple_provenance("rxnorm_tty", "RXNORM", code, orig_name),
                )

        return [by_code.get(code) or self._make_none(code, "RXNORM") for code in codes]

    def _resolve_loinc(self, codes: Sequence[str], max_depth: int) -> list[_Row]:
        if not codes:
            return []
        with self._temp_codes(codes) as temp:
            rows = self.con.execute(
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
            orig = self._technical_name(code, "LNC")
            if match_type == "first_axis":
                by_code[code] = _Row(
                    code=code,
                    source="LNC",
                    name=friendly_name,
                    friendly_source="LNC",
                    match_type=match_type,
                    match_depth=0,
                    technical_name=orig,
                    matched_via=self._simple_provenance(match_type, "LNC", code, friendly_name),
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
                    matched_via=self._simple_provenance("loinc_component", "LNC", code, friendly_name),
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
                    matched_via=self._simple_provenance("loinc_common", "LNC", code, friendly_name),
                )
            else:
                by_code[code] = self._make_original(code, "LNC", technical_name=orig)

        rows_out = [by_code.get(code) or self._make_original(code, "LNC") for code in codes]
        self._apply_snomed_fallback("LNC", rows_out, max_depth)
        return rows_out

    def _resolve_cpt(self, codes: Sequence[str], max_depth: int) -> list[_Row]:
        if not codes:
            return []
        display_order_sql = _source_atom_order_sql("CPT")
        hierarchy_join, hierarchy_target = _source_hierarchy_join_sql(
            "CPT",
            "w.AUI",
            upward=True,
        )
        with self._temp_codes(codes) as temp:
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
            rows = self.con.execute(query, [max_depth]).fetchall()

        by_code: dict[str, _Row] = {}
        for code, friendly_name, friendly_source, match_type, match_depth, _source in rows:
            by_code[code] = _Row(
                code=code,
                source="CPT",
                name=friendly_name,
                friendly_source=friendly_source,
                match_type=match_type,
                match_depth=int(match_depth or 0),
                technical_name=self._technical_name(code, "CPT"),
                matched_via=self._simple_provenance(match_type, "CPT", code, friendly_name),
            )

        fallback_rows = [row for row in by_code.values() if row.match_type == "original"]
        if not fallback_rows:
            return [by_code.get(code) or self._make_original(code, "CPT") for code in codes]

        fallback_codes = [row.code for row in fallback_rows]
        mapping = self._map_cpt_targets(fallback_codes)
        if not mapping:
            return [by_code.get(code) or self._make_original(code, "CPT") for code in codes]

        hcpcs_targets = sorted({target for (src, target) in mapping.values() if src == "HCPCS"})
        icd10_targets = sorted({target for (src, target) in mapping.values() if src == "ICD10CM"})
        snomed_targets = sorted({target for (src, target) in mapping.values() if src == "SNOMEDCT_US"})

        hcpcs_results = {
            row.code: row for row in self._resolve_default(hcpcs_targets, "HCPCS", max_depth)
        } if hcpcs_targets else {}
        icd10_results = {
            row.code: row for row in self._resolve_default(icd10_targets, "ICD10CM", max_depth)
        } if icd10_targets else {}
        snomed_results = (
            self._resolve_default_via_snomed(snomed_targets, "SNOMEDCT_US", max_depth)
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
            base.technical_name = self._technical_name(cpt_code, "CPT")
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

        return [by_code.get(code) or self._make_original(code, "CPT") for code in codes]

    def _map_cpt_targets(self, codes):
        return _mappings._map_cpt_targets(self, codes=codes)


    def _resolve_cvx(self, codes: Sequence[str]) -> list[_Row]:
        metadata: dict[str, list[tuple[str | None, str | None]]] = {}
        if codes:
            try:
                with self._temp_codes(codes) as temp:
                    rows = self.con.execute(
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
        if self._cvx_groups_auto and not self.cvx_groups and needs_external_groups:
            self.cvx_groups = _load_default_cvx_groups()
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
            groups = metadata_groups or self.cvx_groups.get(code)
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
                        technical_name=self._technical_name(code, "CVX"),
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
                rows.append(self._make_original(code, "CVX"))
        return rows

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
