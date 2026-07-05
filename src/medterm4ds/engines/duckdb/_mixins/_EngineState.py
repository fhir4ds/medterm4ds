"""Connection, config, prepared-cache lifecycle, and shared temp-table helpers."""


from __future__ import annotations

from medterm4ds.engines.duckdb._engine_base import *  # noqa: F401,F403
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from medterm4ds.core.config import LocalDuckDBConfig


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

