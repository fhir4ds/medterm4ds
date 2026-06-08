from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from medterm4ds.engines.duckdb.prepared import (
    PATIENT_FRIENDLY_POLICY_VERSION,
    PREPARED_SCHEMA_VERSION,
)

ROOT = Path(__file__).resolve().parents[1]


def _make_duckdb(path: Path) -> None:
    con = duckdb.connect(str(path))
    try:
        con.execute(
            """
            CREATE TABLE mrconso (
                CODE VARCHAR,
                TTY VARCHAR,
                STR VARCHAR,
                AUI VARCHAR,
                SUPPRESS VARCHAR,
                SAB VARCHAR,
                CUI VARCHAR
            )
            """
        )
        con.execute(
            """
            CREATE TABLE mrrel (
                AUI1 VARCHAR,
                AUI2 VARCHAR,
                RELA VARCHAR,
                REL VARCHAR
            )
            """
        )
        con.execute(
            """
            CREATE TABLE mrsat (
                CODE VARCHAR,
                SAB VARCHAR,
                ATN VARCHAR,
                ATV VARCHAR
            )
            """
        )
        con.executemany(
            "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("E11.9", "PT", "Type 2 diabetes mellitus", "ICD_E119", "N", "ICD10CM", "C_DIAB"),
                ("44054006", "PT", "Diabetes mellitus type 2", "SNOMED_DIAB", "N", "SNOMEDCT_US", "C_DIAB"),
                ("D_DIAB", "MH", "Diabetes", "MP_DIAB", "N", "MEDLINEPLUS", "C_DIAB"),
                ("208", "PT", "COVID-19 vaccine", "CVX_208", "N", "CVX", "C_CVX"),
            ],
        )
    finally:
        con.close()


def _env() -> dict[str, str]:
    env = dict(os.environ)
    pythonpath = str(ROOT / "src")
    medterm_src = "/mnt/d/medterm/src"
    env["PYTHONPATH"] = f"{pythonpath}:{medterm_src}:{env.get('PYTHONPATH', '')}"
    return env


def test_parity_script_writes_json_and_markdown(tmp_path):
    if not Path("/mnt/d/medterm/src").exists():
        pytest.skip("medterm baseline checkout is not available")
    db_path = tmp_path / "umls.duckdb"
    json_path = tmp_path / "parity.json"
    markdown_path = tmp_path / "parity.md"
    csv_path = tmp_path / "parity.csv"
    _make_duckdb(db_path)

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "compare_patient_friendly_parity.py"),
            "--db",
            str(db_path),
            "--sources",
            "ICD10CM",
            "--per-source",
            "1",
            "--sample-mode",
            "tty",
            "--per-tty",
            "1",
            "--compare-batch-size",
            "2",
            "--no-prepare-cache",
            "--output-json",
            str(json_path),
            "--output-md",
            str(markdown_path),
            "--output-csv",
            str(csv_path),
        ],
        cwd=ROOT,
        env=_env(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["summary"] == {"match": 1}
    assert report["classifications"] == {}
    assert report["db_role"] == "unknown"
    assert "prepared_schema_version" in report
    assert "patient_friendly_policy_version" in report
    assert "crosswalk_edges" in report["prepared_tables"]
    assert isinstance(report["missing_prepared_tables"], list)
    assert isinstance(report["schema_errors"], list)
    assert report["sample_mode"] == "tty"
    assert report["compare_batch_size"] == 2
    assert report["cases"][0]["source"] == "ICD10CM"
    assert "Parity Report" in markdown_path.read_text(encoding="utf-8")
    parity_row = list(csv.DictReader(csv_path.open(encoding="utf-8")))[0]
    assert parity_row["status"] == "match"
    assert "classification" in parity_row


def test_source_parity_runner_writes_index(tmp_path):
    if not Path("/mnt/d/medterm/src").exists():
        pytest.skip("medterm baseline checkout is not available")
    db_path = tmp_path / "umls.duckdb"
    work_dir = tmp_path / "source_parity"
    _make_duckdb(db_path)

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_patient_friendly_parity_matrix.py"),
            "--db",
            str(db_path),
            "--sources",
            "ICD10CM",
            "--per-source",
            "1",
            "--compare-batch-size",
            "2",
            "--timeout-seconds",
            "60",
            "--work-dir",
            str(work_dir),
            "--no-prepare-cache",
        ],
        cwd=ROOT,
        env=_env(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    index = json.loads((work_dir / "index.json").read_text(encoding="utf-8"))
    assert index["summary"] == {"pass": 1}
    assert index["classifications"] == {}
    assert index["db_role"] == "unknown"
    assert "prepared_schema_version" in index
    assert "patient_friendly_policy_version" in index
    assert "crosswalk_edges" in index["prepared_tables"]
    assert isinstance(index["missing_prepared_tables"], list)
    assert isinstance(index["schema_errors"], list)
    assert index["source_statuses"] == {"ICD10CM": "pass"}
    assert (work_dir / "icd10cm.csv").exists()
    index_row = list(csv.DictReader((work_dir / "index.csv").open(encoding="utf-8")))[0]
    assert index_row["status"] == "pass"
    assert index_row["classifications"] == "{}"


def test_acceptance_script_exercises_cli_outputs(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    work_dir = tmp_path / "acceptance"
    report_path = tmp_path / "acceptance.json"
    _make_duckdb(db_path)

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_cli_acceptance.py"),
            "--db",
            str(db_path),
            "--sources",
            "ICD10CM,CVX",
            "--limit",
            "2",
            "--partial-limit",
            "1",
            "--fhir-limit",
            "1",
            "--work-dir",
            str(work_dir),
            "--output-json",
            str(report_path),
            "--no-prepare-cache",
        ],
        cwd=ROOT,
        env=_env(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["db_role"] == "unknown"
    assert "prepared_schema_version" in report
    assert "patient_friendly_policy_version" in report
    assert "crosswalk_edges" in report["prepared_tables"]
    assert isinstance(report["missing_prepared_tables"], list)
    assert isinstance(report["schema_errors"], list)
    assert {check["name"]: check["status"] for check in report["checks"]} == {
        "jsonl_resume": "pass",
        "csv": "pass",
        "fhir_json_r4": "pass",
        "lookup_cli": "pass",
        "map_cli": "pass",
        "hierarchy_cli": "skip",
    }
    assert (work_dir / "acceptance.jsonl").exists()
    assert (work_dir / "acceptance.csv").exists()
    assert (work_dir / "acceptance.fhir.json").exists()
    assert (work_dir / "lookup.json").exists()
    assert (work_dir / "map.json").exists()


def test_bulk_validation_and_mapping_quality_scripts(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    work_dir = tmp_path / "bulk_validation"
    validation_path = tmp_path / "bulk_validation.json"
    quality_path = tmp_path / "mapping_quality.json"
    quality_csv_path = tmp_path / "mapping_review_cases.csv"
    _make_duckdb(db_path)

    validation = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_bulk_validation.py"),
            "--db",
            str(db_path),
            "--work-dir",
            str(work_dir),
            "--output-json",
            str(validation_path),
            "--limit",
            "1",
            "--batch-size",
            "1",
            "--prepare-cache",
        ],
        cwd=ROOT,
        env=_env(),
        text=True,
        capture_output=True,
        check=False,
    )
    quality = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "review_mapping_quality.py"),
            "--db",
            str(db_path),
            "--pairs",
            "ICD10CM:SNOMEDCT_US",
            "--per-source",
            "1",
            "--output-json",
            str(quality_path),
            "--output-csv",
            str(quality_csv_path),
        ],
        cwd=ROOT,
        env=_env(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert validation.returncode == 0, validation.stderr
    validation_report = json.loads(validation_path.read_text(encoding="utf-8"))
    assert {trial["status"] for trial in validation_report["trials"]} == {"pass"}
    assert "crosswalk_edges" in validation_report["prepared_tables"]
    assert isinstance(validation_report["missing_prepared_tables"], list)
    assert isinstance(validation_report["schema_errors"], list)
    assert validation_report["query_chunk_size"] == 5000
    assert (work_dir / "icd10cm_to_snomed.jsonl").exists()

    assert quality.returncode == 0, quality.stderr
    quality_report = json.loads(quality_path.read_text(encoding="utf-8"))
    assert "crosswalk_edges" in quality_report["prepared_tables"]
    assert isinstance(quality_report["missing_prepared_tables"], list)
    assert isinstance(quality_report["schema_errors"], list)
    assert quality_report["reviews"][0]["source"] == "ICD10CM"
    assert quality_report["reviews"][0]["match_types"] == {"same_cui": 1}
    assert list(csv.DictReader(quality_csv_path.open(encoding="utf-8"))) == []


def test_materialize_patient_friendly_script_writes_report(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    report_path = tmp_path / "materialize_patient_friendly.json"
    _make_duckdb(db_path)

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "materialize_patient_friendly.py"),
            "--db",
            str(db_path),
            "--db-role",
            "synthetic",
            "--release-version",
            "2026AA",
            "--source-archive",
            str(tmp_path / "umls-2026AA.zip"),
            "--sources",
            "ICD10CM",
            "--chunk-size",
            "1",
            "--prepare-schema",
            "--output-json",
            str(report_path),
        ],
        cwd=ROOT,
        env=_env(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "pass"
    assert report["db_role"] == "synthetic"
    assert report["manifest_db_role"] == "synthetic"
    assert report["umls_release"] == "2026AA"
    assert report["source_archive"] == str(tmp_path / "umls-2026AA.zip")
    assert report["requested_umls_release"] == "2026AA"
    assert report["requested_source_archive"] == str(tmp_path / "umls-2026AA.zip")
    assert report["missing_prepared_tables"] == []
    assert report["schema_errors"] == []
    assert report["prepared_tables"]["crosswalk_edges"]["exists"] is True
    assert report["materialize_report"]["source_count"] == 1
    assert report["materialize_report"]["inputs"] == 1
    assert report["materialize_report"]["resolutions"] == 1
    assert report["materialize_report"]["friendly_resolutions"] == 1
    assert report["materialize_report"]["original_fallbacks"] == 0
    assert report["materialize_report"]["missing_resolutions"] == 0
    assert report["materialize_report"]["resolution_coverage"] == 1.0
    assert report["materialize_report"]["match_types"] == {"exact": 1}

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute(
            """
            SELECT name, friendly_source
            FROM mt4ds.patient_friendly_resolutions
            WHERE source = 'ICD10CM' AND code = 'E11.9'
            """
        ).fetchone()
    finally:
        con.close()
    assert row == ("Diabetes", "MEDLINEPLUS")


def test_benchmark_patient_friendly_resolutions_script_writes_report(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    report_path = tmp_path / "pf_resolution_benchmark.json"
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE SCHEMA mt4ds")
        con.execute(
            """
            CREATE TABLE mt4ds.prepare_manifest (
                key VARCHAR,
                value VARCHAR,
                updated_at TIMESTAMP
            )
            """
        )
        con.executemany(
            "INSERT INTO mt4ds.prepare_manifest VALUES (?, ?, current_timestamp)",
            [
                ("prepared_schema_version", PREPARED_SCHEMA_VERSION),
                ("patient_friendly_policy_version", PATIENT_FRIENDLY_POLICY_VERSION),
                ("umls_release", "synthetic"),
            ],
        )
        con.execute(
            """
            CREATE TABLE mt4ds.patient_friendly_resolutions (
              source VARCHAR,
              code VARCHAR,
              name VARCHAR,
              friendly_source VARCHAR,
              match_type VARCHAR,
              match_depth INTEGER,
              technical_name VARCHAR,
              selected_candidate_id BIGINT,
              policy_version VARCHAR,
              umls_release VARCHAR,
              prepared_schema_version VARCHAR,
              generated_at TIMESTAMP
            )
            """
        )
        con.executemany(
            """
            INSERT INTO mt4ds.patient_friendly_resolutions
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, current_timestamp)
            """,
            [
                (
                    "ICD10CM",
                    "E11.9",
                    "Diabetes",
                    "MEDLINEPLUS",
                    "broader",
                    1,
                    "Type 2 diabetes mellitus",
                    1,
                    PATIENT_FRIENDLY_POLICY_VERSION,
                    "synthetic",
                    PREPARED_SCHEMA_VERSION,
                ),
                (
                    "ICD10CM",
                    "J00",
                    "Common Cold",
                    "MEDLINEPLUS",
                    "broader",
                    1,
                    "Acute nasopharyngitis",
                    2,
                    PATIENT_FRIENDLY_POLICY_VERSION,
                    "synthetic",
                    PREPARED_SCHEMA_VERSION,
                ),
            ],
        )
    finally:
        con.close()

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "benchmark_patient_friendly_resolutions.py"),
            "--db",
            str(db_path),
            "--db-role",
            "synthetic",
            "--sources",
            "ICD10CM",
            "--limit",
            "2",
            "--chunk-size",
            "1",
            "--output-json",
            str(report_path),
        ],
        cwd=ROOT,
        env=_env(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["db_role"] == "synthetic"
    assert report["prepared_schema_version"] == PREPARED_SCHEMA_VERSION
    assert report["patient_friendly_policy_version"] == PATIENT_FRIENDLY_POLICY_VERSION
    assert report["query_chunk_size"] == 1
    assert report["prepared_tables"]["patient_friendly_resolutions"]["exists"] is True
    assert "crosswalk_edges" in report["missing_prepared_tables"]
    assert report["schema_errors"]
    assert report["summary"] == {"hits": 2, "inputs": 2, "missing": 0}


def test_local_patient_friendly_benchmark_writes_metadata_report(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    report_path = tmp_path / "local_pf_benchmark.json"
    _make_duckdb(db_path)

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "benchmark_local_duckdb_patient_friendly.py"),
            "--db",
            str(db_path),
            "--db-role",
            "synthetic",
            "--sources",
            "ICD10CM",
            "--sizes",
            "1",
            "--memory-limit",
            "",
            "--output-json",
            str(report_path),
        ],
        cwd=ROOT,
        env=_env(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["db_role"] == "synthetic"
    assert report["db_path"] == str(db_path)
    assert report["threads"] is None
    assert report["query_chunk_size"] == 5000
    assert "crosswalk_edges" in report["prepared_tables"]
    assert isinstance(report["missing_prepared_tables"], list)
    assert isinstance(report["schema_errors"], list)
    assert report["results"][0]["size_actual"] == 1


def test_download_umls_release_requires_db_role_for_build(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "download_umls_release.py"),
            "--build",
            "--archive",
            str(tmp_path / "missing.zip"),
            "--output-db",
            str(tmp_path / "umls.duckdb"),
        ],
        cwd=ROOT,
        env=_env(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "--db-role is required" in result.stderr


def test_download_umls_release_requires_release_for_unlabeled_build(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "download_umls_release.py"),
            "--build",
            "--db-role",
            "synthetic",
            "--archive",
            str(tmp_path / "umls.zip"),
            "--output-db",
            str(tmp_path / "umls.duckdb"),
        ],
        cwd=ROOT,
        env=_env(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "--release-version is required" in result.stderr


def test_download_umls_release_rejects_missing_archive(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "download_umls_release.py"),
            "--archive",
            str(tmp_path / "missing.zip"),
        ],
        cwd=ROOT,
        env=_env(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "Archive not found" in result.stderr


def test_download_umls_release_rejects_release_archive_mismatch(tmp_path):
    archive = tmp_path / "umls-2026AA.zip"
    archive.write_text("not a real zip", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "download_umls_release.py"),
            "--build",
            "--db-role",
            "current_candidate",
            "--release-version",
            "2025AB",
            "--archive",
            str(archive),
            "--output-db",
            str(tmp_path / "umls_2025ab.duckdb"),
        ],
        cwd=ROOT,
        env=_env(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "does not match archive-inferred release" in result.stderr


def test_download_umls_release_rejects_ambiguous_umls_local_output(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "download_umls_release.py"),
            "--build",
            "--db-role",
            "current_candidate",
            "--release-version",
            "2026AA",
            "--archive",
            str(tmp_path / "umls-2026AA.zip"),
            "--output-db",
            str(tmp_path / "umls_local.duckdb"),
        ],
        cwd=ROOT,
        env=_env(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "Refusing ambiguous output DB name" in result.stderr


def test_download_umls_release_rejects_existing_output_without_replace(tmp_path):
    output_db = tmp_path / "umls_2026aa.duckdb"
    output_db.write_text("existing", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "download_umls_release.py"),
            "--build",
            "--db-role",
            "current_candidate",
            "--release-version",
            "2026AA",
            "--archive",
            str(tmp_path / "umls-2026AA.zip"),
            "--output-db",
            str(output_db),
        ],
        cwd=ROOT,
        env=_env(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "Output database exists" in result.stderr
