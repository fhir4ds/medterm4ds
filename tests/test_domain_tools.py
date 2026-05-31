from __future__ import annotations

from pathlib import Path

import duckdb

from medterm4ds.domains import (
    cross_reference,
    diagnosis_codes,
    discover,
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
from medterm4ds.engines.duckdb import LocalLiteEngine


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
        engine = LocalLiteEngine(con)

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
        engine = LocalLiteEngine(con)
        search = guideline_search("diabetes", client=client)
        fulltext = guideline_fulltext("111", client=client)
        by_code = guidelines_for_code("E11.9", "ICD10CM", engine=engine, client=client)
    finally:
        con.close()

    assert search["results"][0]["pmid"] == "111"
    assert fulltext["result"]["abstract"] == "Recommendation text."
    assert by_code["guideline_query"] == "Type 2 diabetes mellitus"
