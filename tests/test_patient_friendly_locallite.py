from __future__ import annotations

import sys
from pathlib import Path

import duckdb

from medterm4ds import CodeRef, get_patient_friendly_names
from medterm4ds.engines.duckdb import LocalLiteEngine
from medterm4ds.engines.medterm_baseline import MedtermBulkBaselineEngine


MEDTERM_SRC = Path("/mnt/d/medterm/src")


def _init_schema(con: duckdb.DuckDBPyConnection) -> None:
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


def _seed_patient_friendly_db(con: duckdb.DuckDBPyConnection) -> None:
    _init_schema(con)
    con.executemany(
        "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            # ICD direct MEDLINEPLUS.
            ("E11.9", "PT", "Type 2 diabetes mellitus without complications", "ICD_E119", "N", "ICD10CM", "C_DIAB"),
            ("D_DIAB", "MH", "Type 2 Diabetes Mellitus", "MP_DIAB", "N", "MEDLINEPLUS", "C_DIAB"),
            # ICD10PCS direct CHV.
            ("0FT44ZZ", "PT", "Resection of gallbladder", "PCS_1", "N", "ICD10PCS", "C_PCS"),
            ("CHV_PCS", "PT", "Gallbladder removal", "CHV_PCS_AUI", "N", "CHV", "C_PCS"),
            # HCPCS direct CHV, no SNOMED fallback expected.
            ("J1815", "PT", "Injection, insulin, per 5 units", "HCPCS_INS", "N", "HCPCS", "C_INS"),
            ("CHV_INS", "PT", "Insulin shot", "CHV_INS_AUI", "N", "CHV", "C_INS"),
            # ICD fallback through SNOMED ancestor.
            ("A00.0", "PT", "Rare intestinal infection", "ICD_A00", "N", "ICD10CM", "C_RARE"),
            ("100001", "PT", "Rare intestinal infection SNOMED", "SN_100001", "N", "SNOMEDCT_US", "C_RARE"),
            ("100002", "PT", "Intestinal infection", "SN_100002", "N", "SNOMEDCT_US", "C_RARE_PARENT"),
            ("MP_RARE", "MH", "Intestinal Infections", "MP_RARE_AUI", "N", "MEDLINEPLUS", "C_RARE_PARENT"),
            # SNOMED maps to ICD, then inherits ICD friendly name.
            ("44054006", "PT", "Diabetes mellitus type 2", "SN_DIAB", "N", "SNOMEDCT_US", "C_DIAB"),
            # RxNorm group and ingredient paths.
            ("2611787", "SCD", "Child drug product", "RX_SCD", "N", "RXNORM", "C_RX_SCD"),
            ("2611783", "SCDG", "Red Yeast Rice", "RX_SCDG", "N", "RXNORM", "C_RX_SCDG"),
            ("393052", "SCDC", "oxygen 99 %", "RX_SCDC", "N", "RXNORM", "C_RX_SCDC"),
            ("7806", "IN", "oxygen", "RX_IN", "N", "RXNORM", "C_RX_IN"),
            ("3939301", "MIN", "oxygen MIN alternate", "RX_MIN", "N", "RXNORM", "C_RX_MIN"),
            # LOINC first-axis component.
            ("2345-7", "LN", "Glucose [Mass/volume] in Serum or Plasma", "LNC_GLU", "N", "LNC", "C_GLU_TEST"),
            ("LP14635-4", "LPDN", "Glucose", "LNC_GLU_PART", "N", "LNC", "C_GLU_PART"),
            # CPT fallback through HCPCS parent.
            ("99213", "PT", "Office outpatient visit", "CPT_99213", "N", "CPT", "C_VISIT"),
            ("MP_CPT", "MH", "Office Visit", "MP_CPT_AUI", "N", "MEDLINEPLUS", "C_VISIT"),
            ("G0463", "PT", "Hospital outpatient clinic visit", "HCPCS_G0463", "N", "HCPCS", "C_VISIT"),
            ("G0000", "PT", "Medical office visit parent", "HCPCS_PARENT", "N", "HCPCS", "C_VISIT_PARENT"),
            ("MP_VISIT", "MH", "Doctor's Office Visit", "MP_VISIT_AUI", "N", "MEDLINEPLUS", "C_VISIT_PARENT"),
            ("99214", "PT", "Office outpatient visit extended", "CPT_99214", "N", "CPT", "C_VISIT_X"),
            ("G0464", "PT", "Hospital outpatient clinic visit extended", "HCPCS_G0464", "N", "HCPCS", "C_VISIT_X"),
            # CVX original fallback; group behavior is tested separately.
            ("208", "PT", "COVID-19 vaccine", "CVX_208", "N", "CVX", "C_CVX_208"),
        ],
    )
    con.executemany(
        "INSERT INTO mrrel VALUES (?, ?, ?, ?)",
        [
            ("SN_100001", "SN_100002", "isa", "PAR"),
            ("RX_SCD", "RX_SCDG", "isa", "AUI"),
            ("RX_SCDC", "RX_IN", "isa", "AUI"),
            ("RX_SCDC", "RX_MIN", "isa", "AUI"),
            ("LNC_GLU", "LNC_GLU_PART", "component_of", "AUI"),
            ("HCPCS_G0463", "HCPCS_PARENT", "isa", "PAR"),
            ("HCPCS_G0464", "HCPCS_PARENT", "isa", "PAR"),
        ],
    )


def _patch_baseline_cvx(monkeypatch) -> None:
    if str(MEDTERM_SRC) not in sys.path:
        sys.path.insert(0, str(MEDTERM_SRC))
    import medterm.bulk.transforms.patient_friendly as baseline_pf

    monkeypatch.setattr(baseline_pf, "_CVX_GROUP_CACHE", {})
    monkeypatch.setattr(baseline_pf, "_load_cvx_groups", lambda: {})


def _semantic_rows(results):
    return {
        (r.code.source, r.code.code): {
            "code": r.code.code,
            "source": r.code.source,
            "name": r.name,
            "friendly_source": r.friendly_source,
            "match_type": r.match_type,
            "match_depth": r.match_depth,
        }
        for r in results
    }


def test_locallite_matches_medterm_bulk_on_representative_codes(monkeypatch):
    _patch_baseline_cvx(monkeypatch)
    con = duckdb.connect(database=":memory:")
    try:
        _seed_patient_friendly_db(con)
        codes = [
            CodeRef("ICD10CM", "E11.9"),
            CodeRef("ICD10PCS", "0FT44ZZ"),
            CodeRef("HCPCS", "J1815"),
            CodeRef("ICD10CM", "A00.0"),
            CodeRef("SNOMEDCT_US", "44054006"),
            CodeRef("RXNORM", "2611787"),
            CodeRef("RXNORM", "393052"),
            CodeRef("LNC", "2345-7"),
            CodeRef("CPT", "99213"),
            CodeRef("CVX", "208"),
        ]
        local = LocalLiteEngine(con)
        baseline = MedtermBulkBaselineEngine(con)

        local_rows = _semantic_rows(get_patient_friendly_names(codes, local))
        baseline_rows = _semantic_rows(get_patient_friendly_names(codes, baseline))

        assert local_rows == baseline_rows
    finally:
        con.close()


def test_locallite_adds_structured_provenance(monkeypatch):
    _patch_baseline_cvx(monkeypatch)
    con = duckdb.connect(database=":memory:")
    try:
        _seed_patient_friendly_db(con)
        engine = LocalLiteEngine(con)
        results = get_patient_friendly_names(
            [
                CodeRef("ICD10CM", "A00.0"),
                CodeRef("SNOMEDCT_US", "44054006"),
                CodeRef("RXNORM", "2611787"),
            ],
            engine,
        )

        by_code = {result.code.code: result for result in results}

        rare_path = by_code["A00.0"].matched_via.to_dict()
        assert rare_path["strategy"] == "source_snomed_fallback"
        assert [step["op"] for step in rare_path["steps"]] == [
            "input",
            "cross_reference",
            "ancestor",
            "friendly_atom",
        ]

        snomed_path = by_code["44054006"].matched_via.to_dict()
        assert snomed_path["strategy"] == "snomed_cross_reference"
        assert snomed_path["steps"][1]["target_source"] == "ICD10CM"

        rx_path = by_code["2611787"].matched_via.to_dict()
        assert rx_path["strategy"] == "rxnorm_tty"
        assert rx_path["steps"][1]["op"] == "tty_traversal"
    finally:
        con.close()


def test_prepared_cache_matches_unprepared_locallite():
    con = duckdb.connect(database=":memory:")
    try:
        _seed_patient_friendly_db(con)
        codes = [
            CodeRef("ICD10CM", "E11.9"),
            CodeRef("ICD10PCS", "0FT44ZZ"),
            CodeRef("HCPCS", "J1815"),
            CodeRef("ICD10CM", "A00.0"),
            CodeRef("SNOMEDCT_US", "44054006"),
            CodeRef("RXNORM", "2611787"),
            CodeRef("RXNORM", "393052"),
            CodeRef("LNC", "2345-7"),
            CodeRef("CPT", "99213"),
            CodeRef("CVX", "208"),
        ]

        unprepared = _semantic_rows(get_patient_friendly_names(codes, LocalLiteEngine(con)))

        prepared_engine = LocalLiteEngine(con)
        prepared_engine.prepare_cache(
            ["ICD10CM", "ICD10PCS", "HCPCS", "SNOMEDCT_US", "RXNORM", "LNC", "CVX", "CPT"],
            create_indexes=False,
        )
        prepared = _semantic_rows(get_patient_friendly_names(codes, prepared_engine))

        assert prepared == unprepared
    finally:
        con.close()


def test_locallite_cvx_group_without_network():
    con = duckdb.connect(database=":memory:")
    try:
        _seed_patient_friendly_db(con)
        engine = LocalLiteEngine(con, cvx_groups={"208": ["COVID-19"]})
        result = get_patient_friendly_names([CodeRef("CVX", "208")], engine)[0]

        assert result.name == "COVID-19"
        assert result.friendly_source == "CVX"
        assert result.match_type == "cvx_group"
        assert result.matched_via.to_dict()["steps"][1]["op"] == "vaccine_group"
    finally:
        con.close()


def test_locallite_cpt_crosswalks_to_hcpcs_parent():
    con = duckdb.connect(database=":memory:")
    try:
        _seed_patient_friendly_db(con)
        engine = LocalLiteEngine(con)
        result = get_patient_friendly_names([CodeRef("CPT", "99214")], engine)[0]

        assert result.name == "Doctor's Office Visit"
        assert result.friendly_source == "MEDLINEPLUS"
        assert result.match_type == "broader"
        path = result.matched_via.to_dict()
        assert path["strategy"] == "cpt_cross_reference"
        assert path["steps"][1]["target_source"] == "HCPCS"
        assert path["steps"][1]["target_code"] == "G0464"
    finally:
        con.close()
