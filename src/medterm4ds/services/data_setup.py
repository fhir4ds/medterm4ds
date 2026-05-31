"""Download and build LocalLite terminology data."""

from __future__ import annotations

import json
import os
import shutil
import zipfile
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

RELEASES_URL = "https://uts-ws.nlm.nih.gov/releases"
DOWNLOAD_URL = "https://uts-ws.nlm.nih.gov/download"

_MRCONSO_COLUMNS = ("CUI", "LAT", "TS", "LUI", "STT", "SUI", "ISPREF", "AUI", "SAUI", "SCUI", "SDUI", "SAB", "TTY", "CODE", "STR", "SRL", "SUPPRESS", "CVF")
_MRREL_COLUMNS = ("CUI1", "AUI1", "STYPE1", "REL", "CUI2", "AUI2", "STYPE2", "RELA", "RUI", "SRUI", "SAB", "SL", "RG", "DIR", "SUPPRESS", "CVF")
_MRSAT_COLUMNS = ("CUI", "LUI", "SUI", "METAUI", "STYPE", "CODE", "ATUI", "SATUI", "ATN", "SAB", "ATV", "SUPPRESS", "CVF")


def download_release(
    *,
    output_dir: str | Path,
    api_key: str | None = None,
    release_type: str = "umls-full-release",
    current: bool = True,
    extract: bool = False,
) -> Path:
    """Download a UTS release zip using the NLM Release and Download APIs."""
    key = api_key or os.getenv("UMLS_API_KEY") or os.getenv("UTS_API_KEY")
    if not key:
        raise RuntimeError("A UMLS API key is required. Pass --api-key or set UMLS_API_KEY.")
    release = current_release(release_type=release_type, current=current)
    download_url = release.get("downloadUrl")
    file_name = release.get("fileName") or Path(str(download_url)).name
    if not download_url:
        raise RuntimeError(f"Release metadata did not include downloadUrl: {release}")
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
            archive.extractall(extract_dir)
    return output_path


def current_release(*, release_type: str, current: bool = True) -> dict:
    query = urlencode({"releaseType": release_type, "current": str(current).lower()})
    with urlopen(f"{RELEASES_URL}?{query}") as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))
    if isinstance(payload, list):
        if not payload:
            raise RuntimeError(f"No releases found for {release_type}.")
        return payload[0]
    if isinstance(payload, dict):
        return payload
    raise RuntimeError("Unexpected UTS release response.")


def build_duckdb_from_rrf(
    *,
    rrf_dir: str | Path,
    output_db: str | Path,
    replace: bool = False,
    batch_size: int = 100_000,
) -> Path:
    """Build the minimal LocalLite DuckDB tables from UMLS RRF files."""
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("DuckDB is required to build LocalLite data.") from exc

    source_dir = Path(rrf_dir)
    db_path = Path(output_db)
    if db_path.exists():
        if replace:
            db_path.unlink()
        else:
            raise RuntimeError(f"Output database exists: {db_path}")

    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE TABLE mrconso (CUI VARCHAR, AUI VARCHAR, SAB VARCHAR, TTY VARCHAR, CODE VARCHAR, STR VARCHAR, SUPPRESS VARCHAR)")
        con.execute("CREATE TABLE mrrel (AUI1 VARCHAR, AUI2 VARCHAR, REL VARCHAR, RELA VARCHAR)")
        con.execute("CREATE TABLE mrsat (CODE VARCHAR, SAB VARCHAR, ATN VARCHAR, ATV VARCHAR)")
        _load_rrf(
            con,
            table="mrconso",
            path=_find_rrf(source_dir, "MRCONSO.RRF"),
            columns=_MRCONSO_COLUMNS,
            selected=("CUI", "AUI", "SAB", "TTY", "CODE", "STR", "SUPPRESS"),
            batch_size=batch_size,
        )
        _load_rrf(
            con,
            table="mrrel",
            path=_find_rrf(source_dir, "MRREL.RRF"),
            columns=_MRREL_COLUMNS,
            selected=("AUI1", "AUI2", "REL", "RELA"),
            batch_size=batch_size,
        )
        mrsat = _maybe_find_rrf(source_dir, "MRSAT.RRF")
        if mrsat:
            _load_rrf(
                con,
                table="mrsat",
                path=mrsat,
                columns=_MRSAT_COLUMNS,
                selected=("CODE", "SAB", "ATN", "ATV"),
                batch_size=batch_size,
            )
        con.execute("CREATE INDEX IF NOT EXISTS idx_mrconso_sab_code ON mrconso(SAB, CODE)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_mrconso_aui ON mrconso(AUI)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_mrrel_aui1 ON mrrel(AUI1)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_mrrel_aui2 ON mrrel(AUI2)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_mrsat_ndc ON mrsat(SAB, ATN, ATV)")
    finally:
        con.close()
    return db_path


def verify_duckdb(db_path: str | Path, *, sources: Iterable[str]) -> dict[str, object]:
    """Return a small structural verification report for a LocalLite database."""
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
        "source_counts": source_counts,
    }


def _load_rrf(con, *, table: str, path: Path, columns: tuple[str, ...], selected: tuple[str, ...], batch_size: int) -> None:
    indexes = [columns.index(column) for column in selected]
    placeholders = ",".join(["?"] * len(selected))
    batch: list[tuple[str, ...]] = []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        for line in handle:
            parts = line.rstrip("\n").split("|")
            row = tuple(parts[index] for index in indexes)
            batch.append(row)
            if len(batch) >= batch_size:
                con.executemany(f"INSERT INTO {table} VALUES ({placeholders})", batch)
                batch.clear()
    if batch:
        con.executemany(f"INSERT INTO {table} VALUES ({placeholders})", batch)


def _find_rrf(root: Path, name: str) -> Path:
    path = _maybe_find_rrf(root, name)
    if path is None:
        raise RuntimeError(f"Could not find {name} under {root}")
    return path


def _maybe_find_rrf(root: Path, name: str) -> Path | None:
    direct = root / name
    if direct.exists():
        return direct
    matches = list(root.rglob(name))
    return matches[0] if matches else None
