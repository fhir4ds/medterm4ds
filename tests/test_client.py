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

    def get_code_relations(self, codes, *, direction, max_depth=1, limit=None, include_retired=False):
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


def test_lookup_df_empty_batch_returns_canonical_schema():
    """Regression for QC-004 (EDGE_CASE LOW): lookup_df([]) returned 0-column DF.

    Pre-fix, ``pd.DataFrame([], dtype=object)`` produced an empty frame with
    no columns because there were no records to infer schema from. Downstream
    code (df['name'], df.name.notna()) raised KeyError on the empty case.
    The fix returns the canonical 7-column CodeInfo schema for empty input.
    """
    pd = pytest.importorskip("pandas")
    terms = mt.Terminology(StaticTerminologyEngine())

    empty_df = terms.lookup_df([])
    full_df = terms.lookup_df("ICD10CM", "E11.9")

    assert list(empty_df.columns) == list(full_df.columns)
    assert empty_df.shape[0] == 0
    assert set(empty_df.columns) == {
        "source", "code", "name", "cui", "aui", "tty", "suppress"
    }


def test_map_df_empty_result_returns_canonical_schema():
    """Regression for QC-024 (EDGE_CASE MEDIUM): map_df returned 0-column DF.

    Pre-fix, ``map_df`` on a not-found code produced a 0x0 frame because
    there were no records to infer schema from. Downstream code
    (df['source'], df['target_code']) raised KeyError on the empty case.
    The fix returns the canonical 16-column CodeMapping schema for empty
    results. Mirrors the QC-004 fix shape (FIX-007) for lookup_df.
    """
    pd = pytest.importorskip("pandas")

    class _EmptyMappingEngine(StaticTerminologyEngine):
        def get_code_mappings(self, codes, **kwargs):
            return []

    terms = mt.Terminology(_EmptyMappingEngine())

    empty_df = terms.map_df("SNOMEDCT_US", "NOTACODE", target_sources=["ICD10CM"])

    assert empty_df.shape[0] == 0
    # 16 columns per CodeMapping.to_dict() (15 typed fields + matched_via).
    assert set(empty_df.columns) == {
        "source", "code", "source_display",
        "target_source", "target_code", "target_display",
        "relationship", "match_type", "match_depth",
        "source_cui", "target_cui",
        "source_aui", "target_aui", "target_tty",
        "matched_via",
    }
    # The critical downstream operation that KeyError'd pre-fix:
    assert empty_df["source"].notna().sum() == 0


def test_hierarchy_df_empty_result_returns_canonical_schema():
    """Regression for QC-045 (EDGE_CASE HIGH): hierarchy_df returned 0-column DF.

    Pre-fix, ``hierarchy_df`` on a bogus code produced a 0x0 frame because
    there were no records to infer schema from. Downstream code
    (df['target_code']) raised KeyError on the empty case. The fix returns
    the canonical 14-column CodeRelation schema for empty results. Mirrors
    the QC-004/QC-024 fix shape (FIX-007 / FIX-006).
    """
    pd = pytest.importorskip("pandas")

    class _EmptyHierarchyEngine(StaticTerminologyEngine):
        def get_code_relations(self, codes, **kwargs):
            return []

    terms = mt.Terminology(_EmptyHierarchyEngine())

    empty_df = terms.hierarchy_df(
        ("SNOMEDCT_US", "NOTACODE"), direction="children", max_depth=1
    )

    assert empty_df.shape[0] == 0
    # 14 columns per CodeRelation.to_dict().
    assert set(empty_df.columns) == {
        "source", "code", "source_display",
        "target_source", "target_code", "target_display",
        "relationship", "depth",
        "rel", "rela",
        "source_cui", "target_cui",
        "source_aui", "target_aui",
    }
    # The critical downstream operation that KeyError'd pre-fix:
    assert empty_df["target_code"].notna().sum() == 0


def test_patient_friendly_df_empty_batch_returns_canonical_schema():
    """Regression for QC-072 (EDGE_CASE HIGH): patient_friendly_df returned 0-column DF.

    Pre-fix, ``patient_friendly_df([])`` produced a 0x0 frame because there
    were no records to infer schema from. Downstream code (df['name'])
    raised KeyError on the empty case. The fix returns the canonical
    8-column FriendlyNameResult schema for empty results. Mirrors the
    QC-004/QC-024/QC-045 fix shape (FIX-007 / FIX-006 / EC-03 FIX-002).
    """
    pd = pytest.importorskip("pandas")

    terms = mt.Terminology(StaticTerminologyEngine())
    empty_df = terms.patient_friendly_df([])

    assert empty_df.shape[0] == 0
    # 8 columns per FriendlyNameResult.to_dict().
    assert set(empty_df.columns) == {
        "code", "source", "name",
        "friendly_source", "match_type", "match_depth",
        "technical_name", "matched_via",
    }
    # The critical downstream operation that KeyError'd pre-fix:
    assert empty_df["name"].notna().sum() == 0


def test_conceptmap_df_empty_batch_returns_canonical_schema():
    """Regression for QC-073 (EDGE_CASE HIGH): conceptmap_df returned 0-column DF.

    Pre-fix, ``conceptmap_df([])`` produced a 0x0 frame because there were
    no records to infer schema from. Downstream code (df['target_display'])
    raised KeyError on the empty case. The fix returns the canonical
    11-column ConceptMapRow schema for empty results.
    """
    pd = pytest.importorskip("pandas")

    class _EmptyFriendlyEngine(StaticTerminologyEngine):
        def get_patient_friendly_names(self, codes, max_depth=5):
            return []

    terms = mt.Terminology(_EmptyFriendlyEngine())
    empty_df = terms.conceptmap_df([])

    assert empty_df.shape[0] == 0
    # 11 columns per ConceptMapRow.to_dict().
    assert set(empty_df.columns) == {
        "source", "code", "source_display",
        "target_source", "target_code", "target_display",
        "relationship", "friendly_source",
        "match_type", "match_depth", "matched_via",
    }
    assert empty_df["target_display"].notna().sum() == 0


def test_mapping_conceptmap_df_empty_batch_returns_canonical_schema():
    """Regression for QC-080 (CROSS_SURFACE HIGH): mapping_conceptmap_df returned 0-column DF.

    Pre-fix, ``mapping_conceptmap_df([], target_sources=...)`` produced a
    0x0 frame because there were no records to infer schema from. The fix
    returns the canonical 11-column ConceptMapRow schema for empty results.
    More aggressive than patient_friendly_df/conceptmap_df because mapping
    has no 'original' fallback.
    """
    pd = pytest.importorskip("pandas")

    class _EmptyMappingEngine(StaticTerminologyEngine):
        def get_code_mappings(self, codes, **kwargs):
            return []

    terms = mt.Terminology(_EmptyMappingEngine())
    empty_df = terms.mapping_conceptmap_df(
        [], target_sources=("ICD10CM",)
    )

    assert empty_df.shape[0] == 0
    assert set(empty_df.columns) == {
        "source", "code", "source_display",
        "target_source", "target_code", "target_display",
        "relationship", "friendly_source",
        "match_type", "match_depth", "matched_via",
    }
    assert empty_df["relationship"].notna().sum() == 0


def test_search_df_empty_result_returns_canonical_schema():
    """Regression for QC-105 (CROSS_SURFACE HIGH): search_df returned 0-column DF.

    Pre-fix, ``search_df`` on a no-match query produced a 0x0 frame because
    there were no records to infer schema from. Downstream code
    (df['source']) raised KeyError on the empty case. The fix returns the
    canonical 7-column NameSearchResult schema for empty results. Mirrors
    the QC-004/QC-024/QC-045/QC-072/QC-073/QC-080 fix shape.
    """
    pd = pytest.importorskip("pandas")

    class _EmptySearchEngine(StaticTerminologyEngine):
        def search_names(self, query, *, sources=None, tty_filters=None, limit=25):
            return []

    terms = mt.Terminology(_EmptySearchEngine())
    empty_df = terms.search_df("zzz_no_such_query_xyz", limit=1)

    assert empty_df.shape[0] == 0
    # 7 columns per NameSearchResult.to_dict().
    assert set(empty_df.columns) == {
        "source", "code", "name", "cui", "aui", "tty", "match_type",
    }
    # The critical downstream operation that KeyError'd pre-fix:
    assert empty_df["source"].notna().sum() == 0


def test_code_ttys_df_empty_batch_returns_canonical_schema():
    """Regression for QC-106 (CROSS_SURFACE HIGH): code_ttys_df returned 0-column DF.

    Pre-fix, ``code_ttys_df([])`` AND ``code_ttys_df([bogus])`` both produced
    0x0 frames because there were no records to infer schema from.
    Downstream code (df['tty']) raised KeyError. The fix returns the
    canonical 7-column CodeInfo schema for empty results. Mirrors the
    QC-004/QC-024/QC-045/QC-072/QC-073/QC-080 fix shape.
    """
    pd = pytest.importorskip("pandas")

    class _EmptyTtysEngine(StaticTerminologyEngine):
        def get_code_ttys(self, codes):
            return []

    terms = mt.Terminology(_EmptyTtysEngine())

    # Empty-list input
    empty_df1 = terms.code_ttys_df([])
    assert empty_df1.shape[0] == 0
    assert set(empty_df1.columns) == {
        "source", "code", "name", "cui", "aui", "tty", "suppress",
    }
    assert empty_df1["tty"].notna().sum() == 0

    # Bogus-code input (engine returns [])
    empty_df2 = terms.code_ttys_df([("SNOMEDCT_US", "bogus_xyz")])
    assert empty_df2.shape[0] == 0
    assert set(empty_df2.columns) == {
        "source", "code", "name", "cui", "aui", "tty", "suppress",
    }


def test_resolve_df_empty_batch_returns_canonical_schema():
    """Regression for QC-100 (EDGE_CASE MEDIUM): resolve_df([]) returned 0-column DF.

    Pre-fix, ``resolve_df([])`` produced a 0x0 frame because there were no
    records to infer schema from. Downstream code (df['status']) raised
    KeyError on the empty case. The fix returns the canonical 18-column
    CodeResolution schema for empty input. Mirrors the
    QC-004/QC-024/QC-045/QC-072/QC-073/QC-080/QC-105/QC-106 fix shape.
    """
    pd = pytest.importorskip("pandas")

    class _EmptyResolveEngine(StaticTerminologyEngine):
        def resolve_codes(self, codes):
            return []

    terms = mt.Terminology(_EmptyResolveEngine())
    empty_df = terms.resolve_df([])

    assert empty_df.shape[0] == 0
    # 18 columns per CodeResolution.to_dict().
    assert set(empty_df.columns) == {
        "source", "code",
        "resolved_source", "resolved_code",
        "status", "match_type",
        "input_display", "resolved_display",
        "input_cui", "resolved_cui",
        "input_aui", "resolved_aui",
        "input_suppress", "resolved_suppress",
        "replacement_relationship", "normalized_code",
        "candidates", "matched_via",
    }
    # The critical downstream operation that KeyError'd pre-fix:
    assert empty_df["status"].notna().sum() == 0


def test_resolve_accepts_resolve_mode_kwarg():
    """Regression for QC-102 (EDGE_CASE LOW): client.resolve lacked resolve_mode.

    Pre-fix, ``client.resolve`` always delegated to resolve_codes with no mode
    parameter; the resolve_mode surface was only reachable indirectly via
    lookup_df. The fix exposes resolve_mode on the resolve surface so
    callers can choose active_only / historical / resolve_current.
    """
    import inspect

    terms = mt.Terminology(StaticTerminologyEngine())
    sig = inspect.signature(terms.resolve)
    assert "resolve_mode" in sig.parameters
    assert sig.parameters["resolve_mode"].default == "historical"

    # Active-only mode still returns CodeResolution rows for active codes.
    result = terms.resolve("ICD10CM", "E11.9", resolve_mode="active_only")
    assert result.status == "active"
    assert result.match_type == "active_exact"

    # Default historical mode preserves prior behavior.
    result_default = terms.resolve("ICD10CM", "E11.9")
    assert result_default.status == "active"


def test_coderef_rejects_none_source_or_code():
    """Regression for QC-003 (EDGE_CASE LOW): CodeRef(None, 'x') silently str()'d.

    Pre-fix, ``str(None) == 'None'`` turned a None code into the literal
    string 'None', producing a misleading 'not found' instead of a type
    error. Per GLOBAL_RULES "Silent Fallbacks": programming bugs MUST
    propagate. The fix raises TypeError in __post_init__.
    """
    from medterm4ds.core.models import CodeRef

    with pytest.raises(TypeError, match="code must be a string"):
        CodeRef("SNOMEDCT_US", None)
    with pytest.raises(TypeError, match="source must be a string"):
        CodeRef(None, "44054006")


def test_lookup_rejects_non_string_code_with_helpful_message():
    """Regression for QC-005 (EDGE_CASE LOW): int code gave unhelpful TypeError.

    Pre-fix, ``terms.lookup('SNOMEDCT_US', 44054006)`` raised
    ``TypeError: 'int' object is not iterable`` because the int hit the
    list-comprehension branch. The fix raises a TypeError that points at the
    int-vs-string issue and suggests the string form.
    """
    terms = mt.Terminology(StaticTerminologyEngine())

    with pytest.raises(TypeError, match="code must be a string"):
        terms.lookup("SNOMEDCT_US", 44054006)


def test_parents_rejects_int_code_in_tuple():
    """Regression for QC-054 (EDGE_CASE LOW): int code silently coerced.

    Pre-fix, ``terms.parents([('SNOMEDCT_US', 44054006)])`` silently coerced
    the int to '44054006' via ``str(int)`` and returned valid results — the
    programming error was accepted as valid input. The fix raises TypeError
    pointing at the int-vs-string issue (caught and re-raised by the outer
    _code_refs_from_args wrapper with a generic message).
    """
    terms = mt.Terminology(StaticTerminologyEngine())

    with pytest.raises(TypeError):
        terms.parents([("SNOMEDCT_US", 44054006)])


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
