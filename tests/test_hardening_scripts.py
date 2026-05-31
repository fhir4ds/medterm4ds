from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

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
            "--no-prepare-cache",
            "--output-json",
            str(json_path),
            "--output-md",
            str(markdown_path),
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
    assert report["cases"][0]["source"] == "ICD10CM"
    assert "Parity Report" in markdown_path.read_text(encoding="utf-8")


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
    assert (work_dir / "icd10cm_to_snomed.jsonl").exists()

    assert quality.returncode == 0, quality.stderr
    quality_report = json.loads(quality_path.read_text(encoding="utf-8"))
    assert quality_report["reviews"][0]["source"] == "ICD10CM"
    assert quality_report["reviews"][0]["match_types"] == {"same_cui": 1}
