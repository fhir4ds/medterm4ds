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


