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
import uuid
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


def write_parquet_graph(base: Path) -> dict:
    """Emit the REAL published parquet mirror shape (live-verified
    against fhir4ds/associations v2026.11.3): a repo-ROOT ``parquet/``
    tree — members hive-partitioned by member_system with UUID
    filenames, plus by_cid/by_cid_multi/by_name side tables — derived
    from the SAME GRAPH source. Returns the parquet_mirror manifest
    facts for the release manifest."""
    pdir = base / "parquet"
    pdir.mkdir(parents=True, exist_ok=True)
    members = []
    for name, entry in GRAPH["concepts"].items():
        for bucket, rows in entry["buckets"].items():
            for m in rows:
                members.append((
                    name, bucket,  # members.cid = ANCHOR NAME (real
                    # contract — the JSON concepts key, not a VAL cid)
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
        # Published filenames are UUID-based (e.g. <uuid>-0.parquet) —
        # rename DuckDB's data_N.parquet to pin the glob's
        # filename-agnosticity.
        for pf in (pdir / "members").rglob("*.parquet"):
            pf.rename(pf.parent / f"{uuid.uuid4()}-0.parquet")
        # by_cid: (cid, display) — all cids, no is_entry (real schema)
        con.execute(
            "CREATE TABLE by_cid (cid VARCHAR, display VARCHAR)"
        )
        con.executemany(
            "INSERT INTO by_cid VALUES (?, ?)",
            list(GRAPH["by_cid"].items()),
        )
        con.execute(
            f"COPY by_cid TO '{pdir / 'by_cid.parquet'}' "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        # by_cid_multi: (cid, concepts JSON list) — multi-name index
        con.execute(
            "CREATE TABLE by_cid_multi (cid VARCHAR, concepts VARCHAR)"
        )
        con.executemany(
            "INSERT INTO by_cid_multi VALUES (?, ?)",
            [
                (GRAPH["by_name"][name], json.dumps([name]))
                for name in GRAPH["concepts"]
            ],
        )
        con.execute(
            f"COPY by_cid_multi TO '{pdir / 'by_cid_multi.parquet'}' "
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
    return {
        "members_rows": len(members),
        "partitions": sorted({row[3] for row in members}),
        "compression": "zstd",
        "side_tables": [
            "by_cid.parquet", "by_cid_multi.parquet", "by_name.parquet",
        ],
    }


def write_release(
    root: Path,
    *,
    parquet: bool,
    manifest_mirror: bool | None = None,
    mirror_facts: dict | None = None,
) -> Path:
    base = root / "associations" / REL
    gdir = base / "graph" / REL
    gdir.mkdir(parents=True, exist_ok=True)
    write_json_graph(gdir)
    mirror = None
    if parquet:
        mirror = write_parquet_graph(base)
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
    if manifest_mirror is None:
        manifest_mirror = parquet
    if manifest_mirror:
        manifest["parquet_mirror"] = mirror_facts if mirror_facts is not None else mirror
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
    base = write_release(tmp_path, parquet=False, manifest_mirror=False)
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
            tmp_path / "json", parquet=False, manifest_mirror=False,
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
        """Older-tag shape: no parquet_mirror block in the root
        manifest, no parquet tree — JSON serving unchanged."""
        g = assoc.get_associations_graph()
        assert type(g) is assoc.AssociationsGraph
        assert g.entry_for_cid("VAL-COND-SNOMED-88594005") is not None

    def test_root_manifest_drives_detection_and_layout(
        self, parquet_release,
    ):
        """The mirror is detected via the repo-ROOT manifest's
        parquet_mirror block and served from the repo-ROOT parquet/
        tree with UUID-based hive-partition filenames."""
        g = assoc.get_associations_graph()
        assert isinstance(g, assoc.ParquetAssociationsGraph)
        assert g._pdir == parquet_release / "parquet"
        assert not (parquet_release / "graph" / REL / "parquet").exists()
        filenames = sorted(
            p.name
            for p in (parquet_release / "parquet" / "members").rglob(
                "*.parquet",
            )
        )
        assert filenames and all(
            name != "data_0.parquet" for name in filenames
        )
        # serving actually answers through the parquet members table
        entry = g.entry_for_cid("VAL-COND-SNOMED-88594005")
        assert len(g.members(entry)) == 5

    def test_manifest_advertises_mirror_but_files_missing(
        self, tmp_path, monkeypatch,
    ):
        base = write_release(
            tmp_path, parquet=False, manifest_mirror=True,
            mirror_facts={"members_rows": 7, "partitions": ["loinc"]},
        )
        monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_DIR", str(base))
        monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_RELEASE", REL)
        assoc.reset_associations_graph_for_testing()
        try:
            with pytest.raises(
                assoc.AssociationsLayoutError, match="parquet_mirror",
            ):
                assoc.get_associations_graph()
        finally:
            assoc.reset_associations_graph_for_testing()

    def test_members_row_count_drift_raises_manifest_error(
        self, tmp_path, monkeypatch,
    ):
        from medterm4ds.core.artifact_manifest import ManifestError

        base = write_release(
            tmp_path, parquet=True,
            mirror_facts=None,  # replaced below
        )
        # rewrite the root manifest with a drifted members_rows fact
        manifest = json.loads((base / "manifest.json").read_text())
        manifest["parquet_mirror"]["members_rows"] = (
            manifest["parquet_mirror"]["members_rows"] + 1
        )
        (base / "manifest.json").write_text(json.dumps(manifest))
        monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_DIR", str(base))
        monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_RELEASE", REL)
        assoc.reset_associations_graph_for_testing()
        try:
            with pytest.raises(ManifestError, match="members_rows"):
                assoc.get_associations_graph()
        finally:
            assoc.reset_associations_graph_for_testing()

    def test_undeclared_partition_raises_manifest_error(
        self, tmp_path, monkeypatch,
    ):
        from medterm4ds.core.artifact_manifest import ManifestError

        base = write_release(tmp_path, parquet=True)
        manifest = json.loads((base / "manifest.json").read_text())
        manifest["parquet_mirror"]["partitions"] = ["loinc"]
        (base / "manifest.json").write_text(json.dumps(manifest))
        monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_DIR", str(base))
        monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_RELEASE", REL)
        assoc.reset_associations_graph_for_testing()
        try:
            with pytest.raises(ManifestError, match="partitions"):
                assoc.get_associations_graph()
        finally:
            assoc.reset_associations_graph_for_testing()

    def test_missing_side_table_raises_loud(
        self, tmp_path, monkeypatch,
    ):
        base = write_release(tmp_path, parquet=True)
        (base / "parquet" / "by_name.parquet").unlink()
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
        pdir = base / "parquet"
        # rewrite members without the provenance column
        con = duckdb.connect(":memory:")
        try:
            con.execute(
                f"CREATE TABLE m AS SELECT cid, bucket, member_cid, "
                f"member_name, member_system, distance FROM "
                f"read_parquet("
                f"'{pdir / 'members'}/**/*.parquet', "
                "hive_partitioning=true)"
            )
            import shutil

            shutil.rmtree(pdir / "members")
            con.execute(
                f"COPY m TO '{pdir / 'members'}' (FORMAT PARQUET, "
                "COMPRESSION ZSTD, "
                "PARTITION_BY (member_system))"
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
        pdir = base / "parquet"
        con = duckdb.connect(":memory:")
        try:
            con.execute(
                f"CREATE TABLE m AS SELECT * FROM read_parquet("
                f"'{pdir / 'members'}/**/*.parquet', "
                "hive_partitioning=true)"
            )
            con.execute(
                "CREATE TABLE m2 AS SELECT * FROM m UNION ALL BY "
                "NAME SELECT * FROM (SELECT cid, 'new_bucket' AS "
                "bucket, member_cid, member_system, member_code, "
                "member_name, provenance, distance, derivations "
                "FROM m WHERE cid = 'herpes simplex infection' "
                "LIMIT 1)"
            )
            import shutil

            shutil.rmtree(pdir / "members")
            con.execute(
                f"COPY (SELECT * FROM m2 ORDER BY cid, bucket, "
                f"member_cid) TO '{pdir / 'members'}' "
                "(FORMAT PARQUET, COMPRESSION ZSTD, "
                "PARTITION_BY (member_system))"
            )
        finally:
            con.close()
        # the injected row drifts members_rows by +1 — keep the
        # manifest fact honest so the load reaches the bucket check
        manifest = json.loads((base / "manifest.json").read_text())
        manifest["parquet_mirror"]["members_rows"] += 1
        (base / "manifest.json").write_text(json.dumps(manifest))
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


@pytest.mark.slow
@pytest.mark.fhir4px_smoke
class TestAssocParquetLiveSmoke:
    """LIVE smoke against the published mirror (network; repo is
    public — no HF_TOKEN needed). Resolves tag v2026.11.3, asserts the
    repo-root parquet mirror is active, then runs the model lane's own
    validation case: metformin (RxNorm 6807) × targetSystem
    http://loinc.org returns monitoring panels."""

    def test_v2026_11_3_parquet_mirror_metformin_loinc(
        self, tmp_path, monkeypatch,
    ):
        pytest.importorskip("huggingface_hub")
        # Fresh acquisition into a tmp cache: a pre-mirror cached
        # copy of the same release has no parquet/ tree (staged
        # swaps only refresh on cache-refresh) — the smoke must
        # exercise the published v2026.11.3 bundle as shipped.
        monkeypatch.setenv(
            "MEDTERM4DS_ASSOCIATIONS_DIR",
            str(tmp_path / "associations-smoke"),
        )
        monkeypatch.delenv(
            "MEDTERM4DS_ASSOCIATIONS_RELEASE", raising=False,
        )
        monkeypatch.setenv(
            "MEDTERM4DS_ASSOCIATIONS_HF_REVISION", "v2026.11.3",
        )
        assoc.reset_associations_graph_for_testing()
        # Explicit pin ⇒ no silent auto-download (EV03-QA doctrine):
        # provision the fresh bundle, then serve.
        assoc.download(assoc.associations_root())
        g = assoc.get_associations_graph()
        assert isinstance(g, assoc.ParquetAssociationsGraph)
        assert g.release == "2026.11.15.1"
        # metformin: the published by_name maps the name to its entry
        # cid (the model lane's RxNorm 6807 anchor); if 6807 itself
        # carries display rows it is used directly.
        cid = "VAL-MED-RXNORM-6807"
        if g.by_cid.get(cid) is None:
            cid = g.by_name["metformin"]
        entry = g.entry_for_cid(cid)
        assert entry is not None, "metformin must be an entry cid"
        loinc = [
            m for _b, m in g.members(entry)
            if assoc.CID_PREFIX_TO_URI[
                "-".join(m["cid"].split("-")[:3])
            ] == "http://loinc.org"
        ]
        assert loinc, (
            "metformin must carry >=1 loinc member via the parquet "
            "mirror (targetSystem http://loinc.org)"
        )
        displays = sorted({m["name"] for m in loinc if m.get("name")})
        assert displays, "loinc members must carry plausible displays"
        assert any(
            any(ch.isalpha() for ch in d) for d in displays
        ), f"implausible loinc displays: {displays!r}"
        print(  # noqa: T201 — smoke evidence
            f"[live-smoke] metformin x loinc ({len(loinc)} rows): "
            f"{displays[:8]}"
        )
        assoc.reset_associations_graph_for_testing()
