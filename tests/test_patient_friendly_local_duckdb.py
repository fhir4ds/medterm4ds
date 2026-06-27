from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

from medterm4ds import CodeRef, get_patient_friendly_names
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.engines.duckdb import engine as duckdb_engine



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
            # ICD category display should prefer the heading over expansion terms.
            ("K45", "ET", "sciatic hernia", "ICD_K45_ET1", "N", "ICD10CM", "C_K45_ET1"),
            ("K45", "ET", "obturator hernia", "ICD_K45_ET2", "N", "ICD10CM", "C_K45_ET2"),
            ("K45", "HT", "Other Abdominal Hernia", "ICD_K45_HT", "N", "ICD10CM", "C_K45"),
            # ICD parent hierarchy relies on explicit UMLS PAR edges in this fixture.
            ("L30.1", "PT", "Dyshidrosis [pompholyx]", "ICD_L301", "N", "ICD10CM", "C_L301"),
            ("L30", "HT", "Other and unspecified dermatitis", "ICD_L30", "N", "ICD10CM", "C_L30"),
            ("L20-L30", "HT", "Dermatitis and eczema (L20-L30)", "ICD_L20L30", "N", "ICD10CM", "C_L20L30"),
            ("CHV_DERM", "PT", "Dermatitis and Eczema", "CHV_DERM_AUI", "N", "CHV", "C_L20L30"),
            ("S37.06", "HT", "Major laceration of kidney", "ICD_S3706", "N", "ICD10CM", "C_KIDNEY_MAJOR"),
            ("S37.0", "HT", "Injury of kidney", "ICD_S370", "N", "ICD10CM", "C_KIDNEY_INJURY"),
            ("CHV_KIDNEY", "PT", "Kidney Injury", "CHV_KIDNEY_PT", "N", "CHV", "C_KIDNEY_INJURY"),
            ("CHV_KIDNEY", "SY", "injuries kidney", "CHV_KIDNEY_SY", "N", "CHV", "C_KIDNEY_INJURY"),
            # ICD parent can bridge to SNOMED, then a SNOMED ancestor can supply MEDLINEPLUS.
            ("M99.75", "PT", "Connective tissue and disc stenosis of intervertebral foramina of pelvic region", "ICD_M9975", "N", "ICD10CM", "C_M9975"),
            ("M99.7", "HT", "Connective tissue and disc stenosis of intervertebral foramina", "ICD_M997", "N", "ICD10CM", "C_SN_FORAMEN"),
            ("203715007", "PT", "Connective tissue and disc stenosis of intervertebral foramina", "SN_FORAMEN", "N", "SNOMEDCT_US", "C_SN_FORAMEN"),
            ("76069003", "PT", "Disorder of bone", "SN_BONE", "N", "SNOMEDCT_US", "C_BONE"),
            ("MP_BONE", "PT", "Bone Diseases", "MP_BONE_AUI", "N", "MEDLINEPLUS", "C_BONE"),
            ("CHV_BONE", "PT", "bone disease", "CHV_BONE_AUI", "N", "CHV", "C_BONE"),
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
            # Default source hierarchy frontier: shallow CHV beats deeper MEDLINEPLUS.
            ("B99.9", "PT", "Unspecified infectious disease child", "ICD_B999", "N", "ICD10CM", "C_ICD_FRONTIER"),
            ("B99", "PT", "Infectious disease parent", "ICD_B99", "N", "ICD10CM", "C_ICD_FRONTIER_PARENT"),
            ("CHV_FRONTIER", "PT", "Shallow CHV Infection", "CHV_FRONTIER_AUI", "N", "CHV", "C_ICD_FRONTIER"),
            ("MP_FRONTIER_PARENT", "MH", "Deeper MEDLINE infection", "MP_FRONTIER_PARENT_AUI", "N", "MEDLINEPLUS", "C_ICD_FRONTIER_PARENT"),
            ("B98.9", "PT", "Unspecified infectious condition child", "ICD_B989", "N", "ICD10CM", "C_ICD_CHD_CHILD"),
            ("B98", "HT", "Infectious condition parent", "ICD_B98", "N", "ICD10CM", "C_ICD_CHD_PARENT"),
            ("MP_CHD_PARENT", "MH", "Parent Infection", "MP_CHD_PARENT_AUI", "N", "MEDLINEPLUS", "C_ICD_CHD_PARENT"),
            # SNOMED maps to ICD, then inherits ICD friendly name.
            ("44054006", "PT", "Diabetes mellitus type 2", "SN_DIAB", "N", "SNOMEDCT_US", "C_DIAB"),
            # SNOMED original fallback should prefer the preferred term over fully specified names.
            ("900001", "FN", "Clean SNOMED display (qualifier value)", "SN_ORIG_FN", "N", "SNOMEDCT_US", "C_SN_ORIG"),
            ("900001", "PT", "Clean SNOMED Display", "SN_ORIG_PT", "N", "SNOMEDCT_US", "C_SN_ORIG"),
            ("900001", "SY", "SNOMED synonym display", "SN_ORIG_SY", "N", "SNOMEDCT_US", "C_SN_ORIG"),
            # RxNorm group and ingredient paths.
            ("2611787", "SCD", "Child drug product", "RX_SCD", "N", "RXNORM", "C_RX_SCD"),
            ("2611783", "SCDG", "Red Yeast Rice", "RX_SCDG", "N", "RXNORM", "C_RX_SCDG"),
            ("393052", "SCDC", "oxygen 99 %", "RX_SCDC", "N", "RXNORM", "C_RX_SCDC"),
            ("7806", "IN", "Oxygen", "RX_IN", "N", "RXNORM", "C_RX_IN"),
            ("3939301", "MIN", "Oxygen MIN Alternate", "RX_MIN", "N", "RXNORM", "C_RX_MIN"),
            ("990001", "PIN", "oxygen precise ingredient", "RX_PIN", "N", "RXNORM", "C_RX_PIN"),
            ("990010", "SCDF", "example auto-injector", "RX_SCDF_REAL", "N", "RXNORM", "C_RX_SCDF_REAL"),
            ("990011", "SCDG", "Example Injectable Product", "RX_SCDG_REAL", "N", "RXNORM", "C_RX_SCDG_REAL"),
            ("990020", "SBDF", "example branded injection", "RX_SBDF_REAL", "N", "RXNORM", "C_RX_SBDF_REAL"),
            ("200001", "SBD", "Brand clinical tablet", "RX_SBD", "N", "RXNORM", "C_RX_SBD"),
            ("200002", "SCD", "Clinical tablet", "RX_SBD_SCD", "N", "RXNORM", "C_RX_SBD_SCD"),
            ("200003", "SCDG", "Clinical Oral Product", "RX_SBD_SCDG", "N", "RXNORM", "C_RX_SBD_SCDG"),
            ("200010", "SBDC", "Brand ingredient component", "RX_SBDC", "N", "RXNORM", "C_RX_SBDC"),
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
            # HCPCS fallback through SNOMED when no HCPCS MP/CHV exists.
            ("J9999", "PT", "Unmapped HCPCS drug", "HCPCS_SN", "N", "HCPCS", "C_HCPCS_SN"),
            ("400001", "PT", "HCPCS SNOMED child", "SN_HCPCS_CHILD", "N", "SNOMEDCT_US", "C_HCPCS_SN"),
            ("400002", "PT", "HCPCS SNOMED parent", "SN_HCPCS_PARENT", "N", "SNOMEDCT_US", "C_HCPCS_PARENT"),
            ("MP_HCPCS_SN", "MH", "Helpful HCPCS Concept", "MP_HCPCS_SN_AUI", "N", "MEDLINEPLUS", "C_HCPCS_PARENT"),
            # CPT fallback through CPT hierarchy to SNOMED when same-CUI crosswalk misses.
            ("77777", "PT", "Unfriendly CPT child", "CPT_SN_CHILD", "N", "CPT", "C_CPT_SN_CHILD"),
            ("77770", "PT", "Unfriendly CPT parent", "CPT_SN_PARENT", "N", "CPT", "C_CPT_SN_PARENT"),
            ("300001", "PT", "Mapped SNOMED procedure", "SN_CPT_PARENT", "N", "SNOMEDCT_US", "C_CPT_SN_PARENT"),
            ("300002", "PT", "Helpful SNOMED procedure parent", "SN_CPT_PARENT2", "N", "SNOMEDCT_US", "C_CPT_SN_PARENT2"),
            ("MP_CPT_SN", "MH", "Helpful Procedure", "MP_CPT_SN_AUI", "N", "MEDLINEPLUS", "C_CPT_SN_PARENT2"),
            # CPT exact friendly labels may live on non-display ETCLIN atoms.
            ("88888", "PT", "Generic CPT multi-atom procedure", "CPT_MULTI_PT", "N", "CPT", "C_CPT_MULTI_PT"),
            ("88888", "ETCLIN", "Specific knee injection", "CPT_MULTI_ET", "N", "CPT", "C_CPT_MULTI_ET"),
            ("CHV_CPT_MULTI", "PT", "Knee Injection", "CHV_CPT_MULTI_AUI", "N", "CHV", "C_CPT_MULTI_ET"),
            # CPT original fallback should use a deterministic source-specific display.
            ("0009M", "PT", "Long CPT molecular pathology panel description", "CPT_ORIG_PT", "N", "CPT", "C_CPT_ORIG_M"),
            ("0009M", "SY", "CPT MOLECULAR PANEL", "CPT_ORIG_SY", "N", "CPT", "C_CPT_ORIG_M"),
            ("0009M", "ETCF", "Molecular Pathology Panel", "CPT_ORIG_ETCF", "N", "CPT", "C_CPT_ORIG_M"),
            ("90001", "PT", "Long CPT Procedure Display", "CPT_ORIG2_PT", "N", "CPT", "C_CPT_ORIG_SHORT"),
            ("90001", "SY", "CPT SHORT DISPLAY", "CPT_ORIG2_SY", "N", "CPT", "C_CPT_ORIG_SHORT"),
            # CVX original fallback; group behavior is tested separately.
            ("208", "PT", "COVID-19 vaccine", "CVX_208", "N", "CVX", "C_CVX_208"),
            ("11", "AB", "pertussis", "CVX_11_AB", "N", "CVX", "C_CVX_11"),
            ("11", "PT", "Pertussis Vaccine", "CVX_11_PT", "N", "CVX", "C_CVX_11"),
        ],
    )
    con.executemany(
        "INSERT INTO mrrel VALUES (?, ?, ?, ?)",
        [
            ("SN_100001", "SN_100002", "isa", "PAR"),
            ("ICD_S3706", "ICD_S370", None, "PAR"),
            ("ICD_L301", "ICD_L30", None, "PAR"),
            ("ICD_L30", "ICD_L20L30", None, "PAR"),
            ("ICD_M9975", "ICD_M997", None, "PAR"),
            ("RX_SCD", "RX_SCDG", "isa", "AUI"),
            ("RX_SCDC", "RX_IN", "isa", "AUI"),
            ("RX_SCDC", "RX_MIN", "isa", "AUI"),
            ("RX_PIN", "RX_SCDC", "isa", "AUI"),
            ("RX_SBD", "RX_SBD_SCD", "tradename_of", "RO"),
            ("RX_SBD_SCD", "RX_SBD_SCDG", "dose_form_of", "RO"),
            ("RX_SBDC", "RX_SBD", "has_tradename", "RO"),
            ("LNC_GLU", "LNC_GLU_PART", "component_of", "AUI"),
            ("ICD_B99", "ICD_B999", None, "CHD"),
            ("ICD_B98", "ICD_B989", None, "CHD"),
            ("RX_SBDF_REAL", "RX_SCDF_REAL", "has_tradename", "RB"),
            ("RX_SCDF_REAL", "RX_SCDG_REAL", "inverse_isa", "RB"),
            ("HCPCS_G0463", "HCPCS_PARENT", "isa", "PAR"),
            ("HCPCS_G0464", "HCPCS_PARENT", "isa", "PAR"),
            ("SN_HCPCS_CHILD", "SN_HCPCS_PARENT", "isa", "PAR"),
            ("CPT_SN_CHILD", "CPT_SN_PARENT", "isa", "PAR"),
            ("SN_CPT_PARENT", "SN_CPT_PARENT2", "isa", "PAR"),
            ("SN_FORAMEN", "SN_BONE", "inverse_isa", "PAR"),
        ],
    )


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




def test_local_duckdb_adds_structured_provenance():
    con = duckdb.connect(database=":memory:")
    try:
        _seed_patient_friendly_db(con)
        engine = LocalDuckDBEngine(con)
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


def test_prepared_cache_matches_unprepared_local_duckdb():
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

        unprepared = _semantic_rows(get_patient_friendly_names(codes, LocalDuckDBEngine(con)))

        prepared_engine = LocalDuckDBEngine(con)
        prepared_engine.prepare_cache(
            ["ICD10CM", "ICD10PCS", "HCPCS", "SNOMEDCT_US", "RXNORM", "LNC", "CVX", "CPT"],
            create_indexes=False,
        )
        prepared = _semantic_rows(get_patient_friendly_names(codes, prepared_engine))

        assert prepared == unprepared
    finally:
        con.close()


def test_local_duckdb_cvx_group_without_network():
    con = duckdb.connect(database=":memory:")
    try:
        _seed_patient_friendly_db(con)
        engine = LocalDuckDBEngine(con, cvx_groups={"208": ["COVID-19"]})
        result = get_patient_friendly_names([CodeRef("CVX", "208")], engine)[0]

        assert result.name == "COVID-19"
        assert result.friendly_source == "CVX"
        assert result.match_type == "cvx_group"
        assert result.matched_via.to_dict()["steps"][1]["op"] == "vaccine_group"
    finally:
        con.close()


def test_local_duckdb_loads_default_cvx_groups(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_exc_info):
            return False

        def read(self):
            return b"VGID|208|ignore|COVID-19|ignore\nVGID|208|ignore|COVID-19|ignore\n"

    monkeypatch.delenv("MEDTERM4DS_DISABLE_CVX_GROUPS", raising=False)
    monkeypatch.setattr(duckdb_engine, "_CVX_GROUP_CACHE", None)
    monkeypatch.setattr(duckdb_engine.urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())
    con = duckdb.connect(database=":memory:")
    try:
        _seed_patient_friendly_db(con)
        engine = LocalDuckDBEngine(con)
        result = get_patient_friendly_names([CodeRef("CVX", "208")], engine)[0]

        assert result.name == "COVID-19"
        assert result.match_type == "cvx_group"
    finally:
        con.close()


def test_local_duckdb_prefers_source_specific_original_displays():
    con = duckdb.connect(database=":memory:")
    try:
        _seed_patient_friendly_db(con)
        engine = LocalDuckDBEngine(con)
        snomed, cpt_m, cpt_short, cvx, icd_heading = get_patient_friendly_names(
            [
                CodeRef("SNOMEDCT_US", "900001"),
                CodeRef("CPT", "0009M"),
                CodeRef("CPT", "90001"),
                CodeRef("CVX", "11"),
                CodeRef("ICD10CM", "K45"),
            ],
            engine,
        )

        assert snomed.name == "Clean SNOMED Display"
        assert snomed.technical_name == "Clean SNOMED display (qualifier value)"
        assert cpt_m.name == "Molecular Pathology Panel"
        assert cpt_short.name == "Long CPT Procedure Display"
        assert cvx.name == "Pertussis Vaccine"
        assert icd_heading.name == "Other Abdominal Hernia"
    finally:
        con.close()


def test_local_duckdb_uses_icd_umls_parent_hierarchy():
    con = duckdb.connect(database=":memory:")
    try:
        _seed_patient_friendly_db(con)
        engine = LocalDuckDBEngine(con)
        result = get_patient_friendly_names([CodeRef("ICD10CM", "S37.06")], engine)[0]

        assert (result.name, result.friendly_source, result.match_type, result.match_depth) == (
            "Kidney Injury",
            "CHV",
            "broader",
            1,
        )
    finally:
        con.close()


def test_local_duckdb_uses_icd_parent_range_hierarchy():
    con = duckdb.connect(database=":memory:")
    try:
        _seed_patient_friendly_db(con)
        engine = LocalDuckDBEngine(con)
        result = get_patient_friendly_names([CodeRef("ICD10CM", "L30.1")], engine)[0]

        assert (result.name, result.friendly_source, result.match_type, result.match_depth) == (
            "Dermatitis and Eczema",
            "CHV",
            "broader",
            2,
        )
    finally:
        con.close()


def test_local_duckdb_uses_icd_umls_parent_to_snomed_broader_medlineplus():
    con = duckdb.connect(database=":memory:")
    try:
        _seed_patient_friendly_db(con)
        engine = LocalDuckDBEngine(con)
        result = get_patient_friendly_names([CodeRef("ICD10CM", "M99.75")], engine)[0]

        assert (result.name, result.friendly_source, result.match_type, result.match_depth) == (
            "Bone Diseases",
            "MEDLINEPLUS",
            "broader",
            2,
        )
        path = result.matched_via.to_dict()
        assert path["steps"][1]["target_code"] == "203715007"
        assert path["steps"][2]["code"] == "76069003"
    finally:
        con.close()


def test_local_duckdb_rxnorm_uses_tty_topology_not_isa_only():
    con = duckdb.connect(database=":memory:")
    try:
        _seed_patient_friendly_db(con)
        engine = LocalDuckDBEngine(con)
        (
            sbd,
            sbdc,
            scdc,
            ingredient,
            multi_ingredient,
            precise_ingredient,
            real_style_group,
            real_style_fallback_group,
        ) = get_patient_friendly_names(
            [
                CodeRef("RXNORM", "200001"),
                CodeRef("RXNORM", "200010"),
                CodeRef("RXNORM", "393052"),
                CodeRef("RXNORM", "7806"),
                CodeRef("RXNORM", "3939301"),
                CodeRef("RXNORM", "990001"),
                CodeRef("RXNORM", "990010"),
                CodeRef("RXNORM", "990020"),
            ],
            engine,
        )

        assert (sbd.name, sbd.match_type, sbd.match_depth) == (
            "Clinical Oral Product",
            "group",
            2,
        )
        assert (sbdc.name, sbdc.match_type, sbdc.match_depth) == (
            "Clinical Oral Product",
            "group",
            3,
        )
        assert (scdc.name, scdc.match_type, scdc.match_depth) == (
            "Oxygen",
            "ingredient",
            1,
        )
        assert (ingredient.name, ingredient.match_type, ingredient.match_depth) == (
            "Oxygen",
            "ingredient",
            0,
        )
        assert (multi_ingredient.name, multi_ingredient.match_type, multi_ingredient.match_depth) == (
            "Oxygen MIN Alternate",
            "ingredient",
            0,
        )
        assert (precise_ingredient.name, precise_ingredient.match_type, precise_ingredient.match_depth) == (
            "Oxygen",
            "ingredient",
            2,
        )
        assert (real_style_group.name, real_style_group.match_type, real_style_group.match_depth) == (
            "Example Injectable Product",
            "group",
            1,
        )
        assert (
            real_style_fallback_group.name,
            real_style_fallback_group.match_type,
            real_style_fallback_group.match_depth,
        ) == (
            "Example Injectable Product",
            "group",
            2,
        )
    finally:
        con.close()


def test_local_duckdb_rxnorm_falls_back_through_suppressed_intermediate_nodes():
    con = duckdb.connect(database=":memory:")
    try:
        _init_schema(con)
        con.execute(
            """
            INSERT INTO mrconso VALUES
                ('500001', 'SBD', 'hyoscyamine tablet', 'RX_SUPP_SBD', 'N', 'RXNORM', 'C_RX_SUPP'),
                ('500001', 'SCD', 'suppressed intermediate node', 'RX_SUPP_SCD_E', 'E', 'RXNORM', 'C_RX_SUPP'),
                ('500002', 'SCDG', 'suppressed-chain product', 'RX_SUPP_SCDG', 'N', 'RXNORM', 'C_RX_SUPP_G')
            """
        )
        con.execute(
            """
            INSERT INTO mrrel VALUES
                ('RX_SUPP_SBD', 'RX_SUPP_SCD_E', 'has_quantified_form', 'RB'),
                ('RX_SUPP_SCD_E', 'RX_SUPP_SCDG', 'inverse_isa', 'RB')
            """
        )

        engine = LocalDuckDBEngine(con)
        row = get_patient_friendly_names([CodeRef("RXNORM", "500001")], engine)[0]
        assert (row.name, row.match_type, row.match_depth, row.friendly_source) == (
            "Suppressed-Chain Product",
            "group",
            2,
            "RXNORM",
        )
    finally:
        con.close()


def test_local_duckdb_rxnorm_follows_incoming_tty_topology_edges():
    con = duckdb.connect(database=":memory:")
    try:
        _init_schema(con)
        con.executemany(
            "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("1000083", "SCDC", "alcaftadine 2.5 MG/ML", "RX_ALC_SCDC", "N", "RXNORM", "C_ALC_DOSE"),
                ("1000082", "IN", "Alcaftadine", "RX_ALC_IN", "N", "RXNORM", "C_ALC_IN"),
            ],
        )
        con.execute(
            "INSERT INTO mrrel VALUES ('RX_ALC_IN', 'RX_ALC_SCDC', 'has_ingredient', 'RO')"
        )

        engine = LocalDuckDBEngine(con)
        row = get_patient_friendly_names([CodeRef("RXNORM", "1000083")], engine)[0]

        assert (row.name, row.friendly_source, row.match_type, row.match_depth) == (
            "Alcaftadine",
            "RXNORM",
            "ingredient",
            1,
        )
    finally:
        con.close()


def test_local_duckdb_rxnorm_prefers_active_final_target_over_suppressed():
    con = duckdb.connect(database=":memory:")
    try:
        _init_schema(con)
        con.execute(
            """
            INSERT INTO mrconso VALUES
                ('700000', 'SBDC', 'start drug', 'RX_START', 'N', 'RXNORM', 'C_START'),
                ('900001', 'IN', 'active ingredient', 'RX_ACTIVE_IN', 'N', 'RXNORM', 'C_ACTIVE_IN'),
                ('0001', 'IN', 'suppressed ingredient', 'RX_SUPP_IN', 'Y', 'RXNORM', 'C_SUPP_IN')
            """
        )
        con.execute(
            """
            INSERT INTO mrrel VALUES
                ('RX_START', 'RX_ACTIVE_IN', 'has_active_ingredient', 'RB'),
                ('RX_START', 'RX_SUPP_IN', 'has_suppressed_ingredient', 'RB')
            """
        )

        engine = LocalDuckDBEngine(con)
        row = get_patient_friendly_names([CodeRef("RXNORM", "700000")], engine)[0]
        assert (row.name, row.match_type, row.match_depth, row.friendly_source) == (
            "Active Ingredient",
            "ingredient",
            1,
            "RXNORM",
        )
    finally:
        con.close()


def test_local_duckdb_filters_broad_snomed_service_chv_fallback():
    con = duckdb.connect(database=":memory:")
    try:
        _init_schema(con)
        con.executemany(
            "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("1000001000124105", "PT", "United States Department of Agriculture Rural Mortgage Program", "SN_SERVICE_CHILD", "N", "SNOMEDCT_US", "C_SERVICE_CHILD"),
                ("224930009", "PT", "Services", "SN_SERVICE_PARENT", "N", "SNOMEDCT_US", "C_SERVICE_PARENT"),
                ("0000039438", "PT", "service", "CHV_SERVICE", "N", "CHV", "C_SERVICE_PARENT"),
            ],
        )
        con.execute(
            "INSERT INTO mrrel VALUES ('SN_SERVICE_CHILD', 'SN_SERVICE_PARENT', 'inverse_isa', 'PAR')"
        )

        engine = LocalDuckDBEngine(con)
        row = get_patient_friendly_names([CodeRef("SNOMEDCT_US", "1000001000124105")], engine)[0]

        assert (row.name, row.friendly_source, row.match_type) == (
            "United States Department of Agriculture Rural Mortgage Program",
            "SNOMEDCT_US",
            "original",
        )
    finally:
        con.close()


def test_local_duckdb_does_not_route_snomed_ancestor_to_rxnorm():
    con = duckdb.connect(database=":memory:")
    try:
        _init_schema(con)
        con.executemany(
            "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("1000001000004108", "PT", "Mismatch Repair Endonuclease PMS2", "SN_PMS2", "N", "SNOMEDCT_US", "C_PMS2"),
                ("74628008", "PT", "Hydrolase", "SN_HYDROLASE", "N", "SNOMEDCT_US", "C_HYDROLASE"),
                ("1156", "IN", "asparaginase", "RX_ASPARAGINASE", "N", "RXNORM", "C_HYDROLASE"),
            ],
        )
        con.execute("INSERT INTO mrrel VALUES ('SN_PMS2', 'SN_HYDROLASE', 'isa', 'PAR')")

        engine = LocalDuckDBEngine(con)
        row = get_patient_friendly_names([CodeRef("SNOMEDCT_US", "1000001000004108")], engine)[0]

        assert (row.name, row.friendly_source, row.match_type) == (
            "Mismatch Repair Endonuclease PMS2",
            "SNOMEDCT_US",
            "original",
        )
    finally:
        con.close()


def _init_mrsty(con: duckdb.DuckDBPyConnection) -> None:
    """Create the mrsty table for tests that exercise TUI-based routing."""
    con.execute(
        """
        CREATE TABLE mrsty (
            cui VARCHAR,
            tui VARCHAR,
            sty VARCHAR
        )
        """
    )


def test_local_duckdb_snomed_substance_routes_to_rxnorm_with_mrsty():
    """A SNOMED concept whose CUI has T121 (Pharmacologic Substance) should
    route to RXNORM, not LNC, when MRSTY is loaded."""
    con = duckdb.connect(database=":memory:")
    try:
        _init_schema(con)
        _init_mrsty(con)
        con.executemany(
            "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("SN_DRUG", "PT", "Phenylephrine", "SN_PHE", "N", "SNOMEDCT_US", "C_PHE"),
                ("8163", "IN", "phenylephrine", "RX_PHE", "N", "RXNORM", "C_PHE"),
                ("LP_PHE", "LPN", "Phenylephrine", "LNC_PHE", "N", "LNC", "C_PHE"),
            ],
        )
        con.executemany(
            "INSERT INTO mrsty VALUES (?, ?, ?)",
            [
                ("C_PHE", "T121", "Pharmacologic Substance"),
                ("C_PHE", "T109", "Organic Chemical"),
            ],
        )

        engine = LocalDuckDBEngine(con)
        row = get_patient_friendly_names([CodeRef("SNOMEDCT_US", "SN_DRUG")], engine)[0]

        # Should resolve via RXNORM ingredient, not LNC.
        assert row.friendly_source == "RXNORM", row
        assert row.name == "Phenylephrine"
    finally:
        con.close()


def test_local_duckdb_snomed_vaccine_routes_to_cvx_with_mrsty():
    """A SNOMED vaccine concept sharing a CUI with a CVX atom should route to
    CVX regardless of TUI (vaccines share generic substance TUIs)."""
    con = duckdb.connect(database=":memory:")
    try:
        _init_schema(con)
        _init_mrsty(con)
        con.executemany(
            "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("SN_VAC", "PT", "Pneumococcal vaccine", "SN_VAC_AUI", "N", "SNOMEDCT_US", "C_VAC"),
                ("33", "PT", "pneumococcal", "CVX_VAC_AUI", "N", "CVX", "C_VAC"),
                ("33_RX", "IN", "Pneumococcal Vaccine", "RX_VAC_AUI", "N", "RXNORM", "C_VAC"),
            ],
        )
        con.executemany(
            "INSERT INTO mrsty VALUES (?, ?, ?)",
            [
                ("C_VAC", "T121", "Pharmacologic Substance"),
            ],
        )

        engine = LocalDuckDBEngine(con)
        # Reach into the engine's internal mapping to verify routing.
        mapping = engine._map_snomed_codes(["SN_VAC"])
        assert mapping["SN_VAC"][0] == "CVX", mapping["SN_VAC"]
    finally:
        con.close()


def test_local_duckdb_snomed_disease_still_routes_to_icd10cm_with_mrsty():
    """A SNOMED disease concept should still route to ICD10CM when MRSTY is
    loaded. Regression guard for the TUI-based routing change."""
    con = duckdb.connect(database=":memory:")
    try:
        _init_schema(con)
        _init_mrsty(con)
        con.executemany(
            "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("SN_DX", "PT", "Type 2 diabetes mellitus", "SN_DX_AUI", "N", "SNOMEDCT_US", "C_DX"),
                ("E11", "HT", "Type 2 diabetes mellitus", "ICD_E11_AUI", "N", "ICD10CM", "C_DX"),
            ],
        )
        con.executemany(
            "INSERT INTO mrsty VALUES (?, ?, ?)",
            [
                ("C_DX", "T047", "Disease or Syndrome"),
            ],
        )

        engine = LocalDuckDBEngine(con)
        mapping = engine._map_snomed_codes(["SN_DX"])
        assert mapping["SN_DX"][0] == "ICD10CM", mapping["SN_DX"]
    finally:
        con.close()


def test_local_duckdb_snomed_procedure_routes_to_cpt_with_mrsty():
    """A SNOMED therapeutic procedure should route to CPT, not RXNORM or LNC."""
    con = duckdb.connect(database=":memory:")
    try:
        _init_schema(con)
        _init_mrsty(con)
        con.executemany(
            "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("SN_PROC", "PT", "Appendectomy", "SN_PROC_AUI", "N", "SNOMEDCT_US", "C_PROC"),
                ("44970", "PT", "Laparoscopic appendectomy", "CPT_AUI", "N", "CPT", "C_PROC"),
                ("0DB68ZX", "PT", "Excision of appendix", "ICDPCS_AUI", "N", "ICD10PCS", "C_PROC"),
            ],
        )
        con.executemany(
            "INSERT INTO mrsty VALUES (?, ?, ?)",
            [
                ("C_PROC", "T061", "Therapeutic or Preventive Procedure"),
            ],
        )

        engine = LocalDuckDBEngine(con)
        mapping = engine._map_snomed_codes(["SN_PROC"])
        # Both CPT and ICD10PCS are allowed; priority picks ICD10PCS first.
        assert mapping["SN_PROC"][0] in {"CPT", "ICD10PCS"}, mapping["SN_PROC"]
    finally:
        con.close()


def test_local_duckdb_snomed_pure_protein_does_not_route_to_rxnorm_with_mrsty():
    """A SNOMED protein/gene concept (T116 alone, no T121) should NOT route to
    RXNORM even when a CUI crosswalk exists. Mirrors the PMS2 case but with
    MRSTY loaded: TUI filter excludes the crosswalk."""
    con = duckdb.connect(database=":memory:")
    try:
        _init_schema(con)
        _init_mrsty(con)
        con.executemany(
            "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("SN_PMS2", "PT", "Mismatch Repair Endonuclease PMS2", "SN_PMS2_AUI", "N", "SNOMEDCT_US", "C_PMS2"),
                ("1156", "IN", "asparaginase", "RX_ASPARAGINASE", "N", "RXNORM", "C_PMS2"),
            ],
        )
        con.executemany(
            "INSERT INTO mrsty VALUES (?, ?, ?)",
            [
                # T116 alone — a protein, but NOT a Pharmacologic Substance.
                ("C_PMS2", "T116", "Amino Acid, Peptide, or Protein"),
            ],
        )

        engine = LocalDuckDBEngine(con)
        mapping = engine._map_snomed_codes(["SN_PMS2"])
        # PMS2 should not route anywhere (no compatible TUI, no CVX crosswalk).
        assert "SN_PMS2" not in mapping or mapping["SN_PMS2"][0] != "RXNORM", mapping
    finally:
        con.close()


def test_local_duckdb_cpt_walks_rela_isa_hierarchy():
    con = duckdb.connect(database=":memory:")
    try:
        _init_schema(con)
        con.executemany(
            "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("77777", "PT", "Unfriendly CPT child", "CPT_CHILD", "N", "CPT", "C_CPT_CHILD"),
                ("77770", "PT", "Helpful CPT parent", "CPT_PARENT", "N", "CPT", "C_CPT_PARENT"),
                ("MP_CPT_PARENT", "MH", "Helpful Procedure", "MP_CPT_PARENT", "N", "MEDLINEPLUS", "C_CPT_PARENT"),
            ],
        )
        con.execute("INSERT INTO mrrel VALUES ('CPT_CHILD', 'CPT_PARENT', 'isa', 'RB')")

        engine = LocalDuckDBEngine(con)
        row = get_patient_friendly_names([CodeRef("CPT", "77777")], engine)[0]

        assert (row.name, row.friendly_source, row.match_type, row.match_depth) == (
            "Helpful Procedure",
            "MEDLINEPLUS",
            "broader",
            1,
        )
    finally:
        con.close()


def test_default_family_frontier_and_snomed_fallback_policy():
    con = duckdb.connect(database=":memory:")
    try:
        _seed_patient_friendly_db(con)
        engine = LocalDuckDBEngine(con)
        icd, icd_parent, hcpcs, cpt, cpt_exact_multi_atom = get_patient_friendly_names(
            [
                CodeRef("ICD10CM", "B99.9"),
                CodeRef("ICD10CM", "B98.9"),
                CodeRef("HCPCS", "J9999"),
                CodeRef("CPT", "77777"),
                CodeRef("CPT", "88888"),
            ],
            engine,
        )

        assert (icd.name, icd.friendly_source, icd.match_type, icd.match_depth) == (
            "Shallow CHV Infection",
            "CHV",
            "exact",
            0,
        )
        assert (icd_parent.name, icd_parent.friendly_source, icd_parent.match_type, icd_parent.match_depth) == (
            "Parent Infection",
            "MEDLINEPLUS",
            "broader",
            1,
        )

        assert (hcpcs.name, hcpcs.friendly_source, hcpcs.match_type, hcpcs.match_depth) == (
            "Helpful HCPCS Concept",
            "MEDLINEPLUS",
            "broader",
            1,
        )
        hcpcs_path = hcpcs.matched_via.to_dict()
        assert hcpcs_path["strategy"] == "source_snomed_fallback"
        assert hcpcs_path["steps"][1]["target_source"] == "SNOMEDCT_US"

        assert (cpt.name, cpt.friendly_source, cpt.match_type, cpt.match_depth) == (
            "Helpful Procedure",
            "MEDLINEPLUS",
            "broader",
            2,
        )
        cpt_path = cpt.matched_via.to_dict()
        assert cpt_path["strategy"] == "source_snomed_fallback"
        assert cpt_path["steps"][1]["target_source"] == "SNOMEDCT_US"
        assert (
            cpt_exact_multi_atom.name,
            cpt_exact_multi_atom.friendly_source,
            cpt_exact_multi_atom.match_type,
            cpt_exact_multi_atom.match_depth,
        ) == (
            "Knee Injection",
            "CHV",
            "exact",
            0,
        )
    finally:
        con.close()


def test_local_duckdb_cpt_crosswalks_to_hcpcs_parent():
    con = duckdb.connect(database=":memory:")
    try:
        _seed_patient_friendly_db(con)
        engine = LocalDuckDBEngine(con)
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
