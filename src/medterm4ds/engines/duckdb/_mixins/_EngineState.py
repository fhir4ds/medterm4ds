"""Connection, config, prepared-cache lifecycle, and shared temp-table helpers."""


from __future__ import annotations

import duckdb

from medterm4ds.engines.duckdb._engine_base import *  # noqa: F401,F403
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from medterm4ds.core.config import LocalDuckDBConfig
from medterm4ds.core.sql import chunks

# Multi-row VALUES batch size for temp-table staging (QC-371). ~1000 keeps
# each INSERT statement small while amortizing per-statement overhead.
_TEMP_CODE_BATCH_SIZE = 1000


class _EngineState:
    """Connection, config, prepared-cache lifecycle, and shared temp-table helpers.

    Mixin for LocalDuckDBEngine — methods share state via ``self`` (``self.con``,
    ``self.cache_prepared``, ``self.query_chunk_size``, etc.). Not intended to be
    instantiated on its own.
    """

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
        # QC-435: warn once per engine instance on prepared-schema version
        # skew (per-call logging would spam batch patient-friendly workloads).
        self._prepared_version_mismatch_warned = False
        # QC-437: distinct missing-table sets already warned about by the
        # patient-friendly prepared gate (avoids per-call log spam).
        self._pf_gate_refusal_warned: set[frozenset[str]] = set()
        # Per-instance cache of (table_name -> exists). A single prepare run
        # can issue 60-100+ _table_exists probes against information_schema;
        # memoizing collapses that to one round-trip per unique name.
        # Invalidate via _invalidate_table_exists_cache() after any DDL.
        self._table_exists_cache: dict[str, bool] = {}
        self._active_source_code_cache: dict[str, set[str]] = {}
        # Per-source walk_edges coverage memo (QC-402). Keyed by source name.
        self._walk_edges_source_cache: dict[str, bool] = {}
        self.con.execute(f"SET preserve_insertion_order={str(preserve_insertion_order).lower()}")
        # QC-465 (MEDIUM): these were falsy checks (``if threads:``), so
        # explicit-but-falsy overrides were silently DROPPED instead of
        # applied: threads=0 kept the profile default, memory_limit=''
        # reverted to DuckDB's ~80%-of-RAM default despite profile='low',
        # temp_directory='' was ignored. ``None`` means "use the profile
        # default"; anything else is forwarded (and local_duckdb_config
        # has already rejected malformed values with a named ValueError).
        if threads is not None:
            self.con.execute(f"PRAGMA threads={int(threads)}")
        if memory_limit is not None:
            self.con.execute(f"PRAGMA memory_limit='{memory_limit}'")
        if temp_directory is not None:
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

        # prepare_cache creates/drops multiple temp tables and mt4ds tables;
        # invalidate the existence cache so probes during and after prepare
        # don't return stale state from before the schema changed.
        self._invalidate_table_exists_cache()

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
                # CR-035 (EC-20 sibling): narrow to duckdb.Error + warn — a
                # bare except hid programming bugs and logged at debug.
                try:
                    self.con.execute(ddl)
                except duckdb.Error as exc:
                    logger.warning("Skipping local DuckDB cache index %s: %s", ddl, exc)

        # prepare_cache just created temp tables (mrconso, mrrel, indexes).
        # Invalidate again so post-prepare probes see the new state.
        self._invalidate_table_exists_cache()
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
            except duckdb.Error as exc:
                # CR-035 (EC-20 sibling): narrow + warn (was bare except/debug).
                logger.warning("Skipping SNOMED parent link cache index: %s", exc)
            self._snomed_parent_links_cache_prepared = True
            return _SNOMED_PARENT_LINKS_CACHE_TABLE
        except duckdb.Error as exc:
            # CR-035 (EC-20 sibling): narrow + warn (was bare except/debug).
            logger.warning("Failed to create SNOMED parent link cache: %s", exc)
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



    def _table_exists(self, name: str) -> bool:
        """Cached existence check for a table by name (any schema).

        A single prepare run can probe the same table name multiple times
        (e.g., 'crosswalk_edges' from prepared.py and from
        _has_patient_friendly_prepared_tables). Memoize per-instance to
        avoid redundant information_schema round-trips.

        Cache is invalidated on DDL via _invalidate_table_exists_cache,
        which prepare_cache and any CREATE TABLE site must call.
        """
        cached = self._table_exists_cache.get(name)
        if cached is not None:
            return cached
        row = self.con.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_name = ?
            LIMIT 1
            """,
            [name],
        ).fetchone()
        result = bool(row)
        self._table_exists_cache[name] = result
        return result

    def _walk_edges_cover_source(self, source: str) -> bool:
        """QC-402 (MEDIUM): per-source non-empty gate on ``mt4ds.walk_edges``.

        Mirrors the QC-398 gate on ``code_replacements``: production prepared
        tables can predate builder fixes that ADDED whole sources (RXNORM/MSH
        have 0 rows) or new edge vocabularies (ATC partial, LNC missing
        class_of). Gating the prepared hierarchy dispatch on TABLE EXISTENCE
        alone let a source with no rows silently return [] on every surface
        while raw mrrel has the edges. A source with no walk_edges rows defers
        to the raw-mrrel path. ``LIMIT 1`` keeps the probe bounded (indexed on
        the leading ``source`` column); memoized because the dispatch runs per
        (source, chunk).
        """
        cached = self._walk_edges_source_cache.get(source)
        if cached is not None:
            return cached
        probe = self.con.execute(
            "SELECT 1 FROM mt4ds.walk_edges WHERE source = ? LIMIT 1",
            [source],
        ).fetchone()
        covered = probe is not None
        if not covered:
            logger.warning(
                "mt4ds.walk_edges has 0 rows for source %r — hierarchy ops for "
                "this source fall back to the raw mrrel path (prepared tables "
                "are stale or partial; rebuild with `medterm4ds data "
                "prepare-derived --db <db>`).",
                source,
            )
        self._walk_edges_source_cache[source] = covered
        return covered

    def _invalidate_table_exists_cache(self) -> None:
        """Clear the _table_exists cache. Call after any DDL that may have
        created or dropped tables (prepare_cache, CREATE TEMP TABLE, etc.)."""
        self._table_exists_cache.clear()



    def _has_prepared_tables(self) -> bool:
        """Check if mt4ds prepared tables are available (cached after first check)."""
        if self._prepared_tables_available is None:
            try:
                rows = self.con.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'mt4ds' AND table_name = 'best_atoms'"
                ).fetchall()
                self._prepared_tables_available = len(rows) > 0
            except duckdb.Error as exc:
                # QC-437 sibling: narrow to duckdb.Error, log the degraded probe.
                logger.warning(
                    "Failed to probe for mt4ds.best_atoms (%s); prepared-table "
                    "paths treated as unavailable.",
                    exc,
                )
                self._prepared_tables_available = False
        return self._prepared_tables_available



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

            if str(row[0]) != PREPARED_SCHEMA_VERSION:
                # QC-435 (HIGH)/QC-437 (LOW): the version gate silently
                # disabled the prepared patient-friendly/crosswalk paths —
                # every answer came from the legacy resolver (LNC raising
                # NotImplementedError) with no operator signal. Loudly warn
                # ONCE per engine instance naming both versions and the
                # consequence; remediation is a rebuild of the PERSISTED
                # mt4ds schema (engine.prepare_cache only builds temp tables
                # and cannot re-version it).
                if not self._prepared_version_mismatch_warned:
                    logger.warning(
                        "Prepared mt4ds schema version %s does not match package "
                        "version %s — prepared patient-friendly/crosswalk paths are "
                        "DISABLED for this connection; answers are served by the "
                        "legacy raw-mrrel resolver (LNC patient-friendly unsupported). "
                        "Rebuild the persisted mt4ds schema: medterm4ds data "
                        "prepare-derived --db <db>.",
                        row[0],
                        PREPARED_SCHEMA_VERSION,
                    )
                    self._prepared_version_mismatch_warned = True
                return False
            return True
        except duckdb.Error as exc:
            # QC-437: narrow the bare ``except Exception`` to duckdb.Error so
            # programming bugs propagate; a failed probe is still a degraded
            # gate and must be visible, not silent.
            logger.warning(
                "Failed to read mt4ds.prepare_manifest (%s); treating the "
                "prepared schema version as NOT current — prepared "
                "patient-friendly/crosswalk paths are disabled and the legacy "
                "resolver serves answers.",
                exc,
            )
            return False

    @contextmanager


    def _temp_codes(self, codes: Sequence[str]) -> Iterator[str]:
        table = f"_mt4ds_codes_{uuid4().hex}"
        self.con.execute(f"CREATE TEMP TABLE {table} (code VARCHAR)")
        try:
            # QC-371 (LOW): executemany issued one single-row INSERT per code
            # (~4.7K rows/s), dominating batch-lookup wall time at 100K
            # inputs. Stage via multi-row VALUES batches instead — WITH
            # bound placeholders (not sql_values literals) so codes carrying
            # control characters / quote bytes stay safe (regression caught
            # by test_xml_control_chars_sanitized_qc300: code "a\0b" broke
            # the literal form with a Parser Error).
            rows = [(str(code),) for code in _dedupe(codes)]
            for batch in chunks(rows, _TEMP_CODE_BATCH_SIZE):
                placeholders = ", ".join(["(?)"] * len(batch))
                self.con.execute(
                    f"INSERT INTO {table} VALUES {placeholders}",
                    [value for row in batch for value in row],
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

