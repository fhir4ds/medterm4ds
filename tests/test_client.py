from __future__ import annotations

import duckdb
import pytest

import medterm4ds as mt
from medterm4ds import (
    CodeInfo,
    CodeMapping,
    CodeRef,
    CodeRelation,
    CodeResolution,
    FriendlyNameResult,
    NameSearchResult,
    OptimizeResult,
    OptimizeRule,
    SourceStats,
)


class StaticTerminologyEngine:
    def get_code_infos(self, codes):
        return [
            CodeInfo(
                code=code,
                name="Type 2 diabetes mellitus" if code.code == "E11.9" else None,
                cui="C_DIAB" if code.code == "E11.9" else None,
                aui="ICD_E119" if code.code == "E11.9" else None,
                tty="PT" if code.code == "E11.9" else None,
                suppress="N" if code.code == "E11.9" else None,
            )
            if code.code == "E11.9"
            else None
            for code in codes
        ]

    def get_code_mappings(
        self,
        codes,
        *,
        target_sources,
        max_results_per_code=50,
        max_depth=0,
        include_target_ancestors=False,
        include_target_descendants=False,
    ):
        return [
            CodeMapping(
                source=codes[0],
                target=CodeRef(target_sources[0], "44054006"),
                relationship="equivalent",
                match_type="same_cui",
                source_display="Type 2 diabetes mellitus",
                target_display="Diabetes mellitus type 2",
            )
        ]

    def get_code_relations(self, codes, *, direction, max_depth=1, limit=None):
        return [
            CodeRelation(
                source=codes[0],
                target=CodeRef(codes[0].source, "E11"),
                relationship="parent",
                depth=1,
            )
        ]

    def get_source_stats(self, sources=None):
        return [SourceStats("ICD10CM", code_count=1, atom_count=1)]

    def sample_source_codes(self, sources, *, per_source=10):
        return [CodeRef(sources[0], "E11.9")]

    def get_code_ttys(self, codes):
        return [
            CodeInfo(
                code=codes[0],
                name="Type 2 diabetes mellitus",
                cui="C_DIAB",
                aui="ICD_E119",
                tty="PT",
                suppress="N",
            )
        ]

    def search_names(self, query, *, sources=None, tty_filters=None, limit=25):
        return [
            NameSearchResult(
                code=CodeRef("ICD10CM", "E11.9"),
                name="Type 2 diabetes mellitus",
                match_type="contains",
            )
        ]

    def get_patient_friendly_names(self, codes, max_depth=5):
        return [
            FriendlyNameResult(
                code=code,
                name="Diabetes",
                friendly_source="MEDLINEPLUS",
                match_type="exact",
            )
            for code in codes
        ]

    def resolve_codes(self, codes):
        return [
            CodeResolution(
                input=code,
                resolved=code,
                status="active",
                match_type="active_exact",
            )
            for code in codes
        ]

    def optimize_codes(
        self,
        codes,
        *,
        relationship=None,
        output_format="compact",
        include_codes=False,
    ):
        return OptimizeResult(
            source=codes[0].source,
            relationship=relationship or "isa",
            rules=(OptimizeRule(include=CodeRef(codes[0].source, "E11")),),
            original_count=len(codes),
            optimized_count=1,
            reduction=50.0,
        )


def test_terminology_facade_supports_single_and_batch_inputs():
    terms = mt.Terminology(StaticTerminologyEngine())

    single = terms.lookup("ICD10CM", "E11.9")
    batch = terms.lookup("ICD10CM", ["E11.9", "NOPE"])
    refs = terms.lookup([CodeRef("ICD10CM", "E11.9"), ("CVX", "208")])

    assert single.name == "Type 2 diabetes mellitus"
    assert [row.name if row else None for row in batch] == ["Type 2 diabetes mellitus", None]
    assert refs[0].code == CodeRef("ICD10CM", "E11.9")
    assert terms.patient_friendly("ICD10CM", "E11.9").name == "Diabetes"
    assert terms.resolve("ICD10CM", "E11.9").status == "active"


def test_terminology_facade_exposes_mapping_hierarchy_discovery_and_optimize():
    terms = mt.Terminology(StaticTerminologyEngine())

    mapping = terms.map("ICD10CM", "E11.9", target_sources=["SNOMEDCT_US"])
    hierarchy = terms.parents("ICD10CM", "E11.9")
    search = terms.search("diabetes", sources=["ICD10CM"])
    optimized = terms.optimize("ICD10CM", ["E11.40", "E11.41"])

    assert mapping[0].target == CodeRef("SNOMEDCT_US", "44054006")
    assert hierarchy[0].target == CodeRef("ICD10CM", "E11")
    assert terms.source_stats()[0].source == "ICD10CM"
    assert terms.sample_codes("ICD10CM")[0] == CodeRef("ICD10CM", "E11.9")
    assert terms.code_ttys("ICD10CM", "E11.9")[0].tty == "PT"
    assert search[0].code == CodeRef("ICD10CM", "E11.9")
    assert optimized.rules[0].include == CodeRef("ICD10CM", "E11")


def test_terminology_facade_dataframe_helpers():
    pd = pytest.importorskip("pandas")
    terms = mt.Terminology(StaticTerminologyEngine())

    lookup_df = terms.lookup_df("ICD10CM", ["E11.9", "NOPE"])
    mapping_df = terms.map_df("ICD10CM", "E11.9", target_sources=["SNOMEDCT_US"])
    search_df = terms.search_df("diabetes", sources=["ICD10CM"])

    assert isinstance(lookup_df, pd.DataFrame)
    assert lookup_df.to_dict("records")[1]["name"] is None
    assert mapping_df.to_dict("records")[0]["target_source"] == "SNOMEDCT_US"
    assert search_df.to_dict("records")[0]["code"] == "E11.9"


def test_connect_remote_wraps_remote_api_engine():
    def transport(path, payload):
        assert path == "/lookup"
        return {
            "results": [
                {
                    "source": payload["codes"][0]["source"],
                    "code": payload["codes"][0]["code"],
                    "name": "Type 2 diabetes mellitus",
                    "cui": "C_DIAB",
                    "aui": "ICD_E119",
                    "tty": "PT",
                    "suppress": "N",
                }
            ]
        }

    terms = mt.connect_remote("http://example.test", transport=transport)

    assert terms.lookup("ICD10CM", "E11.9").name == "Type 2 diabetes mellitus"


def test_connect_opens_local_duckdb_database(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    con = duckdb.connect(str(db_path))
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
            "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("E11.9", "PT", "Type 2 diabetes mellitus", "ICD_E119", "N", "ICD10CM", "C_DIAB"),
        )
    finally:
        con.close()

    with mt.connect(db_path, memory_profile="low") as terms:
        row = terms.lookup("ICD10CM", "E11.9")

    assert row.name == "Type 2 diabetes mellitus"
