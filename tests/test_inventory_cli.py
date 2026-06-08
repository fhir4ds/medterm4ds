from __future__ import annotations

import csv
import json
from pathlib import Path

import duckdb

from medterm4ds.apps.cli import main
from medterm4ds.core.config import local_duckdb_config
from medterm4ds.core.models import CodeRef
from medterm4ds.services.inventory import count_source_codes, iter_source_codes, normalize_sources


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
                ("208", "PT", "COVID-19 Vaccine", "CVX_208", "N", "CVX", "C_CVX"),
                ("208", "PT", "COVID-19 vaccine duplicate", "CVX_208_B", "N", "CVX", "C_CVX"),
                ("999", "PT", "Suppressed code", "CVX_999", "Y", "CVX", "C_SUPP"),
            ],
        )
    finally:
        con.close()


def _make_hierarchy_duckdb(path: Path) -> None:
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
                ("E11.9", "PT", "Type 2 diabetes mellitus", "ICD_E119", "N", "ICD10CM", "C_E119"),
                ("E11", "PT", "Type 2 diabetes mellitus", "ICD_E11", "N", "ICD10CM", "C_E11"),
                ("E00-E89", "PT", "Endocrine diseases", "ICD_E00", "N", "ICD10CM", "C_E00"),
            ],
        )
        con.executemany(
            "INSERT INTO mrrel VALUES (?, ?, ?, ?)",
            [
                ("ICD_E119", "ICD_E11", "isa", "PAR"),
                ("ICD_E11", "ICD_E00", "isa", "PAR"),
            ],
        )
    finally:
        con.close()


def test_memory_profiles_can_be_overridden():
    config = local_duckdb_config(
        "low",
        memory_limit="768MB",
        threads=2,
        query_chunk_size=250,
    )

    assert config.memory_limit == "768MB"
    assert config.threads == 2
    assert config.query_chunk_size == 250


def test_cli_data_build_duckdb_rejects_ambiguous_umls_local_output(tmp_path, capsys):
    status = main(
        [
            "data",
            "build-duckdb",
            "--rrf-dir",
            str(tmp_path),
            "--output-db",
            str(tmp_path / "umls_local.duckdb"),
            "--db-role",
            "current_candidate",
        ]
    )

    assert status == 2
    assert "Refusing ambiguous output DB name" in capsys.readouterr().err


def test_inventory_counts_and_streams_distinct_codes(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        assert normalize_sources("loinc, snomed, ICD10-CM") == ("LNC", "SNOMEDCT_US", "ICD10CM")
        assert count_source_codes(con, ["ICD10CM", "CVX"]) == {"CVX": 1, "ICD10CM": 1}
        codes = list(iter_source_codes(con, ["ICD10CM", "CVX"], fetch_size=1))
        resumed = list(
            iter_source_codes(
                con,
                ["ICD10CM", "CVX"],
                fetch_size=1,
                resume_after=CodeRef("ICD10CM", "E11.9"),
            )
        )
    finally:
        con.close()

    assert [(code.source, code.code) for code in codes] == [
        ("ICD10CM", "E11.9"),
        ("CVX", "208"),
    ]
    assert [(code.source, code.code) for code in resumed] == [("CVX", "208")]


def test_inventory_uses_prepared_best_atoms_active_only():
    con = duckdb.connect(database=":memory:")
    try:
        con.execute("CREATE SCHEMA mt4ds")
        con.execute(
            """
            CREATE TABLE mt4ds.best_atoms (
                source VARCHAR,
                code VARCHAR,
                aui VARCHAR,
                cui VARCHAR,
                tty VARCHAR,
                name VARCHAR,
                suppress VARCHAR,
                is_active BOOLEAN,
                rank INTEGER
            )
            """
        )
        con.executemany(
            "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("ICD10CM", "E11.9", "ICD_PT", "C_DIAB", "PT", "Type 2 diabetes mellitus", "N", True, 1),
                ("ICD10CM", "S1", "ICD_SUP", "C_SUP", "PT", "Suppressed only", "Y", False, 1),
                ("CVX", "208", "CVX_208", "C_CVX", "PT", "COVID-19 Vaccine", "N", True, 1),
            ],
        )

        counts = count_source_codes(con, ["ICD10CM", "CVX"])
        codes = list(iter_source_codes(con, ["ICD10CM", "CVX"], fetch_size=1))
        limited = list(iter_source_codes(con, ["ICD10CM", "CVX"], limit=1))
    finally:
        con.close()

    assert counts == {"CVX": 1, "ICD10CM": 1}
    assert [(code.source, code.code) for code in codes] == [
        ("ICD10CM", "E11.9"),
        ("CVX", "208"),
    ]
    assert [(code.source, code.code) for code in limited] == [("ICD10CM", "E11.9")]


def test_cli_writes_patient_friendly_conceptmap_jsonl(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    output_path = tmp_path / "conceptmap.jsonl"
    _make_duckdb(db_path)

    status = main(
        [
            "conceptmap",
            "patient-friendly",
            "--db",
            str(db_path),
            "--sources",
            "ICD10CM,CVX",
            "--output",
            str(output_path),
            "--memory-profile",
            "low",
            "--batch-size",
            "1",
        ]
    )

    assert status == 0
    rows = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [row["target_display"] for row in rows] == ["Diabetes", "COVID-19 Vaccine"]
    assert [row["relationship"] for row in rows] == ["equivalent", "not-translated"]


def test_cli_writes_patient_friendly_conceptmap_csv(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    output_path = tmp_path / "conceptmap.csv"
    _make_duckdb(db_path)

    status = main(
        [
            "conceptmap",
            "patient-friendly",
            "--db",
            str(db_path),
            "--sources",
            "ICD10CM",
            "--output",
            str(output_path),
            "--format",
            "csv",
            "--no-prepare-cache",
        ]
    )

    assert status == 0
    with output_path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert rows[0]["source"] == "ICD10CM"
    assert rows[0]["target_display"] == "Diabetes"


def test_cli_writes_patient_friendly_conceptmap_fhir_json(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    output_path = tmp_path / "conceptmap.json"
    _make_duckdb(db_path)

    status = main(
        [
            "conceptmap",
            "patient-friendly",
            "--db",
            str(db_path),
            "--sources",
            "ICD10CM,CVX",
            "--output",
            str(output_path),
            "--format",
            "fhir-json",
            "--batch-size",
            "1",
        ]
    )

    assert status == 0
    resource = json.loads(output_path.read_text(encoding="utf-8"))
    assert resource["resourceType"] == "ConceptMap"
    assert [group["source"] for group in resource["group"]] == [
        "http://hl7.org/fhir/sid/icd-10-cm",
        "http://hl7.org/fhir/sid/cvx",
    ]
    assert resource["group"][0]["element"][0]["target"][0]["display"] == "Diabetes"
    assert resource["group"][0]["element"][0]["target"][0]["equivalence"] == "equivalent"
    assert "relationship" not in resource["group"][0]["element"][0]["target"][0]


def test_cli_writes_mapping_conceptmap_fhir_json(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    output_path = tmp_path / "mapping.json"
    _make_duckdb(db_path)

    status = main(
        [
            "conceptmap",
            "mapping",
            "--db",
            str(db_path),
            "--sources",
            "ICD10CM,CVX",
            "--target-sources",
            "SNOMEDCT_US",
            "--output",
            str(output_path),
            "--format",
            "fhir-json",
            "--batch-size",
            "1",
        ]
    )

    assert status == 0
    resource = json.loads(output_path.read_text(encoding="utf-8"))
    assert resource["resourceType"] == "ConceptMap"
    assert resource["url"] == "urn:medterm4ds:ConceptMap:mapping"
    assert resource["group"][0]["source"] == "http://hl7.org/fhir/sid/icd-10-cm"
    assert resource["group"][0]["target"] == "http://snomed.info/sct"
    target = resource["group"][0]["element"][0]["target"][0]
    assert target["code"] == "44054006"
    assert target["display"] == "Diabetes mellitus type 2"
    assert target["equivalence"] == "equivalent"
    assert any(
        extension["url"].endswith("/matched-via")
        for extension in target["extension"]
    )


def test_cli_writes_mapping_conceptmap_jsonl(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    output_path = tmp_path / "mapping.jsonl"
    _make_duckdb(db_path)

    status = main(
        [
            "conceptmap",
            "mapping",
            "--db",
            str(db_path),
            "--sources",
            "ICD10CM,CVX",
            "--target-sources",
            "SNOMEDCT_US",
            "--output",
            str(output_path),
            "--format",
            "jsonl",
            "--limit",
            "2",
            "--batch-size",
            "1",
            "--checkpoint-every",
            "1",
        ]
    )

    assert status == 0
    rows = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [(row["source"], row["code"], row["target_code"]) for row in rows] == [
        ("ICD10CM", "E11.9", "44054006")
    ]
    checkpoint = json.loads((tmp_path / "mapping.jsonl.checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["complete"] is True
    assert checkpoint["metadata"]["command"] == "conceptmap mapping"


def test_cli_lookup_prints_json(tmp_path, capsys):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    status = main(
        [
            "lookup",
            "--db",
            str(db_path),
            "--source",
            "ICD10-CM",
            "--code",
            "E11.9",
            "--code",
            "NOPE",
            "--memory-profile",
            "low",
        ]
    )

    assert status == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["results"][0] == {
        "source": "ICD10CM",
        "code": "E11.9",
        "name": "Type 2 diabetes mellitus",
        "cui": "C_DIAB",
        "aui": "ICD_E119",
        "tty": "PT",
        "suppress": "N",
    }
    assert payload["results"][1] == {
        "source": "ICD10CM",
        "code": "NOPE",
        "name": None,
        "cui": None,
        "aui": None,
        "tty": None,
        "suppress": None,
    }


def test_cli_lookup_writes_jsonl(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    output_path = tmp_path / "lookup.jsonl"
    _make_duckdb(db_path)

    status = main(
        [
            "lookup",
            "--db",
            str(db_path),
            "--source",
            "ICD10CM",
            "--code",
            "E11.9",
            "--source",
            "CVX",
            "--code",
            "208",
            "--output",
            str(output_path),
        ]
    )

    assert status == 0
    rows = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [(row["source"], row["code"], row["name"]) for row in rows] == [
        ("ICD10CM", "E11.9", "Type 2 diabetes mellitus"),
        ("CVX", "208", "COVID-19 Vaccine"),
    ]


def test_cli_hierarchy_prints_json(tmp_path, capsys):
    db_path = tmp_path / "umls.duckdb"
    _make_hierarchy_duckdb(db_path)

    status = main(
        [
            "hierarchy",
            "ancestors",
            "--db",
            str(db_path),
            "--source",
            "ICD10-CM",
            "--code",
            "E11.9",
            "--max-depth",
            "2",
            "--memory-profile",
            "low",
        ]
    )

    assert status == 0
    payload = json.loads(capsys.readouterr().out)
    assert [(row["target_code"], row["relationship"], row["depth"]) for row in payload["results"]] == [
        ("E11", "ancestor", 1),
        ("E00-E89", "ancestor", 2),
    ]


def test_cli_hierarchy_writes_csv(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    output_path = tmp_path / "children.csv"
    _make_hierarchy_duckdb(db_path)

    status = main(
        [
            "hierarchy",
            "children",
            "--db",
            str(db_path),
            "--source",
            "ICD10CM",
            "--code",
            "E11",
            "--output",
            str(output_path),
        ]
    )

    assert status == 0
    with output_path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert rows[0]["code"] == "E11"
    assert rows[0]["target_code"] == "E11.9"
    assert rows[0]["relationship"] == "child"


def test_cli_map_prints_json(tmp_path, capsys):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    status = main(
        [
            "map",
            "--db",
            str(db_path),
            "--source",
            "ICD10-CM",
            "--code",
            "E11.9",
            "--target-source",
            "SNOMED",
            "--memory-profile",
            "low",
        ]
    )

    assert status == 0
    payload = json.loads(capsys.readouterr().out)
    assert [(row["target_source"], row["target_code"], row["match_type"]) for row in payload["results"]] == [
        ("SNOMEDCT_US", "44054006", "same_cui")
    ]


def test_cli_map_writes_jsonl(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    output_path = tmp_path / "map.jsonl"
    _make_duckdb(db_path)

    status = main(
        [
            "map",
            "--db",
            str(db_path),
            "--source",
            "ICD10CM",
            "--code",
            "E11.9",
            "--source",
            "CVX",
            "--code",
            "208",
            "--target-source",
            "SNOMEDCT_US",
            "--output",
            str(output_path),
        ]
    )

    assert status == 0
    rows = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [(row["source"], row["code"], row["target_code"]) for row in rows] == [
        ("ICD10CM", "E11.9", "44054006")
    ]


def test_cli_sources_prints_json(tmp_path, capsys):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    status = main(
        [
            "sources",
            "--db",
            str(db_path),
            "--sources",
            "ICD10CM,CVX",
            "--memory-profile",
            "low",
        ]
    )

    assert status == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["results"] == [
        {"source": "CVX", "code_count": 1, "atom_count": 2},
        {"source": "ICD10CM", "code_count": 1, "atom_count": 1},
    ]


def test_cli_sample_codes_writes_csv(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    output_path = tmp_path / "samples.csv"
    _make_duckdb(db_path)

    status = main(
        [
            "sample-codes",
            "--db",
            str(db_path),
            "--sources",
            "ICD10CM,CVX",
            "--per-source",
            "1",
            "--output",
            str(output_path),
        ]
    )

    assert status == 0
    with output_path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert [(row["source"], row["code"]) for row in rows] == [
        ("CVX", "208"),
        ("ICD10CM", "E11.9"),
    ]


def test_cli_code_ttys_prints_json(tmp_path, capsys):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    status = main(
        [
            "code-ttys",
            "--db",
            str(db_path),
            "--source",
            "CVX",
            "--code",
            "208",
        ]
    )

    assert status == 0
    payload = json.loads(capsys.readouterr().out)
    assert [row["aui"] for row in payload["results"]] == ["CVX_208", "CVX_208_B"]
    assert [row["tty"] for row in payload["results"]] == ["PT", "PT"]


def test_cli_search_names_filters_by_tty(tmp_path, capsys):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    status = main(
        [
            "search-names",
            "--db",
            str(db_path),
            "--query",
            "diabetes",
            "--sources",
            "ICD10CM,MEDLINEPLUS",
            "--tty",
            "MH",
            "--limit",
            "5",
        ]
    )

    assert status == 0
    payload = json.loads(capsys.readouterr().out)
    assert [(row["source"], row["code"], row["match_type"]) for row in payload["results"]] == [
        ("MEDLINEPLUS", "D_DIAB", "exact")
    ]


def test_cli_bulk_lookup_writes_checkpointed_jsonl(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    output_path = tmp_path / "bulk_lookup.jsonl"
    _make_duckdb(db_path)

    status = main(
        [
            "bulk",
            "lookup",
            "--db",
            str(db_path),
            "--sources",
            "ICD10CM,CVX",
            "--output",
            str(output_path),
            "--batch-size",
            "1",
            "--checkpoint-every",
            "1",
            "--memory-profile",
            "low",
        ]
    )

    assert status == 0
    rows = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [(row["source"], row["code"], row["name"]) for row in rows] == [
        ("ICD10CM", "E11.9", "Type 2 diabetes mellitus"),
        ("CVX", "208", "COVID-19 Vaccine"),
    ]
    checkpoint = json.loads((tmp_path / "bulk_lookup.jsonl.checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["complete"] is True
    assert checkpoint["metadata"]["command"] == "bulk lookup"


def test_cli_bulk_patient_friendly_writes_csv(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    output_path = tmp_path / "bulk_friendly.csv"
    _make_duckdb(db_path)

    status = main(
        [
            "bulk",
            "patient-friendly",
            "--db",
            str(db_path),
            "--sources",
            "ICD10CM,CVX",
            "--output",
            str(output_path),
            "--format",
            "csv",
            "--batch-size",
            "1",
            "--no-prepare-cache",
        ]
    )

    assert status == 0
    with output_path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert [row["name"] for row in rows] == ["Diabetes", "COVID-19 Vaccine"]


def test_cli_bulk_map_writes_jsonl(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    output_path = tmp_path / "bulk_map.jsonl"
    _make_duckdb(db_path)

    status = main(
        [
            "bulk",
            "map",
            "--db",
            str(db_path),
            "--sources",
            "ICD10CM,CVX",
            "--target-sources",
            "SNOMED",
            "--output",
            str(output_path),
            "--format",
            "jsonl",
            "--batch-size",
            "1",
        ]
    )

    assert status == 0
    rows = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [(row["source"], row["code"], row["target_source"], row["target_code"]) for row in rows] == [
        ("ICD10CM", "E11.9", "SNOMEDCT_US", "44054006")
    ]


def test_cli_bulk_hierarchy_writes_jsonl(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    output_path = tmp_path / "bulk_hierarchy.jsonl"
    _make_hierarchy_duckdb(db_path)

    status = main(
        [
            "bulk",
            "hierarchy",
            "--db",
            str(db_path),
            "--sources",
            "ICD10CM",
            "--direction",
            "parents",
            "--output",
            str(output_path),
            "--format",
            "jsonl",
            "--batch-size",
            "2",
        ]
    )

    assert status == 0
    rows = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [(row["code"], row["target_code"], row["relationship"]) for row in rows] == [
        ("E11", "E00-E89", "parent"),
        ("E11.9", "E11", "parent"),
    ]


def test_cli_resumes_jsonl_from_existing_output(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    output_path = tmp_path / "conceptmap.jsonl"
    _make_duckdb(db_path)

    first_status = main(
        [
            "conceptmap",
            "patient-friendly",
            "--db",
            str(db_path),
            "--sources",
            "ICD10CM,CVX",
            "--output",
            str(output_path),
            "--limit",
            "1",
            "--batch-size",
            "1",
            "--checkpoint-every",
            "1",
        ]
    )
    assert first_status == 0
    first_rows = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [(row["source"], row["code"]) for row in first_rows] == [("ICD10CM", "E11.9")]

    checkpoint = json.loads((tmp_path / "conceptmap.jsonl.checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["rows"] == 1
    assert checkpoint["last_source"] == "ICD10CM"
    assert checkpoint["last_code"] == "E11.9"

    second_status = main(
        [
            "conceptmap",
            "patient-friendly",
            "--db",
            str(db_path),
            "--sources",
            "ICD10CM,CVX",
            "--output",
            str(output_path),
            "--resume",
            "--batch-size",
            "1",
        ]
    )
    assert second_status == 0

    rows = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [(row["source"], row["code"]) for row in rows] == [
        ("ICD10CM", "E11.9"),
        ("CVX", "208"),
    ]
    assert [row["target_display"] for row in rows] == ["Diabetes", "COVID-19 Vaccine"]


def test_cli_resumes_csv_without_rewriting_header(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    output_path = tmp_path / "conceptmap.csv"
    _make_duckdb(db_path)

    first_status = main(
        [
            "conceptmap",
            "patient-friendly",
            "--db",
            str(db_path),
            "--sources",
            "ICD10CM,CVX",
            "--output",
            str(output_path),
            "--format",
            "csv",
            "--limit",
            "1",
            "--checkpoint-every",
            "1",
        ]
    )
    assert first_status == 0

    second_status = main(
        [
            "conceptmap",
            "patient-friendly",
            "--db",
            str(db_path),
            "--sources",
            "ICD10CM,CVX",
            "--output",
            str(output_path),
            "--format",
            "csv",
            "--resume",
        ]
    )
    assert second_status == 0

    with output_path.open(encoding="utf-8", newline="") as file:
        lines = file.readlines()
    assert sum(1 for line in lines if line.startswith("source,")) == 1

    with output_path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert [(row["source"], row["code"]) for row in rows] == [
        ("ICD10CM", "E11.9"),
        ("CVX", "208"),
    ]
