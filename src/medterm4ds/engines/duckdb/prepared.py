"""Prepared schema management for mt4ds terminology normalization.

Creates ``umls`` and ``mt4ds`` schemas in an existing DuckDB database,
establishes views over raw UMLS tables, maintains a manifest table
for provenance tracking, and builds prepared runtime tables from raw UMLS data.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import duckdb

from medterm4ds.sources import (
    BROAD_CHV_NAMES,
    BROAD_MEDLINEPLUS_NAMES,
    LOINC_CLASS_RELA,
    RELA_HIERARCHY_CHILD_SIDE,
    RELA_HIERARCHY_PARENT_SIDE,
    RXNORM_BASE_TTY_PRIORITY,
    RXNORM_TTY_TOPOLOGY,
    SOURCE_STRATEGIES,
    compute_tty_paths,
)

logger = logging.getLogger(__name__)

# QC-407 (MEDIUM): bumped 0.8 -> 0.9 because the sweep changed prepared-table
# BUILDER LOGIC without bumping (QC-016 best_atoms CPT PT-first ranking;
# QC-340/345/349/350 hierarchy_edges/walk_edges RELA vocabulary incl. RXNORM/
# MSH isa-family + LNC class_of; same_cui_edges is_active semantics). The
# production manifest still says 0.8, so with this bump `medterm4ds data
# verify`, verify_mt4ds_schema(), and the runtime
# _prepared_schema_version_is_current() gate all DETECT the stale tables and
# steer the operator to rebuild instead of silently serving stale answers
# (QC-404 walk_edges, QC-405 best_atoms, QC-412 same_cui_edges). ANY change
# to builder SQL below must bump this value.
PREPARED_SCHEMA_VERSION = "0.9"
PATIENT_FRIENDLY_POLICY_VERSION = "0.2"

_UMLS_TABLES = ("mrconso", "mrrel", "mrsat")
_REQUIRED_MT4DS_TABLES = (
    "atoms",
    "best_atoms",
    "hierarchy_edges",
    "walk_edges",
    "same_cui_edges",
    "crosswalk_edges",
    "friendly_atoms",
    "rxnorm_allowed_tty_edges",
    "rxnorm_tty_paths",
    "rxnorm_tty_path_steps",
    "rxnorm_tty_edges",
    "cvx_metadata",
    "code_replacements",
    "snomed_top_level_depth",
    "patient_friendly_strategy",
)


def _table_exists(con, schema: str, table: str) -> bool:
    """Return True if *schema.table* exists in the catalog."""
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = ? AND table_name = ?",
        [schema, table],
    ).fetchall()
    return len(rows) > 0


def _schema_exists(con, schema: str) -> bool:
    rows = con.execute(
        "SELECT schema_name FROM information_schema.schemata WHERE schema_name = ?",
        [schema],
    ).fetchall()
    return len(rows) > 0


def _row_count(con, qualified_table: str) -> int | None:
    """Return row count for a qualified table, or None if it does not exist.

    Narrow except to duckdb.Error so programming bugs (TypeError from a
    misused con, AttributeError on a closed connection) propagate instead
    of silently returning None — which callers would interpret as "table
    missing" and write NULL row counts into the manifest, indistinguishable
    from a legitimately-absent table.
    """
    try:
        (count,) = con.execute(f"SELECT COUNT(*) FROM {qualified_table}").fetchone()  # noqa: S608
        return int(count)
    except duckdb.Error as exc:
        # Real DuckDB failure (permissions, broken catalog, transient lock).
        # Log at WARNING so an operator running prepare sees why a row count
        # came back None rather than chasing a 'missing table' that exists.
        logging.getLogger(__name__).warning(
            "row count probe failed for %s: %s. Manifest will record NULL "
            "for this table; verify the table exists and is readable.",
            qualified_table,
            exc,
        )
        return None


def _detect_raw_location(con) -> dict[str, str]:
    """Return a mapping of table_name -> 'main' | 'umls' for each raw UMLS table."""
    locations: dict[str, str] = {}
    for table in _UMLS_TABLES:
        if _table_exists(con, "umls", table):
            locations[table] = "umls"
        elif _table_exists(con, "main", table):
            locations[table] = "main"
        else:
            locations[table] = ""
    return locations


def _raw_ref(table: str, locations: dict[str, str] | None = None, con=None) -> str:
    """Return qualified reference for a raw UMLS table (e.g., 'main."mrconso"').

    If locations is None and con is provided, detect automatically.
    Falls back to unqualified table name if neither source exists.
    """
    if locations is None and con is not None:
        locations = _detect_raw_location(con)
    loc = (locations or {}).get(table, "")
    catalog = _current_catalog(con) if con is not None else None
    prefix = f'"{catalog}".' if catalog else ""
    if loc == "umls":
        return f'{prefix}"umls"."{table}"'
    if loc == "main":
        return f'{prefix}"main"."{table}"'
    return f'{prefix}"main"."{table}"'


def _current_catalog(con) -> str:
    """Return the current DuckDB catalog (database) name, quoted for SQL.

    Falls back to 'memory' only for duckdb.Error — the canonical default
    catalog name when DuckDB is opened in-memory. Programming bugs
    (AttributeError, TypeError from a misused con) propagate so they surface
    instead of silently routing all subsequent SQL through a phantom catalog.
    """
    try:
        row = con.execute("SELECT current_database()").fetchone()
        if row and row[0]:
            return str(row[0]).replace('"', '""')
    except duckdb.Error as exc:
        # Real DuckDB failures (permissions, unsupported version, broken
        # connection) — log at WARNING so an operator running prepare on a
        # disk-backed DB with a catalog probe failure doesn't silently get
        # 'memory'.'umls'.'mrconso' queries that return zero rows.
        logging.getLogger(__name__).warning(
            "DuckDB catalog probe failed; falling back to 'memory' catalog. "
            "If this DB is disk-backed, subsequent qualified SQL will likely miss. "
            "Error: %s",
            exc,
        )
    return "memory"


# QC-459 (HIGH): views previously baked the build-time catalog (DB filename
# stem) into their DDL ('... FROM "valid"."main"."mrconso"'). DuckDB re-binds
# view bodies at query time, so any copy/rename of a built DB (deploy.sh
# copies, role-split names, backups) dangled the baked reference and every
# umls.* read failed with 'Binder Error: Catalog "valid" does not exist'.
# A three-part (catalog.schema.table) reference right after FROM is the
# fingerprint of a baked view; schema-qualified references (main.<table>)
# resolve against the CURRENT catalog and survive renames.
_BAKED_CATALOG_REF = re.compile(r"\bFROM\s+[^\s,;]+(?:\.[^\s,;]+){2,}")


def _umls_view_sql(con, table: str) -> str | None:
    """Return the stored SQL of umls.<table> if it is a view, else None."""
    rows = con.execute(
        "SELECT sql FROM duckdb_views() "
        "WHERE schema_name = 'umls' AND view_name = ? AND internal = false",
        [table],
    ).fetchall()
    return str(rows[0][0]) if rows else None


def _ensure_views(con, locations: dict[str, str]) -> list[str]:
    """Create stable ``umls`` schema views over raw ``main`` UMLS tables.

    If a raw table already exists in ``umls``, it is treated as authoritative
    and is not replaced. Builders still use ``_raw_ref`` so either layout works.

    Views reference ``main.<table>`` WITHOUT a catalog qualifier (QC-459) so
    the DB file stays relocatable; a pre-existing view whose SQL still bakes
    a catalog name (built by an older medterm4ds) is dropped and recreated.
    """
    views: list[str] = []
    for table in _UMLS_TABLES:
        if locations.get(table) != "main":
            continue
        existing_sql = _umls_view_sql(con, table)
        if existing_sql is not None and not _BAKED_CATALOG_REF.search(existing_sql):
            continue
        if existing_sql is not None:
            con.execute(f'DROP VIEW "{_current_catalog(con)}"."umls"."{table}"')
        # Three-part TARGET (unambiguous even when the DB filename stem equals
        # a schema name, e.g. 'umls.duckdb' -> catalog 'umls'), two-part BODY
        # (relocatable: 'main.<table>' re-binds against the current catalog
        # after any copy/rename).
        con.execute(
            f'CREATE VIEW "{_current_catalog(con)}"."umls"."{table}" AS '
            f'SELECT * FROM "main"."{table}"'
        )
        views.append(table)
    return views


def _upsert_manifest(con, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO mt4ds.prepare_manifest (key, value, updated_at) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        [key, value, datetime.now(timezone.utc).isoformat()],
    )


# ---------------------------------------------------------------------------
# Builder functions for prepared runtime tables
# ---------------------------------------------------------------------------

# Sources that use REL='PAR' AND RELA IS NULL for hierarchy
_PAR_SOURCES = frozenset({"ICD10CM", "ICD10PCS", "HCPCS", "LNC"})

# Sources that use the RELA isa/inverse_isa family (+ ATC has_member /
# member_of, LOINC-not) for hierarchy. RXNORM added by QC-349 (EC-15 HIGH):
# its 238,329 isa/inverse_isa edges were previously excluded because
# RxNormStrategy.hierarchy_edge_sql() returned None, leaving the prepared
# hierarchy empty for RxNorm while $subsumes was advertised.
_RELA_ISA_SOURCES = frozenset({"CPT", "ATC", "MSH", "RXNORM"})

_REPLACEMENT_RELAS = (
    "same_as",
    "replaced_by",
    "possibly_replaced_by",
    "mapped_to",
    "moved_to",
)


def _tty_priority_sql(priority: dict[str, int], *, default: int = 99) -> str:
    cases = " ".join(
        f"WHEN '{tty}' THEN {rank}"
        for tty, rank in sorted(priority.items(), key=lambda item: item[1])
    )
    return f"CASE upper(tty) {cases} ELSE {default} END"


def _best_atom_order_sql() -> str:
    rxnorm_tty_rank = _tty_priority_sql(RXNORM_BASE_TTY_PRIORITY)
    return f"""
              CASE
                WHEN source = 'RXNORM' THEN {rxnorm_tty_rank}
                WHEN source IN ('ICD10CM', 'ICD10PCS') THEN
                  CASE upper(tty)
                    WHEN 'PT' THEN 0
                    WHEN 'HT' THEN 1
                    WHEN 'AB' THEN 2
                    WHEN 'ET' THEN 3
                    ELSE 4
                  END
                WHEN source = 'SNOMEDCT_US' THEN
                  CASE upper(tty)
                    WHEN 'PT' THEN 0
                    WHEN 'SCD' THEN 1
                    WHEN 'FN' THEN 2
                    WHEN 'SY' THEN 3
                    ELSE 4
                  END
                WHEN source = 'CPT' THEN
                  CASE upper(tty)
                    -- PT (AMA's published preferred term) is rank 0; ETCF/ETCLIN
                    -- are CMS clinical-equivalent atoms added for billing, NOT
                    -- the canonical CPT display. Pre-QC-016 this ranked ETCF
                    -- above PT, returning the wrong CUI/display/tty for ~90% of
                    -- CPT codes and diverging from the legacy mrconso path
                    -- (which uses PT>MH>LN>else). Found by DATA_INTEGRITY lens.
                    WHEN 'PT' THEN 0
                    WHEN 'ETCF' THEN 1
                    WHEN 'ETCLIN' THEN 2
                    WHEN 'SY' THEN 3
                    ELSE 4
                  END
                WHEN source = 'CVX' THEN
                  CASE upper(tty)
                    WHEN 'PT' THEN 0
                    WHEN 'SY' THEN 1
                    WHEN 'AB' THEN 2
                    ELSE 3
                  END
                ELSE
                  CASE upper(tty)
                    WHEN 'PT' THEN 0
                    WHEN 'MH' THEN 1
                    WHEN 'LN' THEN 2
                    ELSE 3
                  END
              END,
              CASE
                WHEN source IN ('ICD10CM', 'ICD10PCS', 'SNOMEDCT_US', 'CPT', 'CVX') THEN LENGTH(name)
                ELSE 0
              END,
              aui
    """


def _prepare_atoms(con, *, replace: bool) -> dict[str, object]:
    """Build mt4ds.atoms -- normalized atom records from raw UMLS mrconso."""
    table = "atoms"
    qualified = f"mt4ds.{table}"
    if not replace and _table_exists(con, "mt4ds", table):
        return {table: {"status": "exists", "rows": _row_count(con, qualified)}}

    mrconso_ref = _raw_ref("mrconso", con=con)
    logger.info("Building %s", qualified)
    con.execute(f"DROP TABLE IF EXISTS {qualified}")
    con.execute(
        f"""
        CREATE TABLE {qualified} AS
        SELECT
          SAB AS source,
          CODE AS code,
          AUI AS aui,
          CUI AS cui,
          upper(TTY) AS tty,
          STR AS name,
          SUPPRESS AS suppress,
          CASE WHEN SUPPRESS = 'N' THEN true ELSE false END AS is_active
        FROM {mrconso_ref}
        WHERE CODE IS NOT NULL AND CODE != ''
          AND AUI IS NOT NULL AND AUI != ''
        """
    )
    for ddl in (
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_atoms_source_code ON {qualified}(source, code)",
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_atoms_aui ON {qualified}(aui)",
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_atoms_cui_source ON {qualified}(cui, source)",
    ):
        try:
            con.execute(ddl)
        except duckdb.Error as exc:
            logger.warning("Skipping index on %s: %s", qualified, exc)

    rows = _row_count(con, qualified)
    logger.info("Built %s: %s rows", qualified, rows)
    return {table: {"status": "created", "rows": rows}}


def _prepare_best_atoms(con, *, replace: bool) -> dict[str, object]:
    """Build mt4ds.best_atoms -- ranked atoms per source/code with display rank."""
    table = "best_atoms"
    qualified = f"mt4ds.{table}"
    if not replace and _table_exists(con, "mt4ds", table):
        return {table: {"status": "exists", "rows": _row_count(con, qualified)}}

    logger.info("Building %s", qualified)
    con.execute(f"DROP TABLE IF EXISTS {qualified}")
    atom_order_sql = _best_atom_order_sql()
    con.execute(
        f"""
        CREATE TABLE {qualified} AS
        SELECT
          source, code, aui, cui, tty, name, suppress, is_active,
          ROW_NUMBER() OVER (
            PARTITION BY source, code
            ORDER BY
              CASE WHEN suppress = 'N' THEN 0 ELSE 1 END,
              {atom_order_sql}
          ) AS rank
        FROM mt4ds.atoms
        """
    )
    try:
        con.execute(
            f"CREATE INDEX IF NOT EXISTS idx_mt4ds_best_atoms_source_code_rank "
            f"ON {qualified}(source, code, rank)"
        )
    except duckdb.Error as exc:
        logger.warning("Skipping index on %s: %s", qualified, exc)

    rows = _row_count(con, qualified)
    logger.info("Built %s: %s rows", qualified, rows)
    return {table: {"status": "created", "rows": rows}}


def _prepare_hierarchy_edges(con, *, replace: bool) -> dict[str, object]:
    """Build mt4ds.hierarchy_edges -- normalized child-to-parent edges from mrrel."""
    table = "hierarchy_edges"
    qualified = f"mt4ds.{table}"
    if not replace and _table_exists(con, "mt4ds", table):
        return {table: {"status": "exists", "rows": _row_count(con, qualified)}}

    logger.info("Building %s", qualified)
    con.execute(f"DROP TABLE IF EXISTS {qualified}")

    # Collect hierarchy edge SQL from each registered source strategy
    par_sources = []
    rela_isa_sources = []
    snomed_sql = None
    for source_name, strategy in SOURCE_STRATEGIES.items():
        edge_sql = strategy.hierarchy_edge_sql()
        if edge_sql is None:
            continue
        # SNOMED has its own dedicated SQL pattern
        if source_name == "SNOMEDCT_US":
            snomed_sql = edge_sql
        elif source_name in _PAR_SOURCES:
            par_sources.append(source_name)
        elif source_name in _RELA_ISA_SOURCES:
            rela_isa_sources.append(source_name)

    mrrel_ref = _raw_ref("mrrel", con=con)

    # Check if mrrel is available
    has_mrrel = _table_exists(con, "umls", "mrrel") or _table_exists(con, "main", "mrrel")
    if not has_mrrel:
        con.execute(
            f"CREATE TABLE {qualified} ("
            "source VARCHAR, from_code VARCHAR, from_aui VARCHAR, from_cui VARCHAR, "
            "from_tty VARCHAR, to_code VARCHAR, to_aui VARCHAR, to_cui VARCHAR, "
            "to_tty VARCHAR, relationship VARCHAR, direction VARCHAR, edge_source VARCHAR)"
        )
        rows = 0
        logger.info("Built %s (empty, no mrrel): %s rows", qualified, rows)
        return {table: {"status": "created", "rows": rows}}

    # Build UNION of edge selects for each source group
    union_parts: list[str] = []

    # SNOMED: PAR with COALESCE RELA filter
    if snomed_sql:
        union_parts.append(
            f"""
            SELECT DISTINCT
              child.source,
              child.code AS from_code, child.aui AS from_aui,
              child.cui AS from_cui, child.tty AS from_tty,
              parent.code AS to_code, parent.aui AS to_aui,
              parent.cui AS to_cui, parent.tty AS to_tty,
              'isa' AS relationship, 'parent' AS direction,
              'umls_mrrel' AS edge_source
            FROM {mrrel_ref} r
            JOIN mt4ds.atoms child ON child.aui = r.AUI1
            JOIN mt4ds.atoms parent ON parent.aui = r.AUI2
            WHERE {snomed_sql}
              AND child.source = 'SNOMEDCT_US'
              AND parent.source = 'SNOMEDCT_US'
              AND child.code != parent.code
            """
        )
        # SNOMED CHD reversal
        union_parts.append(
            f"""
            SELECT DISTINCT
              child.source,
              child.code AS from_code, child.aui AS from_aui,
              child.cui AS from_cui, child.tty AS from_tty,
              parent.code AS to_code, parent.aui AS to_aui,
              parent.cui AS to_cui, parent.tty AS to_tty,
              'isa' AS relationship, 'parent' AS direction,
              'umls_mrrel' AS edge_source
            FROM {mrrel_ref} r
            JOIN mt4ds.atoms parent ON parent.aui = r.AUI1
            JOIN mt4ds.atoms child ON child.aui = r.AUI2
            WHERE r.REL = 'CHD'
              AND COALESCE(r.RELA, 'isa') IN ('isa', 'inverse_isa')
              AND child.source = 'SNOMEDCT_US'
              AND parent.source = 'SNOMEDCT_US'
              AND child.code != parent.code
            """
        )

    # PAR sources (ICD10CM, ICD10PCS, HCPCS, LNC): REL='PAR' AND RELA IS NULL
    if par_sources:
        sab_list = ", ".join(f"'{s}'" for s in sorted(par_sources))
        union_parts.append(
            f"""
            SELECT DISTINCT
              child.source,
              child.code AS from_code, child.aui AS from_aui,
              child.cui AS from_cui, child.tty AS from_tty,
              parent.code AS to_code, parent.aui AS to_aui,
              parent.cui AS to_cui, parent.tty AS to_tty,
              'isa' AS relationship, 'parent' AS direction,
              'umls_mrrel' AS edge_source
            FROM {mrrel_ref} r
            JOIN mt4ds.atoms child ON child.aui = r.AUI1
            JOIN mt4ds.atoms parent ON parent.aui = r.AUI2
            WHERE r.REL = 'PAR' AND r.RELA IS NULL
              AND child.source IN ({sab_list})
              AND parent.source = child.source
              AND child.code != parent.code
            """
        )
        # PAR CHD reversal
        union_parts.append(
            f"""
            SELECT DISTINCT
              child.source,
              child.code AS from_code, child.aui AS from_aui,
              child.cui AS from_cui, child.tty AS from_tty,
              parent.code AS to_code, parent.aui AS to_aui,
              parent.cui AS to_cui, parent.tty AS to_tty,
              'isa' AS relationship, 'parent' AS direction,
              'umls_mrrel' AS edge_source
            FROM {mrrel_ref} r
            JOIN mt4ds.atoms parent ON parent.aui = r.AUI1
            JOIN mt4ds.atoms child ON child.aui = r.AUI2
            WHERE r.REL = 'CHD' AND r.RELA IS NULL
              AND child.source IN ({sab_list})
              AND parent.source = child.source
              AND child.code != parent.code
            """
        )
        # LOINC multiaxial hierarchy (found by QC-350, EC-15 HIGH): REL='RO'
        # RELA='class_of' rows (284,774 in UMLS 2026AA) connect every lab code
        # to its multiaxial classes (Laboratory/Chemistry/Hematology/...).
        # These rows store the code (child) in AUI1 and the class (parent) in
        # AUI2; they are NOT mirrored in UMLS, so a single SELECT suffices.
        # RELA vocabulary is canonical in sources.base (LOINC_CLASS_RELA).
        union_parts.append(
            f"""
            SELECT DISTINCT
              child.source,
              child.code AS from_code, child.aui AS from_aui,
              child.cui AS from_cui, child.tty AS from_tty,
              parent.code AS to_code, parent.aui AS to_aui,
              parent.cui AS to_cui, parent.tty AS to_tty,
              'isa' AS relationship, 'parent' AS direction,
              'umls_mrrel' AS edge_source
            FROM {mrrel_ref} r
            JOIN mt4ds.atoms child ON child.aui = r.AUI1
            JOIN mt4ds.atoms parent ON parent.aui = r.AUI2
            WHERE r.REL = 'RO' AND r.RELA = '{LOINC_CLASS_RELA}'
              AND child.source IN ({sab_list})
              AND parent.source = child.source
              AND child.code != parent.code
            """
        )

    # RELA isa-family sources (CPT, ATC, MSH, RXNORM). Direction must respect
    # REL, never RELA (found by QC-340/345/349, EC-15): UMLS mirrors every
    # hierarchy edge twice. PAR/RB rows (RELA inverse_isa, has_member) store
    # child in AUI1 and parent in AUI2; CHD/RN rows (RELA isa, member_of)
    # store parent in AUI1 and child in AUI2. Vocabulary is canonical in
    # sources.base (RELA_HIERARCHY_PARENT_SIDE / RELA_HIERARCHY_CHILD_SIDE).
    if rela_isa_sources:
        sab_list = ", ".join(f"'{s}'" for s in sorted(rela_isa_sources))
        parent_side_relas = ", ".join(f"'{v}'" for v in RELA_HIERARCHY_PARENT_SIDE)
        child_side_relas = ", ".join(f"'{v}'" for v in RELA_HIERARCHY_CHILD_SIDE)
        union_parts.append(
            f"""
            SELECT DISTINCT
              child.source,
              child.code AS from_code, child.aui AS from_aui,
              child.cui AS from_cui, child.tty AS from_tty,
              parent.code AS to_code, parent.aui AS to_aui,
              parent.cui AS to_cui, parent.tty AS to_tty,
              'isa' AS relationship, 'parent' AS direction,
              'umls_mrrel' AS edge_source
            FROM {mrrel_ref} r
            JOIN mt4ds.atoms child ON child.aui = r.AUI1
            JOIN mt4ds.atoms parent ON parent.aui = r.AUI2
            WHERE r.REL IN ('PAR', 'RB')
              AND r.RELA IN ({parent_side_relas})
              AND child.source IN ({sab_list})
              AND parent.source = child.source
              AND child.code != parent.code
            """
        )
        union_parts.append(
            f"""
            SELECT DISTINCT
              child.source,
              child.code AS from_code, child.aui AS from_aui,
              child.cui AS from_cui, child.tty AS from_tty,
              parent.code AS to_code, parent.aui AS to_aui,
              parent.cui AS to_cui, parent.tty AS to_tty,
              'isa' AS relationship, 'parent' AS direction,
              'umls_mrrel' AS edge_source
            FROM {mrrel_ref} r
            JOIN mt4ds.atoms parent ON parent.aui = r.AUI1
            JOIN mt4ds.atoms child ON child.aui = r.AUI2
            WHERE r.REL IN ('CHD', 'RN')
              AND r.RELA IN ({child_side_relas})
              AND child.source IN ({sab_list})
              AND parent.source = child.source
              AND child.code != parent.code
            """
        )

    if union_parts:
        full_sql = " UNION ALL ".join(union_parts)
        con.execute(f"CREATE TABLE {qualified} AS {full_sql}")
    else:
        con.execute(
            f"CREATE TABLE {qualified} ("
            "source VARCHAR, from_code VARCHAR, from_aui VARCHAR, from_cui VARCHAR, "
            "from_tty VARCHAR, to_code VARCHAR, to_aui VARCHAR, to_cui VARCHAR, "
            "to_tty VARCHAR, relationship VARCHAR, direction VARCHAR, edge_source VARCHAR)"
        )

    for ddl in (
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_hier_from_aui_dir ON {qualified}(source, from_aui, direction)",
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_hier_to_aui ON {qualified}(source, to_aui)",
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_hier_from_code ON {qualified}(source, from_code)",
    ):
        try:
            con.execute(ddl)
        except duckdb.Error as exc:
            logger.warning("Skipping index on %s: %s", qualified, exc)

    rows = _row_count(con, qualified)
    logger.info("Built %s: %s rows", qualified, rows)
    return {table: {"status": "created", "rows": rows}}


def _prepare_walk_edges(con, *, replace: bool) -> dict[str, object]:
    """Build mt4ds.walk_edges -- same as hierarchy_edges (extended later with RxNorm TTY edges)."""
    table = "walk_edges"
    qualified = f"mt4ds.{table}"
    if not replace and _table_exists(con, "mt4ds", table):
        return {table: {"status": "exists", "rows": _row_count(con, qualified)}}

    logger.info("Building %s", qualified)
    con.execute(f"DROP TABLE IF EXISTS {qualified}")
    con.execute(
        f"""
        CREATE TABLE {qualified} AS
        SELECT * FROM mt4ds.hierarchy_edges
        """
    )
    for ddl in (
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_walk_from_aui_dir ON {qualified}(source, from_aui, direction)",
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_walk_to_aui ON {qualified}(source, to_aui)",
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_walk_from_code ON {qualified}(source, from_code)",
    ):
        try:
            con.execute(ddl)
        except duckdb.Error as exc:
            logger.warning("Skipping index on %s: %s", qualified, exc)

    rows = _row_count(con, qualified)
    logger.info("Built %s: %s rows", qualified, rows)
    return {table: {"status": "created", "rows": rows}}


def _walk_closure_seed_sources() -> list[str]:
    """CR-031 (HIGH): derive the closure seed whitelist from SOURCE_STRATEGIES.

    Any strategy whose ``hierarchy_edge_sql()`` is non-None contributes parent
    edges to mt4ds.walk_edges, so the closure must seed from the same set. The
    previous hardcoded list ('ICD10CM','ICD10PCS','HCPCS','CPT','LNC',
    'SNOMEDCT_US') excluded RXNORM/ATC/MSH, so the closure table stayed empty
    for them and every closure-accelerated ancestor walk silently returned []
    for those sources (crosswalk_prepared._broader_mappings, services.walk
    prepared paths, patient_friendly_prepared) even after walk_edges gained
    their edges. The runtime side (prepared_primitives.walk_closure_table
    per-source coverage probe) back-stops DBs built before this fix.

    No PREPARED_SCHEMA_VERSION bump: the 0.9 rebuild in flight at fix time
    predates this builder change, and the runtime per-source probe makes the
    closure delta a performance (not correctness) skew — a version bump would
    hard-disable the prepared paths on that rebuild for no correctness gain.
    """
    return sorted(
        name
        for name, strategy in SOURCE_STRATEGIES.items()
        if strategy.hierarchy_edge_sql() is not None
    )


def _prepare_walk_closure_limited(
    con,
    *,
    replace: bool,
    max_depth: int = 5,
) -> dict[str, object]:
    """Build mt4ds.walk_closure_limited -- bounded UMLS-only parent walk closure.

    This is an acceleration table for the existing parent-walk resolver. It is
    derived only from mt4ds.walk_edges and does not introduce synthetic edges.
    """
    table = "walk_closure_limited"
    qualified = f"mt4ds.{table}"
    if not replace and _table_exists(con, "mt4ds", table):
        return {table: {"status": "exists", "rows": _row_count(con, qualified)}}

    depth = max(1, int(max_depth))
    seed_sources = _walk_closure_seed_sources()
    seed_list = ", ".join(f"'{source}'" for source in seed_sources) or "''"
    logger.info("Building %s to depth %s (seeds: %s)", qualified, depth, ", ".join(seed_sources))
    con.execute(f"DROP TABLE IF EXISTS {qualified}")
    con.execute(
        f"""
        CREATE TABLE {qualified} AS
        WITH RECURSIVE closure(
            source,
            from_code,
            from_aui,
            from_cui,
            from_tty,
            to_code,
            to_aui,
            to_cui,
            to_tty,
            depth
        ) AS (
            SELECT DISTINCT
              source,
              from_code,
              from_aui,
              from_cui,
              from_tty,
              to_code,
              to_aui,
              to_cui,
              to_tty,
              1 AS depth
            FROM mt4ds.walk_edges
            WHERE direction = 'parent'
              AND source IN ({seed_list})

            UNION

            SELECT
              c.source,
              c.from_code,
              c.from_aui,
              c.from_cui,
              c.from_tty,
              e.to_code,
              e.to_aui,
              e.to_cui,
              e.to_tty,
              c.depth + 1 AS depth
            FROM closure c
            JOIN mt4ds.walk_edges e
              ON e.source = c.source
             AND e.from_aui = c.to_aui
             AND e.direction = 'parent'
            WHERE c.depth < {depth}
        )
        SELECT
          source,
          from_code,
          from_aui,
          from_cui,
          from_tty,
          to_code,
          to_aui,
          to_cui,
          to_tty,
          MIN(depth) AS depth
        FROM closure
        GROUP BY
          source,
          from_code,
          from_aui,
          from_cui,
          from_tty,
          to_code,
          to_aui,
          to_cui,
          to_tty
        """
    )
    for ddl in (
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_walk_closure_from ON {qualified}(source, from_aui, depth)",
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_walk_closure_to ON {qualified}(source, to_aui)",
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_walk_closure_code ON {qualified}(source, from_code)",
    ):
        try:
            con.execute(ddl)
        except duckdb.Error as exc:
            logger.warning("Skipping index on %s: %s", qualified, exc)

    rows = _row_count(con, qualified)
    logger.info("Built %s: %s rows", qualified, rows)
    return {table: {"status": "created", "rows": rows, "max_depth": depth}}


def _prepare_same_cui_edges(con, *, replace: bool) -> dict[str, object]:
    """Build mt4ds.same_cui_edges -- cross-source CUI links between active atoms.

    Uses a temp table of multi-source CUIs to avoid an O(n^2) self-join
    over the full atoms table.
    """
    table = "same_cui_edges"
    qualified = f"mt4ds.{table}"
    if not replace and _table_exists(con, "mt4ds", table):
        return {table: {"status": "exists", "rows": _row_count(con, qualified)}}

    logger.info("Building %s", qualified)
    con.execute(f"DROP TABLE IF EXISTS {qualified}")

    # Step 1: find CUIs that appear in more than one source (any active atom).
    # Note: we intentionally do NOT filter on rank=1 here. A source/code may have
    # multiple atoms across multiple CUIs (different meanings); the rank=1 atom
    # is the preferred DISPLAY, but other active atoms carry valid cross-source
    # CUI links. Filtering rank=1 silently drops same-CUI mappings for codes
    # whose preferred-display atom points to a single-source CUI while a
    # non-preferred atom points to a multi-source CUI (QC-041/QC-043).
    con.execute(
        "CREATE TEMP TABLE IF NOT EXISTS _mt4ds_multi_cui AS "
        "SELECT cui FROM mt4ds.best_atoms "
        "WHERE is_active "
        "GROUP BY cui HAVING COUNT(DISTINCT source) > 1"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx__mt4ds_multi_cui ON _mt4ds_multi_cui(cui)"
    )

    # Step 2: join active atoms across sources sharing a multi-source CUI.
    # SELECT DISTINCT deduplicates edges that arise from multiple atoms per
    # (source, code, cui) within the same source.
    con.execute(
        f"""
        CREATE TABLE {qualified} AS
        SELECT DISTINCT
          a1.source, a1.code, a1.cui,
          a2.source AS target_source, a2.code AS target_code,
          a2.aui AS target_aui, a2.cui AS target_cui,
          a2.tty AS target_tty
        FROM _mt4ds_multi_cui mc
        JOIN mt4ds.best_atoms a1 ON a1.cui = mc.cui AND a1.is_active
        JOIN mt4ds.best_atoms a2 ON a2.cui = mc.cui AND a2.is_active AND a2.source != a1.source
        """
    )
    con.execute("DROP TABLE IF EXISTS _mt4ds_multi_cui")
    for ddl in (
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_samecui_source_code ON {qualified}(source, code)",
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_samecui_target ON {qualified}(target_source, target_code)",
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_samecui_cui ON {qualified}(cui)",
    ):
        try:
            con.execute(ddl)
        except duckdb.Error as exc:
            logger.warning("Skipping index on %s: %s", qualified, exc)

    rows = _row_count(con, qualified)
    logger.info("Built %s: %s rows", qualified, rows)
    return {table: {"status": "created", "rows": rows}}


def _prepare_crosswalk_edges(con, *, replace: bool) -> dict[str, object]:
    """Build mt4ds.crosswalk_edges -- ranked reusable cross-source mappings.

    The first materialized layer contains exact same-CUI crosswalk rows. Broader
    and narrower hierarchy-assisted crosswalk rows can be added later using the
    same shape without changing runtime consumers.
    """
    table = "crosswalk_edges"
    qualified = f"mt4ds.{table}"
    if not replace and _table_exists(con, "mt4ds", table):
        return {table: {"status": "exists", "rows": _row_count(con, qualified)}}

    logger.info("Building %s", qualified)
    con.execute(f"DROP TABLE IF EXISTS {qualified}")
    con.execute(
        f"""
        CREATE TABLE {qualified} AS
        SELECT
          source,
          code,
          cui,
          target_source,
          target_code,
          target_aui,
          target_cui,
          target_tty,
          'same_cui' AS relationship,
          'same_cui' AS match_type,
          0 AS match_depth,
          'same_cui_edges' AS edge_source,
          0 AS priority
        FROM mt4ds.same_cui_edges
        """
    )
    for ddl in (
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_crosswalk_source_code ON {qualified}(source, code)",
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_crosswalk_target ON {qualified}(target_source, target_code)",
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_crosswalk_match ON {qualified}(match_type, priority)",
    ):
        try:
            con.execute(ddl)
        except duckdb.Error as exc:
            logger.warning("Skipping index on %s: %s", qualified, exc)

    rows = _row_count(con, qualified)
    logger.info("Built %s: %s rows", qualified, rows)
    return {table: {"status": "created", "rows": rows}}


def _prepare_friendly_atoms(con, *, replace: bool) -> dict[str, object]:
    """Build mt4ds.friendly_atoms -- MEDLINEPLUS and CHV atoms with broad/heading flags."""
    table = "friendly_atoms"
    qualified = f"mt4ds.{table}"
    if not replace and _table_exists(con, "mt4ds", table):
        return {table: {"status": "exists", "rows": _row_count(con, qualified)}}

    logger.info("Building %s", qualified)
    con.execute(f"DROP TABLE IF EXISTS {qualified}")

    broad_names = sorted(BROAD_CHV_NAMES | BROAD_MEDLINEPLUS_NAMES)
    broad_name_sql = ", ".join(f"'{name}'" for name in broad_names)

    con.execute(
        f"""
        CREATE TABLE {qualified} AS
        SELECT
          a.cui, a.source, a.code, a.aui, a.tty, a.name,
          a.source AS friendly_source,
          CASE WHEN lower(a.name) IN ({broad_name_sql}) THEN true ELSE false END AS is_broad,
          CASE WHEN a.tty IN ('HX', 'PX') THEN true ELSE false END AS is_heading
        FROM mt4ds.atoms a
        WHERE a.source IN ('MEDLINEPLUS', 'CHV')
          AND a.is_active
        """
    )
    for ddl in (
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_friendly_cui ON {qualified}(cui)",
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_friendly_source_code ON {qualified}(source, code)",
    ):
        try:
            con.execute(ddl)
        except duckdb.Error as exc:
            logger.warning("Skipping index on %s: %s", qualified, exc)

    rows = _row_count(con, qualified)
    logger.info("Built %s: %s rows", qualified, rows)
    return {table: {"status": "created", "rows": rows}}


def _prepare_rxnorm_tty_tables(con, *, replace: bool) -> dict[str, object]:
    """Build static RxNorm TTY topology tables from RXNORM_TTY_TOPOLOGY and compute_tty_paths."""
    results: dict[str, object] = {}

    # --- rxnorm_allowed_tty_edges ---
    table = "rxnorm_allowed_tty_edges"
    qualified = f"mt4ds.{table}"
    if not replace and _table_exists(con, "mt4ds", table):
        results[table] = {"status": "exists", "rows": _row_count(con, qualified)}
    else:
        logger.info("Building %s", qualified)
        con.execute(f"DROP TABLE IF EXISTS {qualified}")
        con.execute(
            f"""
            CREATE TABLE {qualified} (
              source_tty VARCHAR,
              target_tty VARCHAR
            )
            """
        )
        edge_rows = []
        for source_tty, neighbors in sorted(RXNORM_TTY_TOPOLOGY.items()):
            for target_tty in neighbors:
                edge_rows.append((source_tty, target_tty))
        if edge_rows:
            con.executemany(
                f"INSERT INTO {qualified} VALUES (?, ?)",
                edge_rows,
            )
        rows = _row_count(con, qualified)
        logger.info("Built %s: %s rows", qualified, rows)
        results[table] = {"status": "created", "rows": rows}

    # --- rxnorm_tty_paths and rxnorm_tty_path_steps ---
    paths_table = "rxnorm_tty_paths"
    paths_qualified = f"mt4ds.{paths_table}"
    steps_table = "rxnorm_tty_path_steps"
    steps_qualified = f"mt4ds.{steps_table}"

    if not replace and _table_exists(con, "mt4ds", paths_table):
        results[paths_table] = {"status": "exists", "rows": _row_count(con, paths_qualified)}
        results[steps_table] = {"status": "exists", "rows": _row_count(con, steps_qualified)}
    else:
        logger.info("Building %s and %s", paths_qualified, steps_qualified)
        con.execute(f"DROP TABLE IF EXISTS {paths_qualified}")
        con.execute(f"DROP TABLE IF EXISTS {steps_qualified}")
        con.execute(
            f"""
            CREATE TABLE {paths_qualified} (
              path_id INTEGER,
              start_tty VARCHAR,
              target_tty VARCHAR,
              match_type VARCHAR,
              target_order INTEGER,
              path_depth INTEGER
            )
            """
        )
        con.execute(
            f"""
            CREATE TABLE {steps_qualified} (
              path_id INTEGER,
              step INTEGER,
              tty VARCHAR
            )
            """
        )

        tty_paths = compute_tty_paths()
        path_rows = []
        step_rows = []
        for path_id, path_info in enumerate(tty_paths):
            steps = path_info["steps"]
            path_rows.append((
                path_id,
                path_info["start_tty"],
                path_info["target_tty"],
                path_info["match_type"],
                path_info["target_order"],
                len(steps) - 1,
            ))
            for step_idx, tty in enumerate(steps):
                step_rows.append((path_id, step_idx, tty))

        if path_rows:
            con.executemany(
                f"INSERT INTO {paths_qualified} VALUES (?, ?, ?, ?, ?, ?)",
                path_rows,
            )
        if step_rows:
            con.executemany(
                f"INSERT INTO {steps_qualified} VALUES (?, ?, ?)",
                step_rows,
            )

        results[paths_table] = {
            "status": "created",
            "rows": _row_count(con, paths_qualified),
        }
        results[steps_table] = {
            "status": "created",
            "rows": _row_count(con, steps_qualified),
        }
        logger.info(
            "Built %s and %s",
            results[paths_table]["rows"],
            results[steps_table]["rows"],
        )

    return results


def _prepare_rxnorm_tty_edges(con, *, replace: bool) -> dict[str, object]:
    """Build mt4ds.rxnorm_tty_edges -- materialized RxNorm AUI edges filtered by TTY topology."""
    table = "rxnorm_tty_edges"
    qualified = f"mt4ds.{table}"

    # Depends on rxnorm_allowed_tty_edges and atoms
    if not _table_exists(con, "mt4ds", "rxnorm_allowed_tty_edges"):
        logger.info("Skipping %s: rxnorm_allowed_tty_edges not found", qualified)
        return {table: {"status": "skipped", "rows": 0}}

    if not replace and _table_exists(con, "mt4ds", table):
        return {table: {"status": "exists", "rows": _row_count(con, qualified)}}

    mrrel_ref = _raw_ref("mrrel", con=con)

    logger.info("Building %s", qualified)
    con.execute(f"DROP TABLE IF EXISTS {qualified}")

    has_mrrel = _table_exists(con, "umls", "mrrel") or _table_exists(con, "main", "mrrel")
    if not has_mrrel:
        con.execute(
            f"CREATE TABLE {qualified} ("
            "source_aui VARCHAR, source_code VARCHAR, source_tty VARCHAR, "
            "source_name VARCHAR, source_suppress VARCHAR, "
            "target_aui VARCHAR, target_code VARCHAR, target_tty VARCHAR, "
            "target_name VARCHAR, target_suppress VARCHAR, "
            "rel VARCHAR, rela VARCHAR)"
        )
        rows = 0
        logger.info("Built %s (empty, no mrrel): %s rows", qualified, rows)
        return {table: {"status": "created", "rows": rows}}

    con.execute(
        f"""
        CREATE TABLE {qualified} AS
        SELECT DISTINCT
          s.aui AS source_aui, s.code AS source_code, s.tty AS source_tty,
          s.name AS source_name, s.suppress AS source_suppress,
          t.aui AS target_aui, t.code AS target_code, t.tty AS target_tty,
          t.name AS target_name, t.suppress AS target_suppress,
          r.REL AS rel, r.RELA AS rela
        FROM {mrrel_ref} r
        JOIN mt4ds.atoms s ON s.aui = r.AUI1
        JOIN mt4ds.atoms t ON t.aui = r.AUI2
        JOIN mt4ds.rxnorm_allowed_tty_edges e
          ON e.source_tty = s.tty AND e.target_tty = t.tty
        WHERE s.source = 'RXNORM' AND t.source = 'RXNORM'
        UNION
        SELECT DISTINCT
          t.aui AS source_aui, t.code AS source_code, t.tty AS source_tty,
          t.name AS source_name, t.suppress AS source_suppress,
          s.aui AS target_aui, s.code AS target_code, s.tty AS target_tty,
          s.name AS target_name, s.suppress AS target_suppress,
          r.REL AS rel, r.RELA AS rela
        FROM {mrrel_ref} r
        JOIN mt4ds.atoms s ON s.aui = r.AUI1
        JOIN mt4ds.atoms t ON t.aui = r.AUI2
        JOIN mt4ds.rxnorm_allowed_tty_edges e
          ON e.source_tty = t.tty AND e.target_tty = s.tty
        WHERE s.source = 'RXNORM' AND t.source = 'RXNORM'
        """
    )
    for ddl in (
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_rxntty_source_aui ON {qualified}(source_aui, target_tty)",
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_rxntty_source_code ON {qualified}(source_code, source_tty)",
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_rxntty_target_aui ON {qualified}(target_aui)",
    ):
        try:
            con.execute(ddl)
        except duckdb.Error as exc:
            logger.warning("Skipping index on %s: %s", qualified, exc)

    rows = _row_count(con, qualified)
    logger.info("Built %s: %s rows", qualified, rows)
    return {table: {"status": "created", "rows": rows}}


def _prepare_cvx_metadata(con, *, replace: bool) -> dict[str, object]:
    """Build mt4ds.cvx_metadata from an existing local CVX metadata table if present."""
    table = "cvx_metadata"
    qualified = f"mt4ds.{table}"
    if not replace and _table_exists(con, "mt4ds", table):
        return {table: {"status": "exists", "rows": _row_count(con, qualified)}}

    logger.info("Building %s", qualified)
    con.execute(f"DROP TABLE IF EXISTS {qualified}")
    if _table_exists(con, "main", table):
        con.execute(
            f"""
            CREATE TABLE {qualified} AS
            SELECT
              CAST(code AS VARCHAR) AS code,
              CAST(group_name AS VARCHAR) AS group_name,
              CAST(short_name AS VARCHAR) AS short_name
            FROM main.cvx_metadata
            """
        )
    else:
        con.execute(
            f"""
            CREATE TABLE {qualified} (
              code VARCHAR,
              group_name VARCHAR,
              short_name VARCHAR
            )
            """
        )

    try:
        con.execute(f"CREATE INDEX IF NOT EXISTS idx_mt4ds_cvx_metadata_code ON {qualified}(code)")
    except duckdb.Error as exc:
        logger.warning("Skipping index on %s: %s", qualified, exc)

    rows = _row_count(con, qualified)
    logger.info("Built %s: %s rows", qualified, rows)
    return {table: {"status": "created", "rows": rows}}


def _prepare_code_replacements(con, *, replace: bool) -> dict[str, object]:
    """Build mt4ds.code_replacements from UMLS MRREL replacement RELAs."""
    table = "code_replacements"
    qualified = f"mt4ds.{table}"
    if not replace and _table_exists(con, "mt4ds", table):
        rows = _row_count(con, qualified)
        if rows:
            return {table: {"status": "exists", "rows": rows}}
        # QC-398 (HIGH): an existing-but-EMPTY table means the original
        # build derived nothing (mrrel absent/truncated at prepare time).
        # Early-returning 'exists:0' here preserved the emptiness forever
        # and pinned --resolve-mode to the degraded path. Rebuild instead.
        logger.warning(
            "%s exists with 0 rows; rebuilding from MRREL replacement RELAs",
            qualified,
        )

    logger.info("Building %s", qualified)
    con.execute(f"DROP TABLE IF EXISTS {qualified}")
    has_mrrel = _table_exists(con, "umls", "mrrel") or _table_exists(con, "main", "mrrel")
    replacement_relas = ", ".join(f"'{rela}'" for rela in _REPLACEMENT_RELAS)
    if has_mrrel:
        mrrel_ref = _raw_ref("mrrel", con=con)
        con.execute(
            f"""
            CREATE TABLE {qualified} AS
            WITH candidates AS (
                SELECT
                  old_atom.source,
                  old_atom.code AS old_code,
                  new_atom.code AS new_code,
                  r.RELA AS rela
                FROM {mrrel_ref} r
                JOIN mt4ds.atoms old_atom ON old_atom.aui = r.AUI1
                JOIN mt4ds.atoms new_atom ON new_atom.aui = r.AUI2
                WHERE r.RELA IN ({replacement_relas})
                  AND old_atom.source = new_atom.source
                  AND old_atom.code != new_atom.code
                  AND NOT old_atom.is_active
                  AND new_atom.is_active
                UNION ALL
                SELECT
                  old_atom.source,
                  old_atom.code AS old_code,
                  new_atom.code AS new_code,
                  r.RELA AS rela
                FROM {mrrel_ref} r
                JOIN mt4ds.atoms old_atom ON old_atom.aui = r.AUI2
                JOIN mt4ds.atoms new_atom ON new_atom.aui = r.AUI1
                WHERE r.RELA IN ({replacement_relas})
                  AND old_atom.source = new_atom.source
                  AND old_atom.code != new_atom.code
                  AND NOT old_atom.is_active
                  AND new_atom.is_active
            ),
            ranked AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY source, old_code, new_code
                           ORDER BY
                               CASE rela
                                   WHEN 'same_as' THEN 0
                                   WHEN 'replaced_by' THEN 1
                                   ELSE 2
                               END,
                               new_code
                       ) AS rn
                FROM candidates
            )
            SELECT source, old_code, new_code, rela
            FROM ranked
            WHERE rn = 1
            """
        )
    else:
        con.execute(
            f"""
            CREATE TABLE {qualified} (
              source VARCHAR,
              old_code VARCHAR,
              new_code VARCHAR,
              rela VARCHAR
            )
            """
        )

    for ddl in (
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_replacements_old ON {qualified}(source, old_code)",
        f"CREATE INDEX IF NOT EXISTS idx_mt4ds_replacements_new ON {qualified}(source, new_code)",
    ):
        try:
            con.execute(ddl)
        except duckdb.Error as exc:
            logger.warning("Skipping index on %s: %s", qualified, exc)

    rows = _row_count(con, qualified)
    logger.info("Built %s: %s rows", qualified, rows)
    return {table: {"status": "created", "rows": rows}}


def _prepare_snomed_top_level_depth(con, *, replace: bool) -> dict[str, object]:
    """Build mt4ds.snomed_top_level_depth -- copy or compute from existing data."""
    table = "snomed_top_level_depth"
    qualified = f"mt4ds.{table}"
    if not replace and _table_exists(con, "mt4ds", table):
        return {table: {"status": "exists", "rows": _row_count(con, qualified)}}

    logger.info("Building %s", qualified)
    con.execute(f"DROP TABLE IF EXISTS {qualified}")

    # Check if main.snomed_top_level_depth already exists (created by data_setup)
    if _table_exists(con, "main", table):
        con.execute(
            f"""
            CREATE TABLE {qualified} AS
            SELECT code, min_top_depth FROM main.snomed_top_level_depth
            """
        )
    else:
        # Create empty placeholder -- the actual computation is done by
        # medterm4ds.services.data_setup.prepare_derived_tables()
        con.execute(
            f"""
            CREATE TABLE {qualified} (
              code VARCHAR,
              min_top_depth INTEGER
            )
            """
        )

    try:
        con.execute(
            f"CREATE INDEX IF NOT EXISTS idx_mt4ds_snomed_tld_code ON {qualified}(code)"
        )
    except duckdb.Error as exc:
        logger.warning("Skipping index on %s: %s", qualified, exc)

    rows = _row_count(con, qualified)
    logger.info("Built %s: %s rows", qualified, rows)
    return {table: {"status": "created", "rows": rows}}


def _prepare_patient_friendly_strategy(con, *, replace: bool) -> dict[str, object]:
    """Build mt4ds.patient_friendly_strategy from all registered source strategy rows."""
    table = "patient_friendly_strategy"
    qualified = f"mt4ds.{table}"
    if not replace and _table_exists(con, "mt4ds", table):
        return {table: {"status": "exists", "rows": _row_count(con, qualified)}}

    logger.info("Building %s", qualified)
    con.execute(f"DROP TABLE IF EXISTS {qualified}")
    con.execute(
        f"""
        CREATE TABLE {qualified} (
          source VARCHAR,
          phase VARCHAR,
          walk_kind VARCHAR,
          target_source VARCHAR,
          target_tty VARCHAR,
          match_type VARCHAR,
          priority INTEGER,
          max_depth INTEGER,
          stop_on_hit BOOLEAN,
          guard VARCHAR
        )
        """
    )

    all_rows: list[tuple[object, ...]] = []
    for source_name, strategy in SOURCE_STRATEGIES.items():
        for row in strategy.friendly_strategy_rows():
            all_rows.append((
                source_name,
                row.get("phase"),
                row.get("walk_kind"),
                row.get("target_source"),
                row.get("target_tty"),
                row.get("match_type"),
                row.get("priority"),
                row.get("max_depth"),
                row.get("stop_on_hit"),
                row.get("guard"),
            ))

    if all_rows:
        con.executemany(
            f"INSERT INTO {qualified} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            all_rows,
        )

    rows = _row_count(con, qualified)
    logger.info("Built %s: %s rows", qualified, rows)
    return {table: {"status": "created", "rows": rows}}


# ---------------------------------------------------------------------------
# Ordered list of all builder functions (called by prepare_mt4ds_schema)
# ---------------------------------------------------------------------------

_TABLE_BUILDERS = [
    _prepare_atoms,
    _prepare_best_atoms,
    _prepare_hierarchy_edges,
    _prepare_walk_edges,
    _prepare_walk_closure_limited,
    _prepare_same_cui_edges,
    _prepare_crosswalk_edges,
    _prepare_friendly_atoms,
    _prepare_rxnorm_tty_tables,
    _prepare_rxnorm_tty_edges,
    _prepare_cvx_metadata,
    _prepare_code_replacements,
    _prepare_snomed_top_level_depth,
    _prepare_patient_friendly_strategy,
]


def prepare_mt4ds_schema(
    con,
    *,
    replace: bool = False,
    db_role: str | None = None,
    umls_release: str | None = None,
    source_archive: str | None = None,
) -> dict[str, object]:
    """Create umls/mt4ds schemas, ensure UMLS views, populate manifest, and build runtime tables.

    Parameters
    ----------
    con : duckdb.DuckDBPyConnection
        An open DuckDB connection.
    replace : bool
        If True, drop and recreate the mt4ds schema (including manifest and tables).
    db_role : str | None
        Optional database role/provenance marker to record in the manifest.
    umls_release : str | None
        Optional explicit UMLS release to record in the manifest. When omitted,
        preparation attempts to discover the release from raw UMLS metadata.
    source_archive : str | None
        Optional source archive path/name to record in the manifest.

    Returns
    -------
    dict with keys: schemas_created, views_created, manifest_ready,
                    source_counts, prepared_schema_version, errors,
                    tables.
    """
    report: dict[str, object] = {
        "schemas_created": [],
        "views_created": [],
        "manifest_ready": False,
        "source_counts": {},
        "prepared_schema_version": PREPARED_SCHEMA_VERSION,
        "umls_release": None,
        "db_role": db_role,
        "source_archive": source_archive,
        "errors": [],
        "tables": {},
    }

    existing_manifest: dict[str, str] = {}
    if replace and _table_exists(con, "mt4ds", "prepare_manifest"):
        try:
            rows = con.execute(
                """
                SELECT key, value
                FROM mt4ds.prepare_manifest
                WHERE key IN ('db_role', 'source_archive', 'umls_release')
                """
            ).fetchall()
            existing_manifest = {
                str(key): str(value)
                for key, value in rows
                if value is not None
            }
        except Exception as exc:
            report["errors"].append(f"manifest preservation read error: {exc}")  # type: ignore[union-attr]

    db_role = db_role or existing_manifest.get("db_role")
    source_archive = source_archive or existing_manifest.get("source_archive")
    preserved_umls_release = existing_manifest.get("umls_release")
    report["db_role"] = db_role
    report["source_archive"] = source_archive

    # --- Create schemas ---
    for schema_name in ("umls", "mt4ds"):
        if replace and schema_name == "mt4ds" and _schema_exists(con, schema_name):
            con.execute(f'DROP SCHEMA "{schema_name}" CASCADE')
            logger.info("Dropped schema %s (replace=True)", schema_name)
        if not _schema_exists(con, schema_name):
            con.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"')
            report["schemas_created"].append(schema_name)  # type: ignore[union-attr]

    # --- Detect raw table locations ---
    locations = _detect_raw_location(con)

    # --- Create views (main -> umls) ---
    report["views_created"] = _ensure_views(con, locations)  # type: ignore[assignment]

    # --- Create / recreate manifest ---
    if replace and _table_exists(con, "mt4ds", "prepare_manifest"):
        con.execute('DROP TABLE mt4ds.prepare_manifest')

    con.execute(
        "CREATE TABLE IF NOT EXISTS mt4ds.prepare_manifest ("
        "  key VARCHAR PRIMARY KEY,"
        "  value VARCHAR,"
        "  updated_at TIMESTAMP DEFAULT current_timestamp"
        ")"
    )
    report["manifest_ready"] = True  # type: ignore[assignment]

    # --- Record metadata ---
    try:
        from medterm4ds import __version__ as pkg_version
    except Exception:
        pkg_version = "unknown"
    _upsert_manifest(con, "package_version", pkg_version)
    _upsert_manifest(con, "prepared_schema_version", PREPARED_SCHEMA_VERSION)
    _upsert_manifest(con, "patient_friendly_policy_version", PATIENT_FRIENDLY_POLICY_VERSION)
    if db_role:
        _upsert_manifest(con, "db_role", db_role)
    if source_archive:
        _upsert_manifest(con, "source_archive", source_archive)

    # Prefer explicit release metadata; otherwise attempt to discover it from mrsat.
    effective_umls_release = umls_release or _discover_umls_release(con) or preserved_umls_release
    if effective_umls_release:
        _upsert_manifest(con, "umls_release", effective_umls_release)
        report["umls_release"] = effective_umls_release  # type: ignore[assignment]

    # --- Source row counts ---
    source_counts: dict[str, int | None] = {}
    catalog = _current_catalog(con)
    for table in _UMLS_TABLES:
        location = locations.get(table, "")
        if location == "umls":
            # Catalog-qualified (see verify_mt4ds_schema): two-part refs are
            # ambiguous when the DB filename stem equals the schema name.
            count = _row_count(con, f'"{catalog}"."umls"."{table}"')
        elif location == "main":
            count = _row_count(con, f'"{catalog}"."main"."{table}"')
        else:
            count = None
        source_counts[table] = count
        if count is not None:
            _upsert_manifest(con, f"source_count.{table}", str(count))
    report["source_counts"] = source_counts  # type: ignore[assignment]

    # --- Build prepared runtime tables ---
    tables_report: dict[str, object] = {}
    for builder in _TABLE_BUILDERS:
        try:
            result = builder(con, replace=replace)
            tables_report.update(result)  # type: ignore[union-attr]
        except Exception as exc:
            builder_name = builder.__name__
            logger.error("Failed to build table via %s: %s", builder_name, exc)
            report["errors"].append(f"{builder_name}: {exc}")  # type: ignore[union-attr]

    report["tables"] = tables_report  # type: ignore[assignment]

    # Record table build results in manifest
    for table_name, table_info in tables_report.items():
        if isinstance(table_info, dict):
            _upsert_manifest(
                con,
                f"table.{table_name}",
                f"{table_info.get('status', 'unknown')}:{table_info.get('rows', '?')}",
            )

    return report


def _discover_umls_release(con) -> str | None:
    """Try to read UMLS release info from mrsat or manifest metadata."""
    qualified = _raw_ref("mrsat", con=con)
    try:
        rows = con.execute(
            f"SELECT DISTINCT ATV FROM {qualified} WHERE ATN = 'RELEASE' LIMIT 1"  # noqa: S608
        ).fetchall()
        if rows and rows[0][0]:
            return str(rows[0][0])
    except Exception:
        pass
    return None


def verify_mt4ds_schema(con) -> dict[str, object]:
    """Verify umls/mt4ds schemas and return metadata.

    Returns
    -------
    dict with keys: umls_schema_exists, mt4ds_schema_exists,
                    manifest_exists, source_tables, prepared_schema_version,
                    package_version, umls_release, errors.
    """
    result: dict[str, object] = {
        "umls_schema_exists": _schema_exists(con, "umls"),
        "mt4ds_schema_exists": _schema_exists(con, "mt4ds"),
        "manifest_exists": _table_exists(con, "mt4ds", "prepare_manifest"),
        "source_tables": {},
        "prepared_tables": {},
        "prepared_schema_version": None,
        "patient_friendly_policy_version": None,
        "package_version": None,
        "umls_release": None,
        "db_role": None,
        "source_archive": None,
        "errors": [],
    }

    # Source table metadata
    locations = _detect_raw_location(con)
    source_tables: dict[str, dict[str, object]] = {}
    # Build one UNION-ALL query that counts all source tables at once
    # instead of one COUNT(*) round-trip per table.
    source_count_clauses = []
    for table in _UMLS_TABLES:
        location = locations.get(table, "")
        if location:
            source_count_clauses.append((table, location))
    source_counts: dict[str, int] = {}
    if source_count_clauses:
        # Catalog-qualified refs: a two-part 'umls."mrconso"' is ambiguous
        # when the DB filename stem equals the schema name (e.g.
        # 'umls.duckdb' -> catalog 'umls') — the count batch silently failed
        # (WARNING + None) for such DBs before this was qualified.
        catalog = _current_catalog(con)
        union_sql = " UNION ALL ".join(
            f"SELECT '{t}' AS t, COUNT(*) AS n FROM \"{catalog}\".{loc}.\"{t}\""
            for t, loc in source_count_clauses
        )
        try:
            for row in con.execute(union_sql).fetchall():
                source_counts[str(row[0])] = int(row[1])
        except duckdb.Error as exc:
            # QC-459 (HIGH): a broken umls.* view (e.g. catalog-baked view DDL
            # after a DB copy/rename) previously logged a WARNING and reported
            # row counts as None while verify still passed. A failing count
            # probe means the source tables/views are unreadable — surface it
            # in the report errors so the verdict flips to failure.
            logging.getLogger(__name__).warning(
                "verify_mt4ds_schema source-table count batch failed: %s. "
                "Row counts will be reported as None.", exc,
            )
            result["errors"].append(  # type: ignore[union-attr]
                f"source-table count query failed: {exc}"
            )
    for table in _UMLS_TABLES:
        location = locations.get(table, "")
        if location:
            source_tables[table] = {
                "location": location,
                "row_count": source_counts.get(table),
            }
        else:
            source_tables[table] = {"location": None, "row_count": None}
    result["source_tables"] = source_tables

    # Prepared mt4ds runtime table metadata
    # One existence query (information_schema) + one UNION-ALL count query
    # instead of 2 round-trips per prepared table.
    required = list(_REQUIRED_MT4DS_TABLES)
    placeholders = ", ".join(["?"] * len(required))
    existing_prepared: set[str] = set()
    try:
        rows = con.execute(
            f"""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'mt4ds' AND table_name IN ({placeholders})
            """,
            required,
        ).fetchall()
        existing_prepared = {str(r[0]) for r in rows}
    except duckdb.Error as exc:
        logging.getLogger(__name__).warning(
            "verify_mt4ds_schema prepared-table existence probe failed: %s", exc,
        )

    prepared_counts: dict[str, int] = {}
    if existing_prepared:
        union_sql = " UNION ALL ".join(
            f"SELECT '{t}' AS t, COUNT(*) AS n FROM mt4ds.\"{t}\""
            for t in sorted(existing_prepared)
        )
        try:
            for row in con.execute(union_sql).fetchall():
                prepared_counts[str(row[0])] = int(row[1])
        except duckdb.Error as exc:
            # QC-459 (HIGH): same treatment as the source-table batch — an
            # unreadable prepared table is an error, not a WARNING + None.
            logging.getLogger(__name__).warning(
                "verify_mt4ds_schema prepared-table count batch failed: %s. "
                "Row counts will be reported as None.", exc,
            )
            result["errors"].append(  # type: ignore[union-attr]
                f"prepared-table count query failed: {exc}"
            )

    prepared_tables: dict[str, dict[str, object]] = {}
    missing_prepared: list[str] = []
    for table in required:
        exists = table in existing_prepared
        prepared_tables[table] = {
            "exists": exists,
            "row_count": prepared_counts.get(table) if exists else None,
        }
        if not exists:
            missing_prepared.append(table)
    result["prepared_tables"] = prepared_tables

    # Manifest metadata
    if result["manifest_exists"]:
        for key, field in [
            ("prepared_schema_version", "prepared_schema_version"),
            ("patient_friendly_policy_version", "patient_friendly_policy_version"),
            ("package_version", "package_version"),
            ("umls_release", "umls_release"),
            ("db_role", "db_role"),
            ("source_archive", "source_archive"),
        ]:
            try:
                rows = con.execute(
                    "SELECT value FROM mt4ds.prepare_manifest WHERE key = ?",
                    [key],
                ).fetchall()
                if rows:
                    result[field] = rows[0][0]  # type: ignore[assignment]
            except Exception as exc:
                result["errors"].append(f"manifest read error for {key}: {exc}")  # type: ignore[union-attr]

    missing = [t for t in _UMLS_TABLES if not locations.get(t)]
    if missing:
        result["errors"].append(f"missing raw tables: {', '.join(missing)}")  # type: ignore[union-attr]
    if missing_prepared:
        result["errors"].append(
            f"missing prepared tables: {', '.join(missing_prepared)}"
        )  # type: ignore[union-attr]

    # QC-460 (MEDIUM): compare live row counts against the golden counts the
    # manifest recorded at prepare time (source_count.<table> and
    # table.<name> 'status:rows'). Pre-fix, a DB mutated after prepare —
    # truncated mrconso, deleted mt4ds rows — verified with errors: [] by
    # construction: the drift-detection data sat unused in the manifest.
    if result["manifest_exists"]:
        try:
            rows = con.execute(
                "SELECT key, value FROM mt4ds.prepare_manifest "
                "WHERE key LIKE 'source_count.%' OR key LIKE 'table.%'"
            ).fetchall()
        except duckdb.Error as exc:
            result["errors"].append(f"manifest golden-count read error: {exc}")  # type: ignore[union-attr]
        else:
            for key, value in rows:
                table = str(key).split(".", 1)[1]
                try:
                    golden = int(str(value).rsplit(":", 1)[-1])
                except ValueError:
                    continue  # 'status:?' placeholder rows have no count
                if str(key).startswith("source_count."):
                    live = source_counts.get(table)
                else:
                    live = prepared_counts.get(table)
                if live is not None and live != golden:
                    result["errors"].append(  # type: ignore[union-attr]
                        f"manifest drift on {table}: manifest recorded {golden} rows, "
                        f"live table has {live} (database changed after prepare)"
                    )

    prepared_version = result.get("prepared_schema_version")
    if prepared_version is not None and str(prepared_version) != PREPARED_SCHEMA_VERSION:
        result["errors"].append(
            "prepared schema version mismatch: "
            f"found {prepared_version}, expected {PREPARED_SCHEMA_VERSION}"
        )  # type: ignore[union-attr]
    policy_version = result.get("patient_friendly_policy_version")
    if policy_version is not None and str(policy_version) != PATIENT_FRIENDLY_POLICY_VERSION:
        result["errors"].append(
            "patient-friendly policy version mismatch: "
            f"found {policy_version}, expected {PATIENT_FRIENDLY_POLICY_VERSION}"
        )  # type: ignore[union-attr]

    return result
