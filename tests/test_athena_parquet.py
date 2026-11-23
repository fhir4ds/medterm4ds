"""Tests for the athena parquet artifact family (FDD
.temp/athena_parquet_fdd.md, decisions A-F).

Hermetic: a tiny synthetic OMOP CSV fixture set drives the ETL
(scripts/build_athena_parquet.py, loaded by path), the snapshot
artifact machinery (services/athena_snapshot.py), the cache-refresh
``--athena`` stage, and the engine provider seam parity pin (table
provider vs parquet provider over the SAME tiny data — decision C).
No gate here downloads from HF or touches the 7GB real download.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import duckdb
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# The repo gitignores scripts/ (operator-local tooling — see
# test_hardening_scripts.py's precedent), so a clean checkout lacks
# scripts/build_athena_parquet.py. Skip at MODULE level: the ETL module
# is loaded by path below, which runs at collection time — a plain
# pytestmark skipif cannot prevent that (CI collection failure,
# 2026-11-16).
if not (REPO_ROOT / "scripts" / "build_athena_parquet.py").is_file():
    pytest.skip(
        "scripts/build_athena_parquet.py not present (clean "
        "checkout / sdist)",
        allow_module_level=True,
    )

_spec = importlib.util.spec_from_file_location(
    "build_athena_parquet",
    REPO_ROOT / "scripts" / "build_athena_parquet.py",
)
etl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(etl)

from medterm4ds.services import athena_snapshot as snap  # noqa: E402

REL = "2099.01.01.9"

SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
RXNORM_URI = "http://www.nlm.nih.gov/research/umls/rxnorm"
ACM = "urn:medterm4ds:athena-conceptmap"
TGT = "urn:athena:concept-id"

# Tiny OMOP CSV fixture set (the real download's tab-separated shape).
# Covers every equivalence class of the bridge: SNOMED S-row
# (equivalent), ICD10CM identity NULL-standard (equivalent — AJ1),
# RxNorm non-standard (relatedto), deprecated SNOMED S-row
# (unmatched), RxNorm Extension (excluded by the vocab filter), CPT4
# (excluded by decision B), plus an orphan relationship edge for the
# AG1 semi-join.
CONCEPT_ROWS = [
    # id, name, domain, vocab, class, std, code, start, end, invalid
    ("1001", "Type 2 diabetes mellitus", "Condition", "SNOMED",
     "Clinical Finding", "S", "44054006", "19700101", "20991231", ""),
    ("1002", "T2DM without complications", "Condition", "ICD10CM",
     "ICD10CM code", "", "E119", "19700101", "20991231", ""),
    ("1003", "metformin 500 MG Oral Tablet", "Drug", "RxNorm",
     "Ingredient", "", "860975", "19700101", "20991231", ""),
    ("1004", "Deprecated concept", "Condition", "SNOMED",
     "Clinical Finding", "S", "111000", "19700101", "20991231", "D"),
    ("1005", "metformin 500 MG [DSC]", "Drug", "RxNorm Extension",
     "Dose Form", "", "6809ext", "19700101", "20231231", ""),
    ("1006", "Office visit", "Procedure", "CPT4",
     "CPT4", "", "99213", "19700101", "20991231", ""),
]
ANCESTOR_ROWS = [
    # ancestor, descendant, min, max
    ("1001", "1002", "1", "1"),
    ("1001", "1001", "0", "0"),
    # orphan edge (9999 not in CONCEPT) — semi-join must drop it
    ("9999", "1001", "1", "2"),
]
RELATIONSHIP_ROWS = [
    # id1, id2, rel, start, end, invalid
    ("1001", "1003", "Has ingredient", "19700101", "20991231", ""),
    ("9999", "1003", "Orphan", "19700101", "20991231", ""),
]
VOCABULARY_ROWS = [
    # id, name, reference, version, concept_id
    ("SNOMED", "SNOMED CT", "snomed.org", "2099-01 SNOMED", "1"),
    ("ICD10CM", "ICD10CM", "cdc.gov", "2099", "2"),
    ("RxNorm", "RxNorm", "nlm.nih.gov", "RxNorm 2099", "3"),
    ("RxNorm Extension", "RxNorm Extension", "nlm.nih.gov", "2099-01", "4"),
    ("CPT4", "CPT4", "ama-assn.org", "2099", "5"),
]


def write_csvs(base: Path) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    (base / "CONCEPT.csv").write_text(
        "concept_id\tconcept_name\tdomain_id\tvocabulary_id\t"
        "concept_class_id\tstandard_concept\tconcept_code\t"
        "valid_start_date\tvalid_end_date\tinvalid_reason\n"
        + "".join("\t".join(r) + "\n" for r in CONCEPT_ROWS),
        encoding="utf-8",
    )
    (base / "CONCEPT_ANCESTOR.csv").write_text(
        "ancestor_concept_id\tdescendant_concept_id\t"
        "min_levels_of_separation\tmax_levels_of_separation\n"
        + "".join("\t".join(r) + "\n" for r in ANCESTOR_ROWS),
        encoding="utf-8",
    )
    (base / "CONCEPT_RELATIONSHIP.csv").write_text(
        "concept_id_1\tconcept_id_2\trelationship_id\t"
        "valid_start_date\tvalid_end_date\tinvalid_reason\n"
        + "".join("\t".join(r) + "\n" for r in RELATIONSHIP_ROWS),
        encoding="utf-8",
    )
    (base / "VOCABULARY.csv").write_text(
        "vocabulary_id\tvocabulary_name\tvocabulary_reference\t"
        "vocabulary_version\tvocabulary_concept_id\n"
        + "".join("\t".join(r) + "\n" for r in VOCABULARY_ROWS),
        encoding="utf-8",
    )
    return base


@pytest.fixture
def csv_dir(tmp_path) -> Path:
    return write_csvs(tmp_path / "extracted")


def _build(tmp_path, csv_dir, name="out", **kwargs):
    return etl.build_athena_parquet(
        csv_dir, tmp_path / name, release=REL, accept_licenses=True,
        **kwargs,
    )


class TestAthenaParquetEtl:
    def test_requires_accept_licenses(self, csv_dir, tmp_path, capsys):
        with pytest.raises(PermissionError, match="accept-licenses"):
            etl.build_athena_parquet(
                csv_dir, tmp_path / "out", release=REL,
            )
        # the licensing notice is echoed first (ingest-athena mirror)
        assert "licensing notice" in capsys.readouterr().err

    def test_manifest_and_layout(self, csv_dir, tmp_path):
        manifest = _build(tmp_path, csv_dir)
        out = tmp_path / "out"
        assert manifest["release"] == REL
        assert manifest["format"] == "athena_parquet_v1"
        assert manifest["cpt_excluded"] is True
        # source vocabulary version read FROM the vocabulary table
        assert manifest["vocabulary_versions"]["SNOMED"] == (
            "2099-01 SNOMED"
        )
        assert manifest["row_counts"] == {
            "concept": 5,  # 6 rows minus the CPT4 row
            "concept_ancestor": 2,  # 3 rows minus the orphan edge
            "concept_relationship": 1,  # 2 rows minus the orphan edge
            "vocabulary": 5,
        }
        assert "CPT4" not in manifest["vocabularies"]
        assert (out / "manifest.json").is_file()
        assert (out / "MD5SUMS").is_file()
        # concept partitioned by vocabulary_id; no CPT4 partition.
        # (Hive dir names are percent-encoded; DuckDB decodes on read.)
        parts = {
            p.name for p in (out / "concept").iterdir() if p.is_dir()
        }
        assert parts == {
            f"vocabulary_id={v}"
            for v in (
                "SNOMED", "ICD10CM", "RxNorm", "RxNorm%20Extension",
            )
        }
        for f in ("concept_ancestor.parquet",
                  "concept_relationship.parquet", "vocabulary.parquet"):
            assert (out / f).is_file()
        # MD5SUMS covers every data file, sorted, md5sum format
        lines = (out / "MD5SUMS").read_text().splitlines()
        listed = [line.split("  ", 1)[1] for line in lines]
        assert listed == sorted(listed)
        data_files = sorted(
            p.relative_to(out).as_posix()
            for p in out.rglob("*.parquet")
        )
        assert listed == data_files

    def test_cpt_excluded_by_default(self, csv_dir, tmp_path):
        _build(tmp_path, csv_dir)
        con = duckdb.connect(":memory:")
        try:
            cpt = con.execute(
                f"SELECT COUNT(*) FROM read_parquet("
                f"'{tmp_path / 'out' / 'concept'}/**/*.parquet', "
                "hive_partitioning=true) WHERE vocabulary_id IN "
                "('CPT','CPT4')"
            ).fetchone()[0]
        finally:
            con.close()
        assert cpt == 0

    def test_include_cpt_escape_hatch(self, csv_dir, tmp_path):
        manifest = _build(tmp_path, csv_dir, include_cpt=True)
        assert manifest["cpt_excluded"] is False
        assert manifest["row_counts"]["concept"] == 6
        assert "CPT4" in manifest["vocabularies"]
        assert (tmp_path / "out" / "concept" / "vocabulary_id=CPT4"
                ).is_dir()

    def test_content_scan_gate_fails_on_surviving_cpt(
        self, csv_dir, tmp_path,
    ):
        # Gate unit: a concept dir that (somehow) carries CPT rows must
        # fail the scan when CPT is excluded, pass when included.
        _build(tmp_path, csv_dir, include_cpt=True)
        bad = tmp_path / "out" / "concept"
        with pytest.raises(
            etl.AthenaParquetBuildError, match="content-scan gate",
        ):
            etl._content_scan_cpt(bad, include_cpt=False)
        etl._content_scan_cpt(bad, include_cpt=True)  # no raise

    def test_deterministic_output(self, csv_dir, tmp_path):
        _build(tmp_path, csv_dir, name="out1")
        _build(tmp_path, csv_dir, name="out2")
        a = tmp_path / "out1"
        b = tmp_path / "out2"
        assert (a / "MD5SUMS").read_text() == (b / "MD5SUMS").read_text()
        # manifests identical apart from the wall-clock built_at stamp
        man_a = json.loads((a / "manifest.json").read_text())
        man_b = json.loads((b / "manifest.json").read_text())
        man_a.pop("built_at"), man_b.pop("built_at")
        assert man_a == man_b
        # row order is stable across builds (natural keys)
        con = duckdb.connect(":memory:")
        try:
            for table, order in (
                ("concept_ancestor.parquet",
                 "ancestor_concept_id, descendant_concept_id"),
                ("concept_relationship.parquet",
                 "concept_id_1, relationship_id, concept_id_2"),
            ):
                seq_a = con.execute(
                    f"SELECT * FROM read_parquet('{a / table}') "
                    f"ORDER BY {order}"
                ).fetchall()
                seq_b = con.execute(
                    f"SELECT * FROM read_parquet('{b / table}') "
                    f"ORDER BY {order}"
                ).fetchall()
                assert seq_a == seq_b
            part = "vocabulary_id=SNOMED"
            fa = sorted((a / "concept" / part).glob("*.parquet"))[0]
            fb = sorted((b / "concept" / part).glob("*.parquet"))[0]
            seq_a = con.execute(
                f"SELECT * FROM read_parquet('{fa}') ORDER BY concept_id"
            ).fetchall()
            seq_b = con.execute(
                f"SELECT * FROM read_parquet('{fb}') ORDER BY concept_id"
            ).fetchall()
            assert seq_a == seq_b
        finally:
            con.close()

    def test_failed_build_leaves_existing_output(
        self, csv_dir, tmp_path, monkeypatch,
    ):
        out = tmp_path / "out"
        _build(tmp_path, csv_dir)
        (out / "sentinel").write_text("keep me")
        monkeypatch.setattr(
            etl, "_content_scan_cpt",
            lambda *a, **k: (_ for _ in ()).throw(
                etl.AthenaParquetBuildError("gate boom")
            ),
        )
        with pytest.raises(etl.AthenaParquetBuildError):
            _build(tmp_path, csv_dir)
        assert (out / "sentinel").read_text() == "keep me"
        assert not list(tmp_path.glob(".tmp-*"))  # staging cleaned


# -- Snapshot artifact machinery --------------------------------------


@pytest.fixture
def snapshot_base(csv_dir, tmp_path, monkeypatch) -> Path:
    """Build the ETL output once and bind it as the pinned snapshot
    via MEDTERM4DS_ATHENA_DIR (mock-free, real layout on disk)."""
    _build(tmp_path, csv_dir)
    base = tmp_path / "athena" / REL
    base.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(tmp_path / "out", base)
    monkeypatch.setenv("MEDTERM4DS_ATHENA_DIR", str(base))
    snap.reset_athena_snapshot_for_testing()
    yield base
    snap.reset_athena_snapshot_for_testing()


class TestAthenaParquetSnapshot:
    def test_absent_raises_absent(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "MEDTERM4DS_ATHENA_DIR",
            str(tmp_path / "athena" / "2099.01.01.9"),
        )
        with pytest.raises(snap.AthenaSnapshotAbsentError):
            snap.get_athena_snapshot()

    def test_manifest_release_mismatch_raises(
        self, csv_dir, tmp_path, monkeypatch,
    ):
        # Release identity is only checkable on the NON-override path
        # (a DIR override's manifest IS the release identity — the
        # terminology/associations binding contract).
        _build(tmp_path, csv_dir)
        base = tmp_path / "athena" / REL
        base.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(tmp_path / "out", base)
        manifest = json.loads((base / "manifest.json").read_text())
        manifest["release"] = "1998.01.01.1"
        (base / "manifest.json").write_text(json.dumps(manifest))
        monkeypatch.delenv("MEDTERM4DS_ATHENA_DIR", raising=False)
        monkeypatch.setenv("MEDTERM4DS_ATHENA_RELEASE", REL)
        monkeypatch.setattr(snap, "athena_root", lambda: tmp_path)
        snap.reset_athena_snapshot_for_testing()
        with pytest.raises(snap.AthenaSnapshotError, match="release"):
            snap.get_athena_snapshot()

    def test_manifest_format_drift_raises(self, snapshot_base):
        manifest = json.loads((snapshot_base / "manifest.json").read_text())
        manifest["format"] = "athena_parquet_v9.9"
        (snapshot_base / "manifest.json").write_text(
            json.dumps(manifest)
        )
        snap.reset_athena_snapshot_for_testing()
        with pytest.raises(snap.AthenaSnapshotError, match="format"):
            snap.get_athena_snapshot()

    def test_manifest_missing_cpt_attestation_raises(
        self, snapshot_base,
    ):
        manifest = json.loads((snapshot_base / "manifest.json").read_text())
        del manifest["cpt_excluded"]
        (snapshot_base / "manifest.json").write_text(
            json.dumps(manifest)
        )
        snap.reset_athena_snapshot_for_testing()
        with pytest.raises(snap.AthenaSnapshotError, match="cpt_excluded"):
            snap.get_athena_snapshot()

    def test_incomplete_files_raise(self, snapshot_base):
        (snapshot_base / "vocabulary.parquet").unlink()
        snap.reset_athena_snapshot_for_testing()
        with pytest.raises(snap.AthenaSnapshotError, match="incomplete"):
            snap.get_athena_snapshot()

    def test_views_mirror_ingest_schema_and_answer(
        self, csv_dir, snapshot_base, tmp_path,
    ):
        snapshot = snap.get_athena_snapshot()
        tables = {
            r[0] for r in snapshot.con.execute("SHOW TABLES").fetchall()
        }
        assert {
            "athena_concept", "athena_concept_ancestor",
            "athena_concept_relationship", "athena_vocabulary",
        } <= tables
        # read-only views, not table copies
        kinds = dict(
            snapshot.con.execute(
                "SELECT table_name, table_type FROM information_schema.tables"
            ).fetchall()
        )
        for t in ("athena_concept", "athena_vocabulary"):
            assert kinds[t] == "VIEW"

    def test_single_flight_and_reset(self, snapshot_base):
        assert snap.get_athena_snapshot() is snap.get_athena_snapshot()
        snap.reset_athena_snapshot_for_testing()
        assert snap.get_athena_snapshot() is not None

    def test_download_stages_then_swaps(self, csv_dir, tmp_path, monkeypatch):
        # Build a source snapshot; simulate the HF download by copying
        # it into snapshot_download's local_dir.
        _build(tmp_path, csv_dir)
        src = tmp_path / "out"
        calls = {}

        import huggingface_hub as hf

        def fake_snapshot_download(**kwargs):
            calls.update(kwargs)
            shutil.copytree(src, kwargs["local_dir"], dirs_exist_ok=True)

        monkeypatch.setattr(hf, "snapshot_download", fake_snapshot_download)
        root = tmp_path / "cache"
        monkeypatch.delenv("MEDTERM4DS_ATHENA_DIR", raising=False)
        monkeypatch.setenv("MEDTERM4DS_ATHENA_RELEASE", REL)
        base = snap.download(root)
        assert calls["repo_id"] == "fhir4ds/athena"
        assert (base / "manifest.json").is_file()

        # A FAILED download leaves the existing release untouched.
        (base / "sentinel").write_text("keep me")

        def boom(**kwargs):
            raise RuntimeError("network down")

        monkeypatch.setattr(hf, "snapshot_download", boom)
        with pytest.raises(RuntimeError, match="network down"):
            snap.download(root)
        assert (base / "sentinel").read_text() == "keep me"
        assert snap.is_present(base)  # old release still valid

        # A download landing INVALID content also leaves it untouched.
        def junk(**kwargs):
            Path(kwargs["local_dir"], "manifest.json").write_text("{}")

        monkeypatch.setattr(hf, "snapshot_download", junk)
        with pytest.raises(snap.AthenaSnapshotError):
            snap.download(root)
        assert (base / "sentinel").read_text() == "keep me"
        assert not list((tmp_path / "cache").glob(".tmp-*"))


class TestAthenaParquetCacheRefresh:
    def test_athena_only_refresh_does_not_fall_through(
        self, monkeypatch, tmp_path,
    ):
        """CR-001 mirror: --athena alone must not fall through to the
        legacy family refresh (keyed on `not terminology` AFTER
        auto-resolution — the CLI passes terminology=False)."""
        import medterm4ds.core.artifact_cache as ac
        import medterm4ds.services.athena_snapshot as snap_mod
        import medterm4ds.services.search as search_mod

        monkeypatch.setattr(search_mod, "_CACHE_REVISION_KEYED", True)
        downloaded = []
        monkeypatch.setattr(
            snap_mod, "download",
            lambda root: downloaded.append(root) or tmp_path,
        )

        import huggingface_hub

        monkeypatch.setattr(
            huggingface_hub, "snapshot_download",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("legacy family refresh must not run")
            ),
        )

        report = ac.cache_refresh(athena=True, terminology=False)
        assert downloaded, "athena download stage must run"
        assert report["layout"] == "athena"
        assert report["athena"]["repo_id"] == "fhir4ds/athena"
        assert "cache_dir" not in report and "families" not in report

    def test_split_and_athena_mutex(self, monkeypatch, tmp_path):
        import medterm4ds.core.artifact_cache as ac

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        with pytest.raises(RuntimeError, match="mutually exclusive"):
            ac.cache_refresh(split=True, athena=True)


# -- Engine provider-seam parity (decision C) -------------------------


def _make_engine_db(db_path: Path, *, with_athena_tables, csv_dir):
    """Minimal mrconso/mrrel engine DB; optionally also the athena_*
    tables via the real ingest (table provider)."""
    con = duckdb.connect(str(db_path))
    con.execute(
        """CREATE TABLE mrconso (
            CODE VARCHAR, TTY VARCHAR, STR VARCHAR, AUI VARCHAR,
            SUPPRESS VARCHAR, SAB VARCHAR, CUI VARCHAR
        )"""
    )
    con.execute(
        "INSERT INTO mrconso VALUES ("
        "'44054006','PT','Type 2 diabetes mellitus','A44054006',"
        "'N','SNOMEDCT_US','C0011847')"
    )
    con.execute(
        "CREATE TABLE mrrel (AUI1 VARCHAR, AUI2 VARCHAR, "
        "RELA VARCHAR, REL VARCHAR)"
    )
    con.execute("INSERT INTO mrrel VALUES ('a','b','isa','PAR')")
    if with_athena_tables:
        from medterm4ds.services.athena import (
            DEFAULT_VOCABULARIES,
            ingest_athena,
        )

        ingest_athena(
            con,
            csv_dir,
            # same content as the parquet build (CPT excluded)
            vocabularies=tuple(
                v for v in DEFAULT_VOCABULARIES if v != "CPT4"
            ),
            accept_licenses=True,
        )
    con.close()


def _client(db_path: Path):
    from starlette.testclient import TestClient

    from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app

    settings = FhirApiSettings(
        db_path=db_path,
        memory_profile="low",
        search_index_dir=str(db_path.parent / "nonexistent"),
        prepare_cache=False,
    )
    return TestClient(create_fhir_app(settings))


PARITY_CASES = [
    # (system_uri, code) covering every equivalence class
    (SNOMED_URI, "44054006"),   # S-row → equivalent
    (ICD10CM_URI, "E119"),      # identity vocab, NULL standard → equivalent
    (RXNORM_URI, "860975"),     # non-standard → relatedto
    (SNOMED_URI, "111000"),     # deprecated S-row → unmatched
    (RXNORM_URI, "6809ext"),    # RxNorm Extension → excluded (empty)
    (SNOMED_URI, "UNKNOWN"),    # unknown code → empty match
]


class TestAthenaParquetTranslateParity:
    """Both providers over the SAME tiny fixture data must produce
    IDENTICAL $translate answers (decision C: equivalence classes are
    provider-independent)."""

    @pytest.fixture(scope="class")
    def built(self, tmp_path_factory):
        tmp = tmp_path_factory.mktemp("athena-parity")
        csvs = write_csvs(tmp / "extracted")
        etl.build_athena_parquet(
            csvs, tmp / "out", release=REL, accept_licenses=True,
        )
        return tmp, csvs

    def _translate(self, client, system, code, *, extra=None):
        params = {
            "url": ACM, "system": system, "code": code,
            "targetsystem": TGT,
        }
        params.update(extra or {})
        resp = client.get("/fhir/ConceptMap/$translate", params=params)
        return resp.status_code, resp.json()

    def test_identical_answers_both_providers(
        self, built, monkeypatch,
    ):
        tmp, csvs = built
        db_tables = tmp / "tables.duckdb"
        db_bare = tmp / "bare.duckdb"
        _make_engine_db(
            db_tables, with_athena_tables=True, csv_dir=csvs,
        )
        _make_engine_db(db_bare, with_athena_tables=False, csv_dir=csvs)

        base = tmp / "athena" / REL
        base.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(tmp / "out", base)
        monkeypatch.setenv("MEDTERM4DS_ATHENA_DIR", str(base))
        snap.reset_athena_snapshot_for_testing()
        try:
            with _client(db_tables) as table_client, \
                    _client(db_bare) as parquet_client:
                for system, code in PARITY_CASES:
                    got_t = self._translate(table_client, system, code)
                    got_p = self._translate(parquet_client, system, code)
                    assert got_t == got_p, (system, code, got_t, got_p)
                # error taxonomy identical on both
                for extra in (
                    {"targetsystem": "http://example.com/other"},
                    {"reverse": "true"},
                ):
                    got_t = self._translate(
                        table_client, SNOMED_URI, "44054006", extra=extra,
                    )
                    got_p = self._translate(
                        parquet_client, SNOMED_URI, "44054006",
                        extra=extra,
                    )
                    assert got_t == got_p == (400, got_t[1])
                # the happy paths actually answered (not all-empty)
                _, body = self._translate(
                    table_client, SNOMED_URI, "44054006",
                )
                matches = [
                    p for p in body.get("parameter", [])
                    if p.get("name") == "match"
                ]
                assert matches
        finally:
            snap.reset_athena_snapshot_for_testing()

    def test_no_provider_keeps_400_remedy(self, built, monkeypatch):
        tmp, csvs = built
        db_bare = tmp / "bare2.duckdb"
        _make_engine_db(db_bare, with_athena_tables=False, csv_dir=csvs)
        monkeypatch.delenv("MEDTERM4DS_ATHENA_DIR", raising=False)
        monkeypatch.setenv("MEDTERM4DS_ATHENA_RELEASE", "2099.01.01.9")
        snap.reset_athena_snapshot_for_testing()
        try:
            with _client(db_bare) as client:
                resp = client.get(
                    "/fhir/ConceptMap/$translate",
                    params={
                        "url": ACM, "system": SNOMED_URI,
                        "code": "44054006", "targetsystem": TGT,
                    },
                )
            assert resp.status_code == 400
            assert resp.json()["issue"][0]["diagnostics"] == (
                "No athena_* tables in this database — run "
                "'medterm4ds data ingest-athena' to use the athena "
                "ConceptMap."
            )
        finally:
            snap.reset_athena_snapshot_for_testing()

    def test_invalid_snapshot_is_503_not_silent_400(
        self, built, monkeypatch,
    ):
        tmp, csvs = built
        db_bare = tmp / "bare3.duckdb"
        _make_engine_db(db_bare, with_athena_tables=False, csv_dir=csvs)
        base = tmp / "athena" / REL
        manifest = json.loads((base / "manifest.json").read_text())
        manifest["format"] = "athena_parquet_v0.1"
        (base / "manifest.json").write_text(json.dumps(manifest))
        monkeypatch.setenv("MEDTERM4DS_ATHENA_DIR", str(base))
        snap.reset_athena_snapshot_for_testing()
        try:
            with _client(db_bare) as client:
                resp = client.get(
                    "/fhir/ConceptMap/$translate",
                    params={
                        "url": ACM, "system": SNOMED_URI,
                        "code": "44054006", "targetsystem": TGT,
                    },
                )
            assert resp.status_code == 503
            assert "cache-refresh --athena" in (
                resp.json()["issue"][0]["diagnostics"]
            )
        finally:
            snap.reset_athena_snapshot_for_testing()
