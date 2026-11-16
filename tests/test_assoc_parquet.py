"""Parquet-first associations serving tests (FDD
.temp/athena_parquet_fdd.md decision E + companion schema note).

Hermetic: the SAME synthetic graph drives BOTH providers — the JSON
bundle (unchanged fallback) and a parquet layout derived from it
(long members table partitioned by member_system + by_cid/by_name
side tables). Parity pins assert identical answers for the resolution
chain, per-member filtering, ordering, ambiguity/honest-empty cases,
and displays.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import duckdb
import pytest

from medterm4ds.services import associations as assoc

REL = "2099.01.01.9"
FORMAT = assoc.ASSOCIATIONS_FORMAT

# Single source of truth for BOTH fixtures (the parity contract):
# anchors, mixed-system buckets, a leaf cid, an is_entry=0 member cid,
# a None-distance member, and hyphenated codes.
GRAPH = {
    "format": FORMAT,
    "version": "2099-01-01.0000",
    "by_cid": {
        "VAL-COND-SNOMED-88594005": "herpes simplex infection",
        "VAL-MED-RXNORM-2034": "acyclovir",
        "VAL-LAB-LOINC-24321-2": "Basic Metabolic Panel (2000)",
        "VAL-LAB-LOINC-LP14355-9": "Creatinine",
        "VAL-COND-SNOMED-33839006": "herpes simplex keratitis",
        "VAL-COND-ICD10CM-A00-A09": "intestinal infections",
        "VAL-MED-RXNORM-2035": "valacyclovir",
        "VAL-PROC-CPT-99213": "office visit",
        "VAL-SYMP-SNOMED-999": "orphan symptom",
    },
    "by_name": {
        "herpes simplex infection": "VAL-COND-SNOMED-88594005",
        "acyclovir": "VAL-MED-RXNORM-2034",
    },
    "concepts": {
        "herpes simplex infection": {
            "name": "herpes simplex infection",
            "buckets": {
                "lab": [
                    {"cid": "VAL-LAB-LOINC-24321-2", "name": "Basic Metabolic Panel (2000)", "provenance": "monitoring_recommendation", "distance": 1},
                    {"cid": "VAL-LAB-LOINC-LP14355-9", "name": "Creatinine", "provenance": "monitoring_recommendation", "distance": 0},
                ],
                "condition": [
                    {"cid": "VAL-COND-SNOMED-33839006", "name": "herpes simplex keratitis", "provenance": "disease_context", "distance": 0},
                    {"cid": "VAL-COND-ICD10CM-A00-A09", "name": "intestinal infections", "provenance": "comorbidity_section", "distance": 1},
                ],
                "medication": [
                    {"cid": "VAL-MED-RXNORM-2035", "name": "valacyclovir", "provenance": "direct_indication", "distance": 0},
                ],
            },
        },
        "acyclovir": {
            "name": "acyclovir",
            "buckets": {
                "lab": [
                    {"cid": "VAL-LAB-LOINC-24321-2", "name": "Basic Metabolic Panel (2000)", "provenance": "panel_cooccurrence", "distance": 0},
                ],
                "procedure": [
                    {"cid": "VAL-PROC-CPT-99213", "name": "office visit", "provenance": "dosage_section", "distance": None},
                ],
            },
        },
    },
}


def _member_system(cid: str) -> str:
    prefix = "-".join(cid.split("-")[:3])
    source = assoc.CID_PREFIX_TO_SOURCE[prefix]
    return assoc.ENGINE_TO_CANONICAL_SYSTEM.get(source, source)


def write_json_graph(gdir: Path) -> None:
    with gzip.open(gdir / "associations.json.gz", "wt") as fh:
        json.dump(GRAPH, fh)
    (gdir / "associations.meta.json").write_text(json.dumps({
        "format": FORMAT, "version": "2099-01-01.0000",
    }))


def write_parquet_graph(gdir: Path) -> None:
    """Emit the parquet layout derived from the SAME GRAPH source:
    members (partitioned by member_system), by_cid (with is_entry
    mirroring concepts-node existence), by_name."""
    pdir = gdir / "parquet"
    pdir.mkdir(parents=True, exist_ok=True)
    members = []
    for name, entry in GRAPH["concepts"].items():
        anchor_cid = GRAPH["by_name"][name]
        for bucket, rows in entry["buckets"].items():
            for m in rows:
                members.append((
                    anchor_cid, bucket,
                    m["cid"], _member_system(m["cid"]),
                    "-".join(m["cid"].split("-")[3:]), m["name"],
                    m["provenance"],
                    m["distance"] if m["distance"] is not None else None,
                    "[]",  # derivations JSON (unused by serving)
                ))
    con = duckdb.connect(":memory:")
    try:
        con.execute(
            """
            CREATE TABLE members (
                cid VARCHAR, bucket VARCHAR, member_cid VARCHAR,
                member_system VARCHAR, member_code VARCHAR,
                member_name VARCHAR, provenance VARCHAR,
                distance INTEGER, derivations VARCHAR
            )
            """
        )
        con.executemany(
            "INSERT INTO members VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            members,
        )
        con.execute(
            f"COPY (SELECT * FROM members ORDER BY cid, bucket, "
            f"member_cid) TO '{pdir / 'members'}' "
            "(FORMAT PARQUET, COMPRESSION ZSTD, "
            "PARTITION_BY (member_system))"
        )
        entry_names = set(GRAPH["concepts"])
        by_cid_rows = [
            (cid, name, name in entry_names)
            for cid, name in GRAPH["by_cid"].items()
        ]
        con.execute(
            "CREATE TABLE by_cid (cid VARCHAR, name VARCHAR, "
            "is_entry BOOLEAN)"
        )
        con.executemany(
            "INSERT INTO by_cid VALUES (?, ?, ?)", by_cid_rows,
        )
        con.execute(
            f"COPY by_cid TO '{pdir / 'by_cid.parquet'}' "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        con.execute(
            "CREATE TABLE by_name (name VARCHAR, cid VARCHAR)"
        )
        con.executemany(
            "INSERT INTO by_name VALUES (?, ?)",
            list(GRAPH["by_name"].items()),
        )
        con.execute(
            f"COPY by_name TO '{pdir / 'by_name.parquet'}' "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        con.close()


def write_release(
    root: Path, *, parquet: bool, manifest_parquet: bool | None = None,
) -> Path:
    base = root / "associations" / REL
    gdir = base / "graph" / REL
    gdir.mkdir(parents=True, exist_ok=True)
    write_json_graph(gdir)
    if parquet:
        write_parquet_graph(gdir)
    manifest = {
        "release": REL,
        "bundle_version": "2099-01-01.0000",
        "format": FORMAT,
        "counts": {"concepts": 2, "cid_entries": 9, "members": 7},
        "version_lock": {
            "graph": "2099-01-01.0000", "naming": "2099-01-01.0000",
            "locked": True,
        },
    }
    if manifest_parquet is None:
        manifest_parquet = parquet
    if manifest_parquet:
        manifest["parquet"] = True
    (base / "manifest.json").write_text(json.dumps(manifest))
    return base


@pytest.fixture
def parquet_release(tmp_path, monkeypatch):
    base = write_release(tmp_path, parquet=True)
    monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_DIR", str(base))
    monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_RELEASE", REL)
    assoc.reset_associations_graph_for_testing()
    yield base
    assoc.reset_associations_graph_for_testing()


@pytest.fixture
def json_release(tmp_path, monkeypatch):
    base = write_release(tmp_path, parquet=False, manifest_parquet=False)
    monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_DIR", str(base))
    monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_RELEASE", REL)
    assoc.reset_associations_graph_for_testing()
    yield base
    assoc.reset_associations_graph_for_testing()


def _serving_snapshot(g):
    """Everything the fhir_api serving path asks of a graph."""
    out = {}
    for cid in (
        "VAL-COND-SNOMED-88594005", "VAL-MED-RXNORM-2034",
        "VAL-SYMP-SNOMED-999",              # leaf — honest empty
        "VAL-LAB-LOINC-24321-2",            # member cid — honest empty
        "VAL-MED-RXNORM-DOES-NOT-EXIST",    # unknown — honest empty
    ):
        entry = g.entry_for_cid(cid)
        out[cid] = None if entry is None else {
            "name": entry["name"],
            "members": [
                (b, m["cid"], m["name"], m["provenance"], m["distance"])
                for b, m in sorted(
                    g.members(entry), key=lambda bm: g.sort_key(*bm),
                )
            ],
        }
    out["by_cid_display_fallback"] = g.by_cid.get("VAL-PROC-CPT-99213")
    out["by_name"] = dict(g.by_name)
    return out


class TestAssocParquetServing:
    def test_parquet_preferred_and_parity_with_json(
        self, tmp_path, monkeypatch,
    ):
        # Two separate release roots (same tmp dir would collide).
        p_base = write_release(tmp_path / "parquet", parquet=True)
        j_base = write_release(
            tmp_path / "json", parquet=False, manifest_parquet=False,
        )
        monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_RELEASE", REL)
        assoc.reset_associations_graph_for_testing()
        try:
            monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_DIR", str(p_base))
            parquet_graph = assoc.get_associations_graph()
            monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_DIR", str(j_base))
            assoc.reset_associations_graph_for_testing()
            json_graph = assoc.get_associations_graph()
        finally:
            assoc.reset_associations_graph_for_testing()
        assert isinstance(parquet_graph, assoc.ParquetAssociationsGraph)
        assert type(json_graph) is assoc.AssociationsGraph
        assert _serving_snapshot(parquet_graph) == _serving_snapshot(
            json_graph
        )

    def test_resolution_chain_anchor(self, parquet_release):
        g = assoc.get_associations_graph()
        entry = g.entry_for_cid("VAL-COND-SNOMED-88594005")
        assert entry is not None
        assert entry["name"] == "herpes simplex infection"

    def test_leaf_and_member_cids_honest_empty(self, parquet_release):
        g = assoc.get_associations_graph()
        assert g.entry_for_cid("VAL-SYMP-SNOMED-999") is None
        assert g.entry_for_cid("VAL-LAB-LOINC-24321-2") is None
        # display still available via by_cid (the fallback display path)
        assert g.by_cid["VAL-SYMP-SNOMED-999"] == "orphan symptom"

    def test_mixed_bucket_members_published_order(self, parquet_release):
        g = assoc.get_associations_graph()
        entry = g.entry_for_cid("VAL-COND-SNOMED-88594005")
        got = [b for b, _m in g.members(entry)]
        assert got == ["lab", "lab", "medication", "condition", "condition"]

    def test_per_member_target_filtering(self, parquet_release):
        from medterm4ds.engines.fhir import system_to_fhir_uri

        g = assoc.get_associations_graph()
        entry = g.entry_for_cid("VAL-COND-SNOMED-88594005")
        snomed = [
            m["cid"] for _b, m in g.members(entry)
            if system_to_fhir_uri(g.parse_cid(m["cid"])[0])
            == "http://snomed.info/sct"
        ]
        icd = [
            m["cid"] for _b, m in g.members(entry)
            if system_to_fhir_uri(g.parse_cid(m["cid"])[0])
            == "http://hl7.org/fhir/sid/icd-10-cm"
        ]
        assert snomed == ["VAL-COND-SNOMED-33839006"]
        assert icd == ["VAL-COND-ICD10CM-A00-A09"]

    def test_distance_none_last_within_bucket(self, parquet_release):
        g = assoc.get_associations_graph()
        entry = g.entry_for_cid("VAL-MED-RXNORM-2034")
        keys = [g.sort_key(b, m) for b, m in g.members(entry)]
        assert keys == sorted(keys)
        ordered = [m["cid"] for _b, m in g.members(entry)]
        # procedure (distance None) must rank after lab (distance 0)
        # regardless of member-system partition file layout.
        assert ordered.index("VAL-PROC-CPT-99213") > ordered.index(
            "VAL-LAB-LOINC-24321-2"
        )

    def test_members_deterministic_across_calls(self, parquet_release):
        g = assoc.get_associations_graph()
        entry = g.entry_for_cid("VAL-COND-SNOMED-88594005")
        assert g.members(entry) == g.members(entry)


class TestAssocParquetFallbackAndDrift:
    def test_json_fallback_when_parquet_absent(self, json_release):
        g = assoc.get_associations_graph()
        assert type(g) is assoc.AssociationsGraph
        assert g.entry_for_cid("VAL-COND-SNOMED-88594005") is not None

    def test_manifest_advertises_parquet_but_files_missing(
        self, tmp_path, monkeypatch,
    ):
        base = write_release(tmp_path, parquet=False, manifest_parquet=True)
        monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_DIR", str(base))
        monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_RELEASE", REL)
        with pytest.raises(
            assoc.AssociationsLayoutError, match="parquet",
        ):
            assoc.get_associations_graph()

    def test_missing_side_table_raises_loud(
        self, tmp_path, monkeypatch,
    ):
        base = write_release(tmp_path, parquet=True)
        (base / "graph" / REL / "parquet" / "by_name.parquet").unlink()
        monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_DIR", str(base))
        monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_RELEASE", REL)
        with pytest.raises(
            assoc.AssociationsLayoutError, match="by_name.parquet",
        ):
            assoc.get_associations_graph()

    def test_missing_members_column_raises_loud(
        self, tmp_path, monkeypatch,
    ):
        base = write_release(tmp_path, parquet=True)
        pdir = base / "graph" / REL / "parquet"
        # rewrite members without the provenance column
        con = duckdb.connect(":memory:")
        try:
            con.execute(
                f"CREATE TABLE m AS SELECT cid, bucket, member_cid, "
                f"member_name, distance FROM read_parquet("
                f"'{pdir / 'members'}/**/*.parquet', "
                "hive_partitioning=true)"
            )
            import shutil

            shutil.rmtree(pdir / "members")
            con.execute(
                f"COPY m TO '{pdir / 'members'}' (FORMAT PARQUET, "
                "COMPRESSION ZSTD, PARTITION_BY (bucket))"
            )
        finally:
            con.close()
        monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_DIR", str(base))
        monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_RELEASE", REL)
        with pytest.raises(
            assoc.AssociationsLayoutError, match="provenance",
        ):
            assoc.get_associations_graph()

    def test_unknown_bucket_in_parquet_raises_loud(
        self, tmp_path, monkeypatch,
    ):
        base = write_release(tmp_path, parquet=True)
        pdir = base / "graph" / REL / "parquet"
        con = duckdb.connect(":memory:")
        try:
            con.execute(
                f"CREATE TABLE m AS SELECT * FROM read_parquet("
                f"'{pdir / 'members'}/**/*.parquet', "
                "hive_partitioning=true)"
            )
            con.execute(
                "INSERT INTO m BY NAME SELECT cid, 'new_bucket' AS "
                "bucket, member_cid, member_system, member_code, "
                "member_name, provenance, distance, derivations "
                "FROM m WHERE cid = 'VAL-COND-SNOMED-88594005' LIMIT 1"
            )
            import shutil

            shutil.rmtree(pdir / "members")
            con.execute(
                f"COPY (SELECT * FROM m ORDER BY cid, bucket, "
                f"member_cid) TO '{pdir / 'members'}' "
                "(FORMAT PARQUET, COMPRESSION ZSTD, "
                "PARTITION_BY (member_system))"
            )
        finally:
            con.close()
        monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_DIR", str(base))
        monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_RELEASE", REL)
        assoc.reset_associations_graph_for_testing()
        try:
            g = assoc.get_associations_graph()
            entry = g.entry_for_cid("VAL-COND-SNOMED-88594005")
            with pytest.raises(
                assoc.AssociationsLayoutError, match="new_bucket",
            ):
                g.members(entry)
        finally:
            assoc.reset_associations_graph_for_testing()
