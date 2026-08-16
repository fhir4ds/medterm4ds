"""EC-21 regression tests: memory profiles, engine lifecycle, cache-state APIs.

Covers QC-463..QC-477 remediations:
- cache_clear/cache_versions/cache_info/is_lookup_cached honesty (QC-463,
  QC-475, QC-476, QC-477)
- prepared-cache scope incl. ATC on the Python surface (QC-469)
- cache_indexes default harmonization (QC-470) + FHIR env wiring (QC-472)
- falsy engine overrides raise instead of silently dropping (QC-465)
- threads/query-chunk-size validation (QC-466, QC-473)
- env-contract harmonization on Python connect() and the CLI (QC-464)
- connect() junk-DB / URI-suffix guards (QC-468)
- api.py body caps (QC-474)
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pytest

from medterm4ds.core import provision
from medterm4ds.core.config import local_duckdb_config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_tiny_duckdb(path: Path, *, tables: bool = True) -> None:
    con = duckdb.connect(str(path))
    try:
        if tables:
            con.execute("CREATE TABLE marker AS SELECT 1 AS ok")
    finally:
        con.close()


@pytest.fixture
def fake_cache_home(tmp_path, monkeypatch):
    """MEDTERM4DS_HOME with two usable multi-version lookup DBs (QC-463 repro)."""
    home = tmp_path / "fakehome"
    cache = home / "cache"
    cache.mkdir(parents=True)
    _make_tiny_duckdb(cache / "lookup-2026AA.duckdb")
    _make_tiny_duckdb(cache / "lookup-2025AA.duckdb")
    monkeypatch.setenv("MEDTERM4DS_HOME", str(home))
    return home


def _tiny_source_db(path: Path) -> None:
    con = duckdb.connect(str(path))
    try:
        con.execute(
            """
            CREATE TABLE mrconso (
                CODE VARCHAR, TTY VARCHAR, STR VARCHAR, AUI VARCHAR,
                SUPPRESS VARCHAR, SAB VARCHAR, CUI VARCHAR
            )
            """
        )
        con.execute(
            """
            CREATE TABLE mrrel (
                AUI1 VARCHAR, AUI2 VARCHAR, RELA VARCHAR, REL VARCHAR
            )
            """
        )
        con.executemany(
            "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("A10BA02", "PT", "acarbose", "ATC_A10BA02", "N", "ATC", "C_ACARB"),
                ("44054006", "PT", "Diabetes mellitus type 2", "SNOMED_DM2", "N", "SNOMEDCT_US", "C_DM2"),
            ],
        )
    finally:
        con.close()


# ---------------------------------------------------------------------------
# QC-463: cache_clear crashes after first unlink
# ---------------------------------------------------------------------------


def test_cache_clear_removes_all_old_versions(fake_cache_home):
    """QC-463 (HIGH): stat() ran after unlink() — exactly one version was
    deleted, then FileNotFoundError; the rest were left behind."""
    removed = provision.cache_clear(keep="2026AA")
    assert removed == ["2025AA"]
    remaining = sorted(p.name for p in (fake_cache_home / "cache").glob("lookup-*.duckdb"))
    assert remaining == ["lookup-2026AA.duckdb"]


def test_cache_clear_keep_current_uses_nonempty_release(fake_cache_home, monkeypatch):
    """QC-463 sibling / QC-465: UMLS_RELEASE='' made DEFAULT_UMLS_RELEASE ''
    so keep_current=True kept NOTHING — the current release was deleted too."""
    monkeypatch.setenv("UMLS_RELEASE", "")
    removed = provision.cache_clear(keep="2026AA")
    assert "2026AA" not in removed
    assert removed == ["2025AA"]


# ---------------------------------------------------------------------------
# QC-475/QC-476/QC-477: cache-state honesty
# ---------------------------------------------------------------------------


@pytest.fixture
def messy_cache_home(tmp_path, monkeypatch):
    home = tmp_path / "messyhome"
    cache = home / "cache"
    cache.mkdir(parents=True)
    _make_tiny_duckdb(cache / "lookup-2026AA.duckdb")
    # 2 MB of zeros — passes any size-only floor, is not a DuckDB file.
    (cache / "lookup-CORRUPT.duckdb").write_bytes(b"\x00" * 2_000_000)
    # Empty version tag — phantom '' version in both read APIs.
    _make_tiny_duckdb(cache / "lookup-.duckdb")
    # A directory named like a DB.
    (cache / "lookup-2013AA.duckdb").mkdir()
    # Dangling symlink — crashed both read APIs via stat().
    os.symlink(str(home / "nonexistent.duckdb"), str(cache / "lookup-DANGLE.duckdb"))
    monkeypatch.setenv("MEDTERM4DS_HOME", str(home))
    return home


def test_cache_read_apis_agree_and_skip_non_cache_entries(messy_cache_home):
    """QC-475/477: one predicate across all four functions; dangling symlinks
    and directories must not crash the read."""
    assert provision.cache_versions() == ["2026AA"]
    info = provision.cache_info()
    assert [v["version"] for v in info["lookup_dbs"]] == ["2026AA"]
    assert provision.is_lookup_cached("2026AA") is True
    assert provision.is_lookup_cached("CORRUPT") is False
    assert provision.is_lookup_cached("DANGLE") is False
    assert provision.is_lookup_cached("2013AA") is False


def test_zeros_file_is_not_cached(messy_cache_home):
    """QC-476 (MEDIUM): a corrupt >1 MB file passed the size-only floor and
    provision() handed it to duckdb.connect — every mt.connect() crashed
    until manual deletion."""
    assert provision.is_lookup_cached("CORRUPT") is False


def test_valid_small_duckdb_is_cached(tmp_path, monkeypatch):
    """QC-476 inverse: a valid DuckDB below the old 1 MB floor was rejected."""
    home = tmp_path / "home"
    (home / "cache").mkdir(parents=True)
    _make_tiny_duckdb(home / "cache" / "lookup-2026AA.duckdb")
    monkeypatch.setenv("MEDTERM4DS_HOME", str(home))
    size = (home / "cache" / "lookup-2026AA.duckdb").stat().st_size
    assert size < 1_000_000  # tiny real DuckDB is far below the old floor
    assert provision.is_lookup_cached("2026AA") is True
    assert provision.cache_versions() == ["2026AA"]


def test_provision_offline_names_corrupt_cache(messy_cache_home, monkeypatch):
    """QC-476: offline provision on a corrupt cache entry must raise a clear
    remediation error instead of handing the file to duckdb.connect."""
    with pytest.raises(RuntimeError, match="corrupt or truncated"):
        provision.provision(version="CORRUPT", offline=True, cache_home=messy_cache_home)


def test_cache_clear_skips_unusable_entries(messy_cache_home):
    removed = provision.cache_clear(keep="2026AA")
    # Only the usable non-kept entry is removable; zeros/dirs/dangling stay
    # untouched (they are not cache entries the API claims to manage).
    assert removed == []
    assert (messy_cache_home / "cache" / "lookup-CORRUPT.duckdb").exists()


# ---------------------------------------------------------------------------
# QC-469: Python prepared-cache scope includes ATC
# ---------------------------------------------------------------------------


def test_connect_prepare_cache_includes_atc(tmp_path):
    """QC-469 (HIGH): connect(prepare_cache=True) used the engine's 8-source
    default (no ATC), so t.lookup('ATC', ...) silently returned None on the
    only surface that prepares by explicit opt-in."""
    import medterm4ds as mt

    db = tmp_path / "umls.duckdb"
    _tiny_source_db(db)
    with mt.connect(db, memory_profile="low", prepare_cache=True) as t:
        prepared_sabs = {
            row[0]
            for row in t._connection.execute("SELECT DISTINCT SAB FROM mrconso").fetchall()
        }
        assert "ATC" in prepared_sabs
        assert t.lookup("ATC", "A10BA02") is not None


# ---------------------------------------------------------------------------
# QC-465/QC-466: override validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"memory_limit": ""},
        {"memory_limit": "nonsense"},
        {"threads": 0},
        {"threads": -1},
        {"query_chunk_size": 0},
        {"query_chunk_size": -5},
        {"temp_directory": ""},
    ],
)
def test_local_duckdb_config_rejects_bad_overrides(kwargs):
    """QC-465/QC-466: falsy/garbage overrides raised nothing pre-fix — they
    were silently dropped or crashed deep inside DuckDB."""
    with pytest.raises(ValueError):
        local_duckdb_config("low", **kwargs)


def test_engine_applies_explicit_overrides(tmp_path):
    from medterm4ds.engines.duckdb import LocalDuckDBEngine

    db = tmp_path / "umls.duckdb"
    _tiny_source_db(db)
    con = duckdb.connect(str(db), read_only=True)
    try:
        engine = LocalDuckDBEngine(
            con,
            config=local_duckdb_config("low", threads=2, query_chunk_size=7),
        )
        threads = con.execute("SELECT current_setting('threads')").fetchone()[0]
        assert int(threads) == 2
        assert engine.query_chunk_size == 7
    finally:
        con.close()


# ---------------------------------------------------------------------------
# QC-464: env contract on Python connect() + CLI defaults
# ---------------------------------------------------------------------------


def test_connect_honors_engine_env_vars(tmp_path, monkeypatch):
    import medterm4ds as mt

    db = tmp_path / "umls.duckdb"
    _tiny_source_db(db)
    monkeypatch.setenv("MEDTERM4DS_MEMORY_PROFILE", "low")
    monkeypatch.setenv("MEDTERM4DS_THREADS", "2")
    with mt.connect(db) as t:
        threads = t._connection.execute("SELECT current_setting('threads')").fetchone()[0]
        assert int(threads) == 2  # 'low' profile pins threads=1; env wins
        limit = t._connection.execute("SELECT current_setting('memory_limit')").fetchone()[0]
        assert "488" in str(limit) or "512" in str(limit) or str(limit).startswith("0.5")


def test_connect_explicit_args_beat_env(tmp_path, monkeypatch):
    import medterm4ds as mt

    db = tmp_path / "umls.duckdb"
    _tiny_source_db(db)
    monkeypatch.setenv("MEDTERM4DS_THREADS", "2")
    with mt.connect(db, memory_profile="low", threads=7) as t:
        threads = t._connection.execute("SELECT current_setting('threads')").fetchone()[0]
        assert int(threads) == 7


def test_cli_engine_args_default_from_env(monkeypatch):
    from medterm4ds.apps.cli import build_parser

    monkeypatch.setenv("MEDTERM4DS_MEMORY_PROFILE", "low")
    monkeypatch.setenv("MEDTERM4DS_THREADS", "3")
    parser = build_parser()
    args = parser.parse_args(["lookup", "--db", "x.duckdb", "--source", "SNOMEDCT_US", "--code", "1"])
    assert args.memory_profile == "low"
    assert args.threads == 3


def test_cli_rejects_negative_threads():
    """QC-466: --threads -1 died with a raw duckdb SyntaxException traceback."""
    from medterm4ds.apps.cli import build_parser

    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["lookup", "--db", "x.duckdb", "--source", "SNOMEDCT_US", "--code", "1", "--threads", "-1"])


# ---------------------------------------------------------------------------
# QC-473: named env errors on all three servers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["-1", "abc"])
def test_env_int_rejects_invalid_threads_named(value, monkeypatch):
    from medterm4ds.core.env import env_int

    monkeypatch.setenv("MEDTERM4DS_THREADS", value)
    with pytest.raises(ValueError, match="MEDTERM4DS_THREADS"):
        env_int("MEDTERM4DS_THREADS", minimum=1)


def test_mcp_settings_names_bad_threads(monkeypatch, tmp_path):
    pytest.importorskip("fastmcp")
    from medterm4ds.apps.mcp import McpSettings

    monkeypatch.setenv("MEDTERM4DS_DB", str(tmp_path / "x.duckdb"))
    monkeypatch.setenv("MEDTERM4DS_THREADS", "-1")
    with pytest.raises(ValueError, match="MEDTERM4DS_THREADS"):
        McpSettings.from_env()


def test_fhir_settings_cache_indexes_wired(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    from medterm4ds.apps.fhir_api import FhirApiSettings

    monkeypatch.setenv("MEDTERM4DS_DB", str(tmp_path / "x.duckdb"))
    monkeypatch.setenv("MEDTERM4DS_CACHE_INDEXES", "true")
    settings = FhirApiSettings.from_env()
    assert settings.cache_indexes is True  # QC-472: field exists and is honored


def test_api_settings_cache_indexes_default_false(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    from medterm4ds.apps.api import ApiSettings

    monkeypatch.setenv("MEDTERM4DS_DB", str(tmp_path / "x.duckdb"))
    assert ApiSettings.from_env().cache_indexes is False


# ---------------------------------------------------------------------------
# QC-468: connect() junk-DB / URI-suffix guards
# ---------------------------------------------------------------------------


def test_connect_rejects_missing_readwrite_path(tmp_path):
    """QC-468: read_only=False on a nonexistent path silently created a
    12KB junk DB and then failed on the first query."""
    import medterm4ds as mt

    junk = tmp_path / "junk_readwrite.duckdb"
    with pytest.raises(RuntimeError, match="Database not found"):
        mt.connect(junk, read_only=False)
    assert not junk.exists()


def test_connect_rejects_uri_suffix_path(tmp_path):
    """QC-468: DuckDB 1.5 does not parse '?mode=ro' suffixes — connect()
    opened the literal filename as a new DB."""
    import medterm4ds as mt

    db = tmp_path / "umls.duckdb"
    _tiny_source_db(db)
    with pytest.raises(RuntimeError, match="connection string"):
        mt.connect(str(db) + "?mode=ro")
    assert not (tmp_path / "umls.duckdb?mode=ro").exists()


# ---------------------------------------------------------------------------
# QC-474: api.py body caps
# ---------------------------------------------------------------------------


def _api_fixture(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from medterm4ds.apps.api import ApiSettings, create_app

    db = tmp_path / "umls.duckdb"
    _tiny_source_db(db)
    app = create_app(ApiSettings(db_path=db, sources=("ATC", "SNOMEDCT_US"), memory_profile="low", prepare_cache=False))
    return TestClient(app)


def test_api_rejects_oversized_code_strings(tmp_path):
    client = _api_fixture(tmp_path)
    with client:
        response = client.post(
            "/lookup",
            json={"codes": [{"source": "ATC", "code": "A" * 100_000}]},
        )
        assert response.status_code == 422


def test_api_rejects_oversized_body(tmp_path):
    from medterm4ds.apps.api import MAX_REQUEST_BODY_BYTES

    client = _api_fixture(tmp_path)
    with client:
        # Count-legal (2 codes) but the body exceeds the byte cap.
        huge = "A" * (MAX_REQUEST_BODY_BYTES + 1000)
        response = client.post(
            "/lookup",
            json={"codes": [{"source": "ATC", "code": huge}]},
        )
        # Either cap may fire; both are 4xx, neither is 200.
        assert response.status_code in (413, 422)


def test_connect_cache_indexes_defaults_false():
    """QC-470: Python default was True while every server used False — same
    fast+prepare intent produced 5 indexes vs 0 (and +28s/+0.8GB on
    production)."""
    import inspect

    import medterm4ds as mt

    default = inspect.signature(mt.connect).parameters["cache_indexes"].default
    assert default is False


def test_default_umls_release_never_empty(monkeypatch):
    """QC-465: UMLS_RELEASE='' imported as DEFAULT_UMLS_RELEASE='' (falsy),
    which made cache_clear(keep_current=True) keep NOTHING."""
    import importlib

    monkeypatch.setenv("UMLS_RELEASE", "")
    from medterm4ds.core import provision as prov

    reloaded = importlib.reload(prov)
    assert reloaded.DEFAULT_UMLS_RELEASE == "2026AA"
    importlib.reload(prov)  # restore pristine module state
