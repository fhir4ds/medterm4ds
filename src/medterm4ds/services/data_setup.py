"""Download and build local DuckDB terminology data."""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
from collections import defaultdict, deque
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

RELEASES_URL = "https://uts-ws.nlm.nih.gov/releases"
DOWNLOAD_URL = "https://uts-ws.nlm.nih.gov/download"
DEFAULT_UMLS_RELEASE_TYPE = "umls-metathesaurus-full-subset"

_MRCONSO_COLUMNS = ("CUI", "LAT", "TS", "LUI", "STT", "SUI", "ISPREF", "AUI", "SAUI", "SCUI", "SDUI", "SAB", "TTY", "CODE", "STR", "SRL", "SUPPRESS", "CVF")
_MRREL_COLUMNS = ("CUI1", "AUI1", "STYPE1", "REL", "CUI2", "AUI2", "STYPE2", "RELA", "RUI", "SRUI", "SAB", "SL", "RG", "DIR", "SUPPRESS", "CVF")
_MRSAT_COLUMNS = ("CUI", "LUI", "SUI", "METAUI", "STYPE", "CODE", "ATUI", "SATUI", "ATN", "SAB", "ATV", "SUPPRESS", "CVF")

DEFAULT_UMLS_VERIFY_SOURCES = (
    "ICD10CM",
    "ICD10PCS",
    "HCPCS",
    "SNOMEDCT_US",
    "RXNORM",
    "LNC",
    "CVX",
    "CPT",
)


def download_release(
    *,
    output_dir: str | Path = "data/umls",
    api_key: str | None = None,
    release_type: str = DEFAULT_UMLS_RELEASE_TYPE,
    release_version: str | None = None,
    current: bool | None = None,
    extract: bool = False,
) -> Path:
    """Download a UTS release zip using the NLM Release and Download APIs."""
    key = api_key or os.getenv("UMLS_API_KEY") or os.getenv("UTS_API_KEY")
    if not key:
        raise RuntimeError("A UMLS API key is required. Pass --api-key or set UMLS_API_KEY.")
    release = current_release(
        release_type=release_type,
        release_version=release_version,
        current=current,
    )
    download_url = release.get("downloadUrl")
    file_name = release.get("fileName") or Path(str(download_url)).name
    if not download_url:
        raise RuntimeError(f"Release metadata did not include downloadUrl: {release}")
    _validate_download_filename(str(file_name))
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / str(file_name)
    query = urlencode({"url": download_url, "apiKey": key})
    with urlopen(f"{DOWNLOAD_URL}?{query}") as response:  # noqa: S310
        with output_path.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    if extract and zipfile.is_zipfile(output_path):
        extract_dir = out_dir / output_path.stem
        with zipfile.ZipFile(output_path) as archive:
            _safe_zip_extract(archive, extract_dir)
    return output_path


# Defensive guards for download_release (Tier B security hardening, 2026-06-26).
# The UTS release JSON drives both the output filename and (when extract=True)
# the archive contents. A compromised or MITM'd UTS endpoint could try to:
#   - write outside output_dir via "../../etc/x.zip" filenames
#   - escape extract_dir via "../" zip members (zip-slip)
# These guards refuse those inputs. Allowed chars cover the realistic UTS
# filename space (uppercase alphanumerics, dashes, dots).
_FILENAME_ALLOWED = re.compile(r"^[A-Za-z0-9._\-]+$")


def _validate_download_filename(name: str) -> None:
    if not name or not _FILENAME_ALLOWED.match(name):
        raise RuntimeError(
            f"Refusing download filename with disallowed characters: {name!r}. "
            f"Allowed: uppercase/lowercase letters, digits, dot, dash, underscore."
        )


def _safe_zip_extract(archive: zipfile.ZipFile, extract_dir: Path) -> None:
    """Extract archive members, refusing any that escape extract_dir.

    Guards against zip-slip: a crafted archive with members like
    "../../etc/passwd" would otherwise write outside extract_dir.
    """
    extract_dir_resolved = extract_dir.resolve()
    for member in archive.namelist():
        member_path = (extract_dir_resolved / member).resolve()
        try:
            member_path.relative_to(extract_dir_resolved)
        except ValueError as exc:
            raise RuntimeError(
                f"Refusing to extract archive member outside target dir: {member!r}"
            ) from exc
        archive.extract(member, extract_dir)


def download_umls_release(
    *,
    output_dir: str | Path = "data/umls",
    api_key: str | None = None,
    release_type: str = DEFAULT_UMLS_RELEASE_TYPE,
    release_version: str | None = None,
    current: bool | None = None,
    extract: bool = False,
) -> Path:
    """Download a UMLS release archive for local database setup."""
    return download_release(
        output_dir=output_dir,
        api_key=api_key,
        release_type=release_type,
        release_version=release_version,
        current=current,
        extract=extract,
    )


def current_release(
    *,
    release_type: str,
    release_version: str | None = None,
    current: bool | None = None,
) -> dict:
    """Return release metadata from UTS.

    ``current`` is a release-list filter, not a request for historical content
    within the downloaded release. Omit it by default so callers can pin a
    release version or deliberately choose the first returned release.
    """
    query_args = {"releaseType": release_type}
    if current is not None:
        query_args["current"] = str(current).lower()
    query = urlencode(query_args)
    with urlopen(f"{RELEASES_URL}?{query}") as response:  # noqa: S310
        payload = json.loads(_read_capped(response).decode("utf-8"))
    if isinstance(payload, list):
        if not payload:
            raise RuntimeError(f"No releases found for {release_type}.")
        releases = payload
    elif isinstance(payload, dict):
        releases = [payload]
    else:
        raise RuntimeError("Unexpected UTS release response.")

    if release_version:
        wanted = release_version.upper()
        for release in releases:
            if str(release.get("releaseVersion", "")).upper() == wanted:
                return release
        available = ", ".join(str(release.get("releaseVersion", "")) for release in releases)
        raise RuntimeError(
            f"Release {release_version!r} was not found for {release_type}. "
            f"Available releases: {available}"
        )
    return releases[0]


def build_duckdb_from_rrf(
    *,
    rrf_dir: str | Path,
    output_db: str | Path,
    replace: bool = False,
    batch_size: int = 100_000,
    db_role: str | None = None,
    release_version: str | None = None,
    source_archive: str | Path | None = None,
) -> Path:
    """Build the compact local DuckDB tables from UMLS RRF files.

    The input directory can contain flat ``MR*.RRF`` files, ``MR*.RRF.gz``
    files, or UMLS ``.nlm`` archives containing ``MR*.RRF.*.gz`` shards.
    """
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("DuckDB is required to build local DuckDB data.") from exc

    _ = batch_size  # Kept for backward-compatible CLI/API signatures.
    source_dir = Path(rrf_dir)
    db_path = Path(output_db)
    if db_path.name == "umls_local.duckdb":
        raise RuntimeError(
            "Refusing ambiguous output DB name 'umls_local.duckdb'. "
            "Use a role/release-specific path such as data/umls_current.duckdb "
            "or data/umls_2025ab.duckdb."
        )
    if db_path.exists():
        if replace:
            db_path.unlink()
        else:
            raise RuntimeError(f"Output database exists: {db_path}")

    with tempfile.TemporaryDirectory(prefix="medterm4ds_rrf_") as staging:
        staging_dir = Path(staging)
        mrconso = _find_rrf_sources(source_dir, "MRCONSO.RRF", staging_dir=staging_dir)
        mrrel = _find_rrf_sources(source_dir, "MRREL.RRF", staging_dir=staging_dir)
        mrsat = _find_rrf_sources(source_dir, "MRSAT.RRF", staging_dir=staging_dir, required=False)

        con = duckdb.connect(str(db_path))
        try:
            _create_rrf_table(
                con,
                table="mrconso",
                sources=mrconso,
                columns=_MRCONSO_COLUMNS,
                selected=("CUI", "AUI", "SAB", "TTY", "CODE", "STR", "SUPPRESS"),
            )
            _create_rrf_table(
                con,
                table="mrrel",
                sources=mrrel,
                columns=_MRREL_COLUMNS,
                selected=("AUI1", "AUI2", "REL", "RELA"),
            )
            if mrsat:
                _create_rrf_table(
                    con,
                    table="mrsat",
                    sources=mrsat,
                    columns=_MRSAT_COLUMNS,
                    selected=("CODE", "SAB", "ATN", "ATV"),
                )
            else:
                con.execute("CREATE TABLE mrsat (CODE VARCHAR, SAB VARCHAR, ATN VARCHAR, ATV VARCHAR)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_mrconso_sab_code ON mrconso(SAB, CODE)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_mrconso_aui ON mrconso(AUI)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_mrrel_aui1 ON mrrel(AUI1)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_mrrel_aui2 ON mrrel(AUI2)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_mrsat_ndc ON mrsat(SAB, ATN, ATV)")
            prepare_derived_tables(con, replace=True)
            from medterm4ds.engines.duckdb.prepared import prepare_mt4ds_schema
            prepare_mt4ds_schema(
                con,
                replace=True,
                db_role=db_role,
                umls_release=release_version,
                source_archive=str(source_archive) if source_archive else None,
            )
        finally:
            con.close()
    return db_path


def build_umls_duckdb(
    *,
    rrf_dir: str | Path,
    output_db: str | Path,
    replace: bool = False,
    batch_size: int = 100_000,
    db_role: str | None = None,
    release_version: str | None = None,
    source_archive: str | Path | None = None,
) -> Path:
    """Build a compact local DuckDB database from UMLS RRF release files."""
    return build_duckdb_from_rrf(
        rrf_dir=rrf_dir,
        output_db=output_db,
        replace=replace,
        batch_size=batch_size,
        db_role=db_role,
        release_version=release_version,
        source_archive=source_archive,
    )


def prepare_umls_duckdb(
    db_path: str | Path,
    *,
    replace: bool = True,
    db_role: str | None = None,
    release_version: str | None = None,
    source_archive: str | Path | None = None,
) -> dict[str, object]:
    """Create derived and prepared tables for local DuckDB terminology services."""
    import duckdb

    path = Path(db_path)
    con = duckdb.connect(str(path))
    try:
        report = prepare_derived_tables(con, replace=replace)
        from medterm4ds.engines.duckdb.prepared import prepare_mt4ds_schema
        mt4ds_report = prepare_mt4ds_schema(
            con,
            replace=replace,
            db_role=db_role,
            umls_release=release_version,
            source_archive=str(source_archive) if source_archive else None,
        )
        report["mt4ds"] = mt4ds_report
        return report
    finally:
        con.close()


def annotate_umls_duckdb(
    db_path: str | Path,
    *,
    db_role: str | None = None,
    release_version: str | None = None,
    source_archive: str | Path | None = None,
) -> dict[str, str]:
    """Record DB role and release provenance in ``mt4ds.prepare_manifest``."""
    import duckdb

    path = Path(db_path)
    annotations: dict[str, str] = {}
    if db_role:
        annotations["db_role"] = db_role
    if release_version:
        annotations["umls_release"] = release_version
    if source_archive:
        annotations["source_archive"] = str(source_archive)
    if not annotations:
        return {}

    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS mt4ds")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS mt4ds.prepare_manifest (
              key VARCHAR PRIMARY KEY,
              value VARCHAR,
              updated_at TIMESTAMP
            )
            """
        )
        updated_at = datetime.now(timezone.utc).isoformat()
        con.executemany(
            """
            INSERT INTO mt4ds.prepare_manifest (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT (key) DO UPDATE
            SET value = excluded.value,
                updated_at = excluded.updated_at
            """,
            [(key, value, updated_at) for key, value in annotations.items()],
        )
    finally:
        con.close()
    return annotations


def prepare_derived_tables(con, *, replace: bool = True) -> dict[str, object]:
    """Create optional derived tables for cross-reference guardrails."""
    tables = {row[0] for row in con.execute("show tables").fetchall()}
    results = {}
    if "snomed_top_level_depth" in tables and not replace:
        row = con.execute("SELECT COUNT(*) FROM snomed_top_level_depth").fetchone()
        results["snomed_top_level_depth"] = {
            "status": "exists",
            "rows": int(row[0]) if row else 0,
        }
    else:
        if "mrconso" not in tables or "mrrel" not in tables:
            raise RuntimeError("mrconso and mrrel are required before preparing derived tables.")

        con.execute("DROP TABLE IF EXISTS snomed_top_level_depth")
        snomed_count = con.execute(
            """
            SELECT COUNT(DISTINCT CODE)
            FROM mrconso
            WHERE SAB = 'SNOMEDCT_US'
              AND SUPPRESS = 'N'
              AND CODE IS NOT NULL
              AND CODE != ''
            """
        ).fetchone()[0]
        if int(snomed_count or 0) == 0:
            con.execute("CREATE TABLE snomed_top_level_depth (code VARCHAR, min_top_depth INTEGER)")
            results["snomed_top_level_depth"] = {"status": "created", "rows": 0}
        else:
            con.execute("DROP TABLE IF EXISTS mt4ds_active_snomed_atoms")
            con.execute(
                """
                CREATE TEMP TABLE mt4ds_active_snomed_atoms AS
                SELECT DISTINCT CODE, AUI
                FROM mrconso
                WHERE SAB = 'SNOMEDCT_US'
                  AND SUPPRESS = 'N'
                  AND CODE IS NOT NULL
                  AND CODE != ''
                  AND AUI IS NOT NULL
                  AND AUI != ''
                """
            )
            for ddl in (
                "CREATE INDEX IF NOT EXISTS idx_mt4ds_active_snomed_atoms_aui ON mt4ds_active_snomed_atoms(AUI)",
                "CREATE INDEX IF NOT EXISTS idx_mt4ds_active_snomed_atoms_code ON mt4ds_active_snomed_atoms(CODE)",
            ):
                try:
                    con.execute(ddl)
                except Exception as exc:
                    # Index creation failures (disk quota, locking, unsupported
                    # column type) used to be silently swallowed. Without these
                    # indexes, downstream hierarchy joins on AUI/CODE degrade to
                    # full table scans — multi-minute queries that should be
                    # sub-second. Log at WARNING so operators can detect the
                    # silently-unindexed DB before it ships.
                    logging.getLogger(__name__).warning(
                        "Failed to create SNOMED atoms index (%s): %s. "
                        "Hierarchy queries on mt4ds_active_snomed_atoms may "
                        "degrade to full table scans.",
                        ddl,
                        exc,
                    )
            active_codes = {
                row[0]
                for row in con.execute(
                    "SELECT DISTINCT CODE FROM mt4ds_active_snomed_atoms"
                ).fetchall()
            }
            edge_rows = con.execute(
                """
                SELECT DISTINCT child.CODE AS child_code, parent.CODE AS parent_code
                FROM mrrel r
                JOIN mt4ds_active_snomed_atoms child ON child.AUI = r.AUI1
                JOIN mt4ds_active_snomed_atoms parent ON parent.AUI = r.AUI2
                WHERE r.REL = 'PAR'
                  AND COALESCE(r.RELA, 'isa') IN ('isa', 'inverse_isa')
                  AND child.CODE != parent.CODE
                UNION
                SELECT DISTINCT child.CODE AS child_code, parent.CODE AS parent_code
                FROM mrrel r
                JOIN mt4ds_active_snomed_atoms parent ON parent.AUI = r.AUI1
                JOIN mt4ds_active_snomed_atoms child ON child.AUI = r.AUI2
                WHERE r.REL = 'CHD'
                  AND COALESCE(r.RELA, 'isa') IN ('isa', 'inverse_isa')
                  AND child.CODE != parent.CODE
                """
            ).fetchall()

            parent_to_children: dict[str, set[str]] = defaultdict(set)
            child_codes: set[str] = set()
            for child_code, parent_code in edge_rows:
                if not child_code or not parent_code or child_code == parent_code:
                    continue
                parent_to_children[str(parent_code)].add(str(child_code))
                child_codes.add(str(child_code))

            roots = sorted(active_codes - child_codes)
            queue = deque((code, 1) for code in roots)
            code_depths: dict[str, int] = {}
            while queue:
                code, depth = queue.popleft()
                current_depth = code_depths.get(code)
                if current_depth is not None and current_depth <= depth:
                    continue
                code_depths[code] = depth
                if depth >= 64:
                    continue
                for child_code in sorted(parent_to_children.get(code, ())):
                    queue.append((child_code, depth + 1))

            con.execute("CREATE TABLE snomed_top_level_depth (code VARCHAR, min_top_depth INTEGER)")
            if code_depths:
                con.executemany(
                    "INSERT INTO snomed_top_level_depth VALUES (?, ?)",
                    list(code_depths.items()),
                )
            con.execute("DROP TABLE IF EXISTS mt4ds_active_snomed_atoms")
            con.execute("CREATE INDEX IF NOT EXISTS idx_snomed_top_level_depth_code ON snomed_top_level_depth(code)")
            row = con.execute("SELECT COUNT(*) FROM snomed_top_level_depth").fetchone()
            results["snomed_top_level_depth"] = {
                "status": "created",
                "rows": int(row[0]) if row else 0,
            }

    if "cvx_metadata" in tables and not replace:
        row = con.execute("SELECT COUNT(*) FROM cvx_metadata").fetchone()
        results["cvx_metadata"] = {
            "status": "exists",
            "rows": int(row[0]) if row else 0,
        }
    else:
        con.execute("DROP TABLE IF EXISTS cvx_metadata")
        con.execute("CREATE TABLE cvx_metadata (code VARCHAR, group_name VARCHAR, short_name VARCHAR)")

        cvx_rows = []
        try:
            import urllib.request
            url = 'https://www2.cdc.gov/vaccines/iis/iisstandards/downloads/VG.txt'
            with urllib.request.urlopen(url, timeout=15) as response:
                lines = _read_capped(response).decode('utf-8').splitlines()
                for line in lines:
                    parts = line.split('|')
                    if len(parts) >= 4:
                        cvx_rows.append((parts[1].strip(), parts[3].strip(), parts[3].strip()))

            if cvx_rows:
                con.executemany("INSERT INTO cvx_metadata VALUES (?, ?, ?)", cvx_rows)
        except Exception as exc:
            # Don't fail the build if the CDC fetch is unavailable (offline
            # build, network down, format change). But log at warning so a
            # real bug (404, format change, malformed data) doesn't hide
            # silently — patient_friendly CVX lookups will fall back through
            # the hierarchy either way.
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "CVX metadata fetch failed (continuing without it): %s: %s",
                type(exc).__name__, exc,
            )

        row = con.execute("SELECT COUNT(*) FROM cvx_metadata").fetchone()
        results["cvx_metadata"] = {
            "status": "created",
            "rows": int(row[0]) if row else 0,
        }

    return results


def verify_duckdb(db_path: str | Path, *, sources: Iterable[str]) -> dict[str, object]:
    """Return a small structural verification report for a local DuckDB database."""
    import duckdb

    path = Path(db_path)
    source_tuple = tuple(sources)
    con = duckdb.connect(str(path), read_only=True)
    try:
        tables = {row[0] for row in con.execute("show tables").fetchall()}
        source_counts = {
            source: int(count)
            for source, count in con.execute(
                f"""
                SELECT SAB, COUNT(DISTINCT CODE)
                FROM mrconso
                WHERE SUPPRESS = 'N'
                  AND SAB IN ({','.join(['?'] * len(source_tuple))})
                GROUP BY SAB
                ORDER BY SAB
                """,
                list(source_tuple),
            ).fetchall()
        }
    finally:
        con.close()
    return {
        "db": str(path),
        "tables": sorted(tables),
        "has_required_tables": {"mrconso", "mrrel"}.issubset(tables),
        "has_snomed_top_level_depth": "snomed_top_level_depth" in tables,
        "source_counts": source_counts,
    }


def verify_umls_duckdb(
    db_path: str | Path,
    *,
    sources: Iterable[str] | None = None,
) -> dict[str, object]:
    """Verify a local UMLS DuckDB database using supported core sources by default."""
    return verify_duckdb(
        db_path,
        sources=DEFAULT_UMLS_VERIFY_SOURCES if sources is None else sources,
    )


def _find_rrf_sources(root: Path, name: str, *, staging_dir: Path, required: bool = True) -> tuple[Path, ...]:
    files = _find_rrf_files(root, name, staging_dir=staging_dir)
    if files:
        return files
    archive_files = _materialize_rrf_archive_members(root, name, staging_dir=staging_dir)
    if archive_files:
        return archive_files
    if required:
        raise RuntimeError(f"Could not find {name}, {name}.gz shards, or .nlm archive members under {root}")
    return ()


def _find_rrf_files(root: Path, name: str, *, staging_dir: Path) -> tuple[Path, ...]:
    direct = root / name
    if direct.exists():
        return (direct,)

    exact_matches = sorted(root.rglob(name))
    if exact_matches:
        return tuple(exact_matches)

    compressed_matches = sorted(
        path
        for path in root.rglob(f"{name}*.gz")
        if path.name == f"{name}.gz" or (path.name.startswith(f"{name}.") and path.name.endswith(".gz"))
    )
    if len(compressed_matches) == 1 and compressed_matches[0].name == f"{name}.gz":
        return tuple(compressed_matches)
    if compressed_matches:
        output_path = staging_dir / name
        _concatenate_gzip_files(compressed_matches, output_path)
        return (output_path,)
    return tuple(compressed_matches)


def _materialize_rrf_archive_members(root: Path, name: str, *, staging_dir: Path) -> tuple[Path, ...]:
    archives = sorted(path for path in root.rglob("*.nlm") if zipfile.is_zipfile(path))
    member_refs: list[tuple[Path, list[str]]] = []
    for archive_path in archives:
        with zipfile.ZipFile(archive_path) as archive:
            members = sorted(member for member in archive.namelist() if _is_rrf_archive_member(member, name))
        if members:
            member_refs.append((archive_path, members))
    if not member_refs:
        return ()

    output_path = staging_dir / name
    with output_path.open("wb") as output:
        for archive_path, members in member_refs:
            with zipfile.ZipFile(archive_path) as archive:
                for member in members:
                    with archive.open(member) as source:
                        if member.endswith(".gz"):
                            with gzip.GzipFile(fileobj=source) as decompressed:
                                shutil.copyfileobj(decompressed, output)
                        else:
                            shutil.copyfileobj(source, output)
    return (output_path,)


def _concatenate_gzip_files(paths: Iterable[Path], output_path: Path) -> None:
    with output_path.open("wb") as output:
        for path in paths:
            with gzip.open(path, "rb") as source:
                shutil.copyfileobj(source, output)


def _is_rrf_archive_member(member: str, name: str) -> bool:
    basename = Path(member).name
    if basename == name:
        return True
    return basename.startswith(f"{name}.") and basename.endswith(".gz")


def _create_rrf_table(
    con,
    *,
    table: str,
    sources: tuple[Path, ...],
    columns: tuple[str, ...],
    selected: tuple[str, ...],
) -> None:
    con.execute(
        f"""
        CREATE TABLE {table} AS
        SELECT {", ".join(selected)}
        FROM read_csv(
            {_sql_path_list(sources)},
            delim='|',
            header=false,
            columns={_duckdb_columns(columns)},
            quote='',
            escape='',
            nullstr='',
            all_varchar=true,
            strict_mode=false,
            max_line_size=10000000
        )
        """
    )


def _duckdb_columns(columns: tuple[str, ...]) -> str:
    return "{" + ", ".join(f"{column}: VARCHAR" for column in columns) + "}"


def _sql_path_list(paths: tuple[Path, ...]) -> str:
    # Defense-in-depth (Tier B hardening): paths come from _find_rrf_files
    # traversal of a CLI-supplied directory. An attacker with write access to
    # that directory could otherwise name a file with characters that break
    # out of the SQL string literal. Allow only safe path characters.
    _PATH_ALLOWED = re.compile(r"^[A-Za-z0-9._/\-:\\]+$")
    parts: list[str] = []
    for path in paths:
        path_str = str(path)
        if not _PATH_ALLOWED.match(path_str):
            raise RuntimeError(
                f"Refusing SQL path with disallowed characters: {path_str!r}"
            )
        parts.append(_sql_string(path_str))
    return "[" + ", ".join(parts) + "]"


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


# Cap HTTP JSON response size so a compromised UTS or CDC endpoint cannot OOM
# the build process by streaming an infinitely large response.
MAX_RESPONSE_BYTES = 50 * 1024 * 1024


def _read_capped(response) -> bytes:
    """Read at most MAX_RESPONSE_BYTES from `response` using streaming reads."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise RuntimeError(
                f"HTTP response exceeded {MAX_RESPONSE_BYTES} byte cap; aborting"
            )
        chunks.append(chunk)
    return b"".join(chunks)
