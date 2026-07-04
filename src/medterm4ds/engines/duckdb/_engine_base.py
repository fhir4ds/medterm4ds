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
from typing import Any, TypeVar
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
# NOTE: `from . import hierarchy/mappings/...` is deferred to runtime (lazy
# import inside the functions that need them). Importing here causes a
# circular dependency: this module is loaded by engine.py via wildcard
# import, and the duckdb submodules (hierarchy.py, mappings.py, etc.)
# themselves import helpers from engine.py. Deferring the submodule imports
# breaks the cycle.
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
# Allowlist of hosts the engine will fetch CVX group data from. Anything else
# (set via MEDTERM4DS_CVX_GROUP_URL env override) is rejected as an SSRF guard.
_CVX_GROUP_HOST_ALLOWLIST = ("www2.cdc.gov", "www.cdc.gov", "cdc.gov")


def _is_safe_cvx_url(url: str) -> bool:
    """Validate that a CVX group URL is https and on the cdc.gov allowlist."""
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return host in _CVX_GROUP_HOST_ALLOWLIST
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


def _dedupe(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values))


def _load_default_cvx_groups() -> dict[str, list[str]]:
    """Load CDC CVX vaccine groups on demand.

    Set MEDTERM4DS_DISABLE_CVX_GROUPS=1 to keep CVX resolution fully offline.
    MEDTERM4DS_CVX_GROUP_URL can point at a local test fixture or mirror but
    MUST be https and MUST be on the cdc.gov allowlist (or the cdc.gov default
    URL itself). Anything else is rejected with a warning and the cache is
    left empty — this is an SSRF guard against attacker-controlled env vars
    that could otherwise redirect the runtime fetch to internal hosts (cloud
    metadata endpoints, internal services, etc.).
    """
    global _CVX_GROUP_CACHE
    if os.environ.get("MEDTERM4DS_DISABLE_CVX_GROUPS"):
        return {}
    if _CVX_GROUP_CACHE is not None:
        return _CVX_GROUP_CACHE

    url = os.environ.get("MEDTERM4DS_CVX_GROUP_URL") or _CVX_GROUP_URL
    if not _is_safe_cvx_url(url):
        # Don't fetch — leave cache empty rather than honor an SSRF vector.
        # Patient-friendly CVX lookups will fall back through the hierarchy.
        _CVX_GROUP_CACHE = {}
        return _CVX_GROUP_CACHE

    cache: dict[str, list[str]] = {}
    try:
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
    if source == "RXNORM":
        # Use the canonical RxNorm TTY priority (same as _rxnorm_base_tty_order_sql).
        # Without this case the function fell through to "AUI" (alphabetical),
        # which caused the CSV enrichment to pick random atoms with respect
        # to TTY -- surfacing SY/TMSY/PSN in the JSON for ~12,800 codes that
        # actually have SCD/SBD/SCDG/etc. available. See TTY-FIX, 2026-06-26.
        cases = " ".join(
            f"WHEN '{tty}' THEN {priority}"
            for tty, priority in _RXNORM_BASE_TTY_PRIORITY.items()
        )
        return f"""
            CASE upper(TTY) {cases} ELSE 99 END,
            LENGTH(STR),
            AUI
        """
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





# Backward-compatible alias for pre-0.0.1 naming. Defined lazily because
# LocalDuckDBEngine lives in engine.py (which imports from this module).
def __getattr__(name):
    if name == "LocalLiteEngine":
        from medterm4ds.engines.duckdb.engine import LocalDuckDBEngine
        return LocalDuckDBEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    'Any',
    'Callable',
    'CodeInfo',
    'CodeMapping',
    'CodeRef',
    'CodeRelation',
    'CodeResolution',
    'FriendlyNameResult',
    'Iterator',
    'LocalDuckDBConfig',
    'Mapping',
    'NameSearchResult',
    'OptimizeResult',
    'OptimizeRule',
    'Path',
    'Provenance',
    'ProvenanceStep',
    'Sequence',
    'SourceStats',
    'T',
    'TypeVar',
    '_BLACKLIST_LOINC',
    '_BROAD_CHV_NAMES',
    '_BROAD_CHV_NAME_SQL',
    '_BROAD_MEDLINEPLUS_NAMES',
    '_BROAD_MEDLINEPLUS_NAME_SQL',
    '_COMBO_SEP_HINTS',
    '_COMBO_TERM_STOPWORDS',
    '_CPT_TARGET_PRIORITY',
    '_CVX_GROUP_CACHE',
    '_CVX_GROUP_HOST_ALLOWLIST',
    '_CVX_GROUP_URL',
    '_DEFAULT_OPTIMIZE_REL',
    '_HIERARCHY_RELATIONSHIPS',
    '_PAR_HIERARCHY_SOURCES',
    '_RELA_ISA_HIERARCHY_SOURCES',
    '_REPLACEMENT_RELAS',
    '_RXNORM_BASE_TTY_PRIORITY',
    '_RXNORM_GROUP_TTYS',
    '_RXNORM_KNOWN_TTYS',
    '_Row',
    '_SNOMED_FALLBACK_QUERY_CHUNK_SIZE',
    '_SNOMED_FALLBACK_SOURCES',
    '_SNOMED_PARENT_LINKS_CACHE_TABLE',
    '_SNOMED_TARGET_PRIORITY',
    '_SNOMED_TARGET_SABS_LEGACY',
    '_SNOMED_TARGET_SABS_WITH_MGSTY',
    '_SNOMED_TOP_LEVEL_GUARD_DEPTH',
    '_SNOMED_TOP_LEVEL_GUARD_EXEMPT_MATCH_TYPES',
    '_SNOMED_TUI_TARGETS',
    '__getattr__',
    '_cap_mappings_per_input',
    '_chunks',
    '_dedupe',
    '_dedupe_relation_rows',
    '_has_mrsty_table',
    '_is_broad_friendly_name',
    '_is_combo_chv_mismatch',
    '_is_isa_relationship',
    '_is_safe_cvx_url',
    '_load_default_cvx_groups',
    '_ndc_candidates',
    '_normalize_term_tokens',
    '_relationship_values',
    '_rxnorm_base_tty_order_sql',
    '_rxnorm_find_tty_path',
    '_rxnorm_tty_sql_rows',
    '_snomed_tui_target_pairs_sql',
    '_source_atom_order_sql',
    '_source_hierarchy_atom_order_sql',
    '_source_hierarchy_family',
    '_source_hierarchy_join_sql',
    '_source_technical_atom_order_sql',
    '_sql_literal',
    '_sql_values',
    'contextmanager',
    'dataclass',
    'defaultdict',
    'logger',
    'logging',
    'os',
    're',
    'urllib',
    'uuid4',
]
