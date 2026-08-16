from __future__ import annotations

import pytest

from medterm4ds import (
    CodeInfo,
    CodeMapping,
    CodeRef,
    CodeRelation,
    CodeResolution,
    ConceptMapRow,
    FriendlyNameResult,
    NameSearchResult,
    OptimizeResult,
    OptimizeRule,
    Provenance,
    ProvenanceStep,
    SourceStats,
    get_output_schema,
    list_output_schemas,
)
from medterm4ds.ds import (
    hierarchy_dataframe,
    lookup_dataframe,
    map_dataframe,
    patient_friendly_dataframe,
)
from medterm4ds.outputs import to_dataframe, to_pandas

pd = pytest.importorskip("pandas")


class StaticEngine:
    def get_code_infos(self, codes):
        return [
            CodeInfo(
                code=codes[0],
                name="Type 2 diabetes mellitus",
                cui="C_DIAB",
                aui="ICD_E119",
                tty="PT",
                suppress="N",
            ),
            None,
        ][: len(codes)]

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

    def get_patient_friendly_names(self, codes, max_depth=5):
        return [
            FriendlyNameResult(
                code=codes[0],
                name="Diabetes",
                friendly_source="MEDLINEPLUS",
                match_type="exact",
            )
        ]


def _provenance() -> Provenance:
    return Provenance.from_steps(
        "test",
        [ProvenanceStep(op="input", source="ICD10CM", code="E11.9")],
    )


def test_public_output_schemas_match_to_dict_fields():
    rows = {
        "CodeInfo": CodeInfo(CodeRef("ICD10CM", "E11.9"), name="Type 2 diabetes"),
        "SourceStats": SourceStats("ICD10CM", 1, 2),
        "CodeResolution": CodeResolution(
            input=CodeRef("NDC", "0002-0821-01"),
            resolved=CodeRef("RXNORM", "12345"),
            status="ndc_resolved",
            match_type="ndc_to_rxcui",
        ),
        "NameSearchResult": NameSearchResult(CodeRef("ICD10CM", "E11.9"), "Type 2 diabetes"),
        "CodeMapping": CodeMapping(
            source=CodeRef("ICD10CM", "E11.9"),
            target=CodeRef("SNOMEDCT_US", "44054006"),
            relationship="equivalent",
            match_type="same_cui",
            matched_via=_provenance(),
        ),
        "CodeRelation": CodeRelation(
            source=CodeRef("ICD10CM", "E11.9"),
            target=CodeRef("ICD10CM", "E11"),
            relationship="parent",
        ),
        "FriendlyNameResult": FriendlyNameResult(
            code=CodeRef("ICD10CM", "E11.9"),
            name="Diabetes",
            friendly_source="MEDLINEPLUS",
            match_type="exact",
            matched_via=_provenance(),
        ),
        "ConceptMapRow": ConceptMapRow(
            source=CodeRef("ICD10CM", "E11.9"),
            target=CodeRef("PATIENT_FRIENDLY", "ICD10CM:E11.9"),
            target_display="Diabetes",
            relationship="equivalent",
            matched_via=_provenance(),
        ),
        "OptimizeResult": OptimizeResult(
            source="ICD10CM",
            relationship="isa",
            rules=(OptimizeRule(include=CodeRef("ICD10CM", "E11")),),
            original_count=2,
            optimized_count=1,
            reduction=50.0,
        ),
    }

    assert list_output_schemas() == tuple(rows)
    for name, row in rows.items():
        schema = get_output_schema(name)
        assert schema.version == "1.0.0"
        assert tuple(row.to_dict()) == schema.field_names
        assert schema.to_dict()["fields"][0]["name"] == schema.field_names[0]

    assert get_output_schema(CodeInfo).field_names == get_output_schema("CodeInfo").field_names


def test_dataframe_output_helpers_return_pandas_dataframe():
    df = to_pandas([CodeInfo(CodeRef("ICD10CM", "E11.9"), name="Type 2 diabetes")])

    assert isinstance(df, pd.DataFrame)
    assert df.to_dict("records")[0]["source"] == "ICD10CM"

    with pytest.raises(ValueError, match="backend"):
        to_dataframe([], backend="spark")  # type: ignore[arg-type]


def test_ds_wrappers_return_dataframes_with_service_rows():
    engine = StaticEngine()

    lookup_df = lookup_dataframe(
        [("ICD10CM", "E11.9"), ("CVX", "NOPE")],
        engine=engine,
    )
    map_df = map_dataframe(
        [CodeRef("ICD10CM", "E11.9")],
        engine=engine,
        target_sources=["SNOMEDCT_US"],
    )
    hierarchy_df = hierarchy_dataframe(
        [CodeRef("ICD10CM", "E11.9")],
        engine=engine,
        direction="parents",
    )
    friendly_df = patient_friendly_dataframe(
        [CodeRef("ICD10CM", "E11.9")],
        engine=engine,
    )

    assert lookup_df.to_dict("records")[1] == {
        "source": "CVX",
        "code": "NOPE",
        "name": None,
        "cui": None,
        "aui": None,
        "tty": None,
        "suppress": None,
    }
    assert map_df.to_dict("records")[0]["target_source"] == "SNOMEDCT_US"
    assert hierarchy_df.to_dict("records")[0]["relationship"] == "parent"
    assert friendly_df.to_dict("records")[0]["name"] == "Diabetes"
