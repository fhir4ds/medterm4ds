from __future__ import annotations

from pathlib import Path

import duckdb

from medterm4ds.domains import (
    cross_reference,
    diagnosis_codes,
    discover,
    drugs_for_indication,
    fda_label_by_rxcui,
    guideline_fulltext,
    guideline_search,
    guidelines_for_code,
    indication_search,
    lab_codes,
    procedure_codes,
    search_drug,
    vaccine_codes,
)
from medterm4ds.domains.evidence import HttpResponse, OpenFDALabelClient, PubMedGuidelineClient
from medterm4ds.engines.duckdb import LocalDuckDBEngine


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
                ("E11", "PT", "Type 2 diabetes mellitus", "ICD_E11", "N", "ICD10CM", "C_E11"),
                ("44054006", "PT", "Diabetes mellitus type 2", "SNOMED_DIAB", "N", "SNOMEDCT_US", "C_DIAB"),
                ("4548-4", "LN", "Hemoglobin A1c", "LNC_A1C", "N", "LNC", "C_A1C"),
                ("83036", "PT", "Hemoglobin A1c test", "CPT_A1C", "N", "CPT", "C_A1C"),
                ("J1815", "PT", "Insulin injection", "HCPCS_INS", "N", "HCPCS", "C_INS"),
                ("208", "PT", "COVID-19 vaccine", "CVX_208", "N", "CVX", "C_COVID_VAX"),
                ("12345", "SCD", "Insulin 100 UNT/ML Injection", "RX_INS", "N", "RXNORM", "C_INS"),
            ],
        )
        con.executemany(
            "INSERT INTO mrrel VALUES (?, ?, ?, ?)",
            [
                ("ICD_E119", "ICD_E11", "isa", "PAR"),
            ],
        )
    finally:
        con.close()


def test_domain_search_wrappers_and_cross_reference(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        engine = LocalDuckDBEngine(con)

        diagnosis = diagnosis_codes("diabetes", engine=engine)
        labs = lab_codes("a1c", engine=engine)
        procedures = procedure_codes("a1c", engine=engine)
        vaccines = vaccine_codes("covid", engine=engine)
        drugs = search_drug("insulin", engine=engine)
        xref = cross_reference("E11.9", "ICD10-CM", engine=engine, to_sources=["SNOMED"])
        hierarchy = discover("ICD10CM", code="E11.9", engine=engine, include_ancestors=True)
    finally:
        con.close()

    assert diagnosis["query"] == "diagnosis_codes"
    assert {row["source"] for row in diagnosis["results"]} == {"ICD10CM", "SNOMEDCT_US"}
    assert labs["results"][0]["source"] == "LNC"
    assert procedures["results"][0]["source"] == "CPT"
    assert vaccines["results"][0]["source"] == "CVX"
    assert drugs["results"][0]["source"] == "RXNORM"
    assert xref["results"][0]["target_source"] == "SNOMEDCT_US"
    assert hierarchy["ancestors"][0]["target_code"] == "E11"


def _make_indication_duckdb(path: Path) -> None:
    """Build a small UMLS fixture exercising the indication relationship walk.

    Topology (ICD -> MSH via shared CUI; MSH -> RxNorm via may_treat):
        ICD10CM:E11.9 (CUI_C) -> shares CUI_C with MSH:D003920 (MH)
        MSH:D003920 --may_treat--> RXNORM:161 (IN, "acetaminophen")
                              --> RXNORM:999 (MIN, multi-ingredient)
        RXNORM:161 --has_ingredient--> RXNORM:SCDG1 (SCDG, single-ingredient group)
        RXNORM:999 has 2 has_ingredient edges to IN atoms (so it counts as multi)
    Also includes AUIs that exercise the cycle-detection substring trap:
    AUIs 'A1', 'A10', 'A100' are mutually non-cyclic but prefix-overlapping.
    """
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
                # ICD seed + its parent (so the source walk has somewhere to go)
                ("E11.9", "PT", "Type 2 diabetes mellitus", "A1", "N", "ICD10CM", "C_C"),
                ("E11", "PT", "Type 2 diabetes mellitus", "A10", "N", "ICD10CM", "C_C"),
                # MSH MH descriptor sharing the CUI with the ICD seed
                ("D003920", "MH", "Diabetes Mellitus, Type 2", "MSH_A1", "N", "MSH", "C_C"),
                # RxNorm ingredient target (single-ingredient drug)
                ("161", "IN", "Acetaminophen", "RX_IN_161", "N", "RXNORM", "RX_161"),
                # RxNorm MIN target (multi-ingredient drug)
                ("999", "MIN", "Acetaminophen / Hydrocodone", "RX_MIN_999", "N", "RXNORM", "RX_999"),
                # RxNorm SCDG group expanded from ingredient 161
                ("SCDG1", "SCDG", "Acetaminophen 325 MG Oral Tablet", "RX_SCDG1", "N", "RXNORM", "RX_SCDG1"),
                # Two IN ingredients linked to MIN 999 (counts as 2)
                ("AAA", "IN", "Acetaminophen Ingredient", "RX_IN_AAA", "N", "RXNORM", "RX_AAA"),
                ("BBB", "IN", "Hydrocodone Ingredient", "RX_IN_BBB", "N", "RXNORM", "RX_BBB"),
            ],
        )
        con.executemany(
            "INSERT INTO mrrel VALUES (?, ?, ?, ?)",
            [
                # ICD E11.9 -> E11 parent walk (PAR). AUIs 'A1' and 'A10' are
                # prefix-overlapping: substring-based cycle detection would
                # falsely flag this legitimate edge as a cycle.
                ("A1", "A10", "isa", "PAR"),
                # MSH MH may_treat edges to RxNorm ingredient and MIN
                ("MSH_A1", "RX_IN_161", "may_treat", "RO"),
                ("MSH_A1", "RX_MIN_999", "may_treat", "RO"),
                # Product-group expansion from ingredient 161.
                # Codebase convention: ingredient has_ingredient group (AUI1=IN, AUI2=group).
                ("RX_IN_161", "RX_SCDG1", "has_ingredient", "PAR"),
                # MIN 999 has two ingredients (multi-ingredient). Same direction.
                ("RX_IN_AAA", "RX_MIN_999", "has_ingredient", "PAR"),
                ("RX_IN_BBB", "RX_MIN_999", "has_ingredient", "PAR"),
            ],
        )
    finally:
        con.close()


def test_drugs_for_indication_relationship_walk(tmp_path):
    db_path = tmp_path / "indication.duckdb"
    _make_indication_duckdb(db_path)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        engine = LocalDuckDBEngine(con)
        result = drugs_for_indication(
            "diabetes",
            engine=engine,
            source="ICD10CM",
            code="E11.9",
            relationship_types=["may_treat"],
        )
    finally:
        con.close()

    # Uniform shape — every key present regardless of success/fallback branch.
    expected_keys = {
        "query",
        "condition",
        "source",
        "code",
        "status",
        "relationship_types",
        "target_source",
        "target_ttys",
        "max_depth",
        "include_product_groups",
        "result_count",
        "results",
        "diagnosis_context",
    }
    assert set(result) == expected_keys
    assert result["status"] == "ok"
    assert result["source"] == "ICD10CM"
    assert result["code"] == "E11.9"

    # Path delimiter is uniform ' -> '; the relationship hop is its own element.
    paths = [row["path"] for row in result["results"]]
    assert paths, "expected at least one relationship row"
    for path_segments in paths:
        assert " -> " not in path_segments[-1], f"relationship hop fused: {path_segments}"
        assert "may_treat" in path_segments, f"relationship label missing: {path_segments}"

    # MIN target gets the correct multi-ingredient count (not the 1 fallback).
    min_rows = [r for r in result["results"] if r["target_tty"] == "MIN"]
    assert min_rows, "expected at least one MIN target row in fixture"
    for min_row in min_rows:
        assert min_row["ingredient_count"] >= 2, min_row
        assert min_row["is_single_ingredient"] is False, min_row


def test_drugs_for_indication_fallback_shape_matches_success(tmp_path):
    db_path = tmp_path / "indication.duckdb"
    _make_indication_duckdb(db_path)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        engine = LocalDuckDBEngine(con)
        # No candidates: condition that doesn't match any diagnosis row.
        result = drugs_for_indication("zzz-not-a-condition", engine=engine)
    finally:
        con.close()

    expected_keys = {
        "query",
        "condition",
        "source",
        "code",
        "status",
        "relationship_types",
        "target_source",
        "target_ttys",
        "max_depth",
        "include_product_groups",
        "result_count",
        "results",
        "diagnosis_context",
    }
    assert set(result) == expected_keys
    assert result["status"] == "no_condition_candidates"
    assert result["results"] == []


def test_drugs_for_indication_validation_errors(tmp_path):
    db_path = tmp_path / "indication.duckdb"
    _make_indication_duckdb(db_path)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        engine = LocalDuckDBEngine(con)
        import pytest

        with pytest.raises(ValueError, match="condition must be a non-empty"):
            drugs_for_indication("", engine=engine)
        with pytest.raises(ValueError, match="condition must be a non-empty"):
            drugs_for_indication("   ", engine=engine)
        with pytest.raises(ValueError, match="source is required"):
            drugs_for_indication("diabetes", engine=engine, code="E11.9")
        with pytest.raises(ValueError, match="source is required"):
            drugs_for_indication("diabetes", engine=engine, source="", code="E11.9")
        with pytest.raises(ValueError, match="Unsupported indication relationship"):
            drugs_for_indication("diabetes", engine=engine, relationship_types=["unknown_rel"])
    finally:
        con.close()


class FakeHttp:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, params):
        self.calls.append((url, dict(params)))
        key = (
            url.rsplit("/", 1)[-1],
            params.get("retmode", "json"),
            params.get("search") or params.get("term") or params.get("id"),
        )
        return HttpResponse(self.responses[key])


def test_external_evidence_tools_use_openfda_client():
    http = FakeHttp(
        {
            (
                "label.json",
                "json",
                'openfda.rxcui:"12345"',
            ): '{"results":[{"id":"LABEL1","set_id":"SET1","effective_time":"20240101","openfda":{"rxcui":["12345"],"brand_name":["Brand"],"generic_name":["Generic"],"manufacturer_name":["Maker"]},"indications_and_usage":["Treats hypertension"]}]}',
            (
                "label.json",
                "json",
                'indications_and_usage:"hypertension"',
            ): '{"results":[{"id":"LABEL2","openfda":{"rxcui":["67890"]},"indications_and_usage":["For hypertension"]}]}',
        }
    )
    client = OpenFDALabelClient(http=http)

    label = fda_label_by_rxcui("12345", client=client)
    indication = indication_search("hypertension", client=client)

    assert label["status"] == "ok"
    assert label["query"] == "fda_label_by_rxcui"
    assert label["results"][0]["brand_name"] == ["Brand"]
    assert indication["results"][0]["rxcui"] == ["67890"]


def test_external_evidence_tools_use_pubmed_client(tmp_path):
    http = FakeHttp(
        {
            (
                "esearch.fcgi",
                "json",
                "(diabetes) AND (guideline[Publication Type] OR practice guideline[Publication Type])",
            ): '{"esearchresult":{"idlist":["111"]}}',
            (
                "esummary.fcgi",
                "json",
                "111",
            ): '{"result":{"uids":["111"],"111":{"uid":"111","title":"Diabetes guideline","fulljournalname":"Journal","pubdate":"2024","authors":[{"name":"A Author"}]}}}',
            (
                "esearch.fcgi",
                "json",
                "(Type 2 diabetes mellitus) AND (guideline[Publication Type] OR practice guideline[Publication Type])",
            ): '{"esearchresult":{"idlist":["111"]}}',
            (
                "efetch.fcgi",
                "xml",
                "111",
            ): '<PubmedArticleSet><PubmedArticle><MedlineCitation><Article><Journal><Title>Journal</Title><JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue></Journal><ArticleTitle>Diabetes guideline</ArticleTitle><Abstract><AbstractText>Recommendation text.</AbstractText></Abstract></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>',
        }
    )
    client = PubMedGuidelineClient(http=http)
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        engine = LocalDuckDBEngine(con)
        search = guideline_search("diabetes", client=client)
        fulltext = guideline_fulltext("111", client=client)
        by_code = guidelines_for_code("E11.9", "ICD10CM", engine=engine, client=client)
    finally:
        con.close()

    assert search["results"][0]["pmid"] == "111"
    assert fulltext["result"]["abstract"] == "Recommendation text."
    assert by_code["guideline_query"] == "Type 2 diabetes mellitus"
