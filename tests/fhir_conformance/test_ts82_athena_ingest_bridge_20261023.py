"""TS-82 Athena ingestion + ConceptMap bridge (2026-10-23).

Campaign ATHENA VOCABULARY SUPPORT, stage 1 (+1.5 converter).
FRESH EVIDENCE — loader + converter exercised this run against a
synthetic mini Athena directory; design note
docs/plans/athena-vocabulary-support-design.md (approved 2026-10-23).
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

pytest.importorskip("medterm4ds.services.athena")

from medterm4ds.services.athena import (  # noqa: E402
    ATHENA_VOCAB_TO_FHIR,
    CONCEPT_MAP_URL,
    build_concept_map,
    ingest_athena,
    iter_thin_concept_map_rows,
    licensing_notice,
)

MINI = None  # injected per-fixture


@pytest.fixture(scope="module")
def ingested(tmp_path_factory, athena_mini_dir):
    db = tmp_path_factory.mktemp("ts82") / "athena.duckdb"
    con = duckdb.connect(str(db))
    result = ingest_athena(
        con, str(athena_mini_dir), accept_licenses=True,
        licenses_accepted_at="2026-10-23T18:00:00+00:00",
    )
    yield con, result
    con.close()


class TestLoader:
    """Stage 1 — athena_* tables + provenance."""

    def test_a10_tables_created(self, ingested):
        con, _ = ingested
        names = {
            r[0]
            for r in con.execute("SHOW TABLES").fetchall()
        }
        assert {
            "athena_concept", "athena_concept_ancestor",
            "athena_concept_relationship", "athena_vocabulary",
            "athena_meta",
        } <= names

    def test_a11_license_gate_fail_loud(self, tmp_path, athena_mini_dir):
        con = duckdb.connect(str(tmp_path / "gate.duckdb"))
        with pytest.raises(PermissionError, match="accept"):
            ingest_athena(con, str(athena_mini_dir), accept_licenses=False)
        con.close()

    def test_a12_provenance_block(self, ingested):
        con, _ = ingested
        row = con.execute(
            "SELECT loader_version, source_dir, accepted_licenses_at, "
            "vocabularies FROM athena_meta"
        ).fetchone()
        assert row[0] == "1"
        assert "athena-mini" in row[1]
        assert row[2]  # acceptance recorded
        assert "SNOMED" in row[3]

    def test_a13_dates_typed(self, ingested):
        con, _ = ingested
        t = con.execute(
            "SELECT typeof(valid_start_date) FROM athena_concept LIMIT 1"
        ).fetchone()[0]
        assert t == "DATE"

    def test_a14_idempotent_rerun(self, ingested, athena_mini_dir):
        con, first = ingested
        second = ingest_athena(
            con, str(athena_mini_dir), accept_licenses=True,
            licenses_accepted_at="2026-10-23T19:00:00+00:00",
        )
        assert second.row_counts == first.row_counts

    def test_a15_missing_dir_fail_loud(self, tmp_path):
        con = duckdb.connect(str(tmp_path / "miss.duckdb"))
        with pytest.raises(FileNotFoundError):
            ingest_athena(
                con, tmp_path / "nowhere", accept_licenses=True
            )
        con.close()


class TestConverter:
    """Stage 1.5 — the fhir4ds VD join artifact."""

    def test_b10_resource_shape(self, ingested):
        con, _ = ingested
        cm = build_concept_map(con)
        assert cm["resourceType"] == "ConceptMap"
        assert cm["url"] == CONCEPT_MAP_URL
        assert cm["status"] == "active"
        assert cm["group"]

    def test_b11_group_per_vocabulary(self, ingested):
        con, _ = ingested
        cm = build_concept_map(con)
        sources = {g["source"] for g in cm["group"]}
        assert sources == {
            "http://snomed.info/sct",
            "http://www.nlm.nih.gov/research/umls/rxnorm",
        }
        assert all(
            g["target"] == "urn:athena:concept-id"
            for g in cm["group"]
        )

    def test_b12_equivalence_classes(self, ingested):
        con, _ = ingested
        cm = build_concept_map(con)
        equiv = {}
        for g in cm["group"]:
            for e in g["element"]:
                equiv[e["code"]] = e["target"][0]["equivalence"]
        assert equiv["44054006"] == "equal"
        assert equiv["6809"] == "equal"
        assert equiv["99999001"] == "unmatched"  # deprecated row

    def test_b13_version_provenance(self, ingested):
        con, _ = ingested
        cm = build_concept_map(con)
        for g in cm["group"]:
            assert g.get("extension"), g["source"]

    def test_b14_vocabulary_filter(self, ingested):
        con, _ = ingested
        cm = build_concept_map(con, vocabularies=("SNOMED",))
        assert len(cm["group"]) == 1
        assert cm["group"][0]["source"] == "http://snomed.info/sct"

    def test_b15_correspondence_table_complete(self):
        for vocab in ("SNOMED", "RxNorm", "LOINC", "ICD10CM",
                      "ICD10PCS", "CPT4", "HCPCS", "ATC"):
            assert vocab in ATHENA_VOCAB_TO_FHIR


class TestLicensing:
    """Stage 1 — the notice table."""

    def test_c10_notice_names_all(self):
        notice = licensing_notice()
        for v in ("SNOMED", "CPT4", "LOINC"):
            assert v in notice

    def test_c11_cpt_calls_out_agreement(self):
        assert "UMLS" in licensing_notice(("CPT4",))


class TestThinForm:
    """Element-per-resource NDJSON — the fhir4ds VD join contract
    (coordination note 2026-10-23: scalar code+system ON pairs)."""

    def test_b20_one_row_per_mapping(self, ingested):
        con, result = ingested
        rows = list(iter_thin_concept_map_rows(con))
        assert len(rows) == result.row_counts["concept"]

    def test_b21_scalar_joinable_shape(self, ingested):
        con, _ = ingested
        for row in iter_thin_concept_map_rows(con):
            assert row["resourceType"] == "ConceptMap"
            assert len(row["group"]) == 1
            group = row["group"][0]
            assert isinstance(group["source"], str)
            assert len(group["element"]) == 1
            element = group["element"][0]
            assert isinstance(element["code"], str)
            assert len(element["target"]) == 1
            assert element["target"][0]["code"].isdigit()

    def test_b22_equivalence_consistent_with_monolithic(
        self, ingested
    ):
        con, _ = ingested
        thin = {}
        for row in iter_thin_concept_map_rows(con):
            g = row["group"][0]
            e = g["element"][0]
            thin[(g["source"], e["code"])] = e["target"][0][
                "equivalence"
            ]
        mono = {}
        for g in build_concept_map(con)["group"]:
            for e in g["element"]:
                mono[(g["source"], e["code"])] = e["target"][0][
                    "equivalence"
                ]
        assert thin == mono
