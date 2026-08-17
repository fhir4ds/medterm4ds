from __future__ import annotations

import duckdb

from medterm4ds import CodeRef, get_code_info, get_code_infos
from medterm4ds.engines.duckdb import LocalDuckDBEngine


def _make_lookup_db(con: duckdb.DuckDBPyConnection) -> None:
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
            ("E11.9", "SY", "Diabetes synonym", "ICD_SY", "N", "ICD10CM", "C_DIAB"),
            ("E11.9", "PT", "Type 2 diabetes mellitus", "ICD_PT", "N", "ICD10CM", "C_DIAB"),
            ("E11.9", "PT", "Suppressed diabetes", "ICD_SUP", "Y", "ICD10CM", "C_DIAB_SUP"),
            ("S1", "PT", "Suppressed only", "ICD_SUP_ONLY", "Y", "ICD10CM", "C_SUP_ONLY"),
            ("208", "PT", "COVID-19 vaccine", "CVX_208", "N", "CVX", "C_CVX"),
            ("2345-7", "LN", "Glucose [Mass/volume] in Serum or Plasma", "LNC_GLU", "N", "LNC", "C_GLU"),
        ],
    )


def test_get_code_infos_preserves_order_and_missing_values():
    con = duckdb.connect(database=":memory:")
    try:
        _make_lookup_db(con)
        engine = LocalDuckDBEngine(con)

        infos = get_code_infos(
            [
                CodeRef("CVX", "208"),
                ("ICD10-CM", "E11.9"),
                CodeRef("ICD10CM", "NOPE"),
                CodeRef("ICD10CM", "S1"),
                ("LOINC", "2345-7"),
            ],
            engine=engine,
        )
    finally:
        con.close()

    assert infos[0].to_dict() == {
        "source": "CVX",
        "code": "208",
        "name": "COVID-19 vaccine",
        "cui": "C_CVX",
        "aui": "CVX_208",
        "tty": "PT",
        "suppress": "N",
    }
    assert infos[1].name == "Type 2 diabetes mellitus"
    assert infos[1].tty == "PT"
    assert infos[1].suppress == "N"
    assert infos[2] is None
    assert infos[3] is None
    assert infos[4].code == CodeRef("LNC", "2345-7")


def test_get_code_info_single_lookup():
    con = duckdb.connect(database=":memory:")
    try:
        _make_lookup_db(con)
        engine = LocalDuckDBEngine(con)
        info = get_code_info(CodeRef("ICD10CM", "E11.9"), engine=engine)
    finally:
        con.close()

    assert info.name == "Type 2 diabetes mellitus"
    assert info.cui == "C_DIAB"


def test_get_code_infos_uses_prepared_best_atoms_active_only():
    con = duckdb.connect(database=":memory:")
    try:
        con.execute("CREATE SCHEMA mt4ds")
        con.execute(
            """
            CREATE TABLE mt4ds.best_atoms (
                source VARCHAR,
                code VARCHAR,
                aui VARCHAR,
                cui VARCHAR,
                tty VARCHAR,
                name VARCHAR,
                suppress VARCHAR,
                is_active BOOLEAN,
                rank INTEGER
            )
            """
        )
        con.executemany(
            "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("ICD10CM", "E11.9", "ICD_PT", "C_DIAB", "PT", "Type 2 diabetes mellitus", "N", True, 1),
                ("ICD10CM", "S1", "ICD_SUP_ONLY", "C_SUP_ONLY", "PT", "Suppressed only", "Y", False, 1),
            ],
        )
        engine = LocalDuckDBEngine(con)
        infos = get_code_infos(
            [
                CodeRef("ICD10CM", "E11.9"),
                CodeRef("ICD10CM", "S1"),
            ],
            engine=engine,
        )
    finally:
        con.close()

    assert infos[0].name == "Type 2 diabetes mellitus"
    assert infos[1] is None


def test_discovery_uses_prepared_atoms_and_best_atoms():
    con = duckdb.connect(database=":memory:")
    try:
        con.execute("CREATE SCHEMA mt4ds")
        con.execute(
            """
            CREATE TABLE mt4ds.atoms (
                source VARCHAR,
                code VARCHAR,
                aui VARCHAR,
                cui VARCHAR,
                tty VARCHAR,
                name VARCHAR,
                suppress VARCHAR,
                is_active BOOLEAN
            )
            """
        )
        con.execute(
            """
            CREATE TABLE mt4ds.best_atoms (
                source VARCHAR,
                code VARCHAR,
                aui VARCHAR,
                cui VARCHAR,
                tty VARCHAR,
                name VARCHAR,
                suppress VARCHAR,
                is_active BOOLEAN,
                rank INTEGER
            )
            """
        )
        con.executemany(
            "INSERT INTO mt4ds.atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("ICD10CM", "E11.9", "ICD_PT", "C_DIAB", "PT", "Type 2 diabetes mellitus", "N", True),
                ("ICD10CM", "E11.9", "ICD_SY", "C_DIAB", "SY", "Diabetes synonym", "N", True),
                ("ICD10CM", "S1", "ICD_SUP", "C_SUP", "PT", "Suppressed only", "Y", False),
                ("CVX", "208", "CVX_208", "C_CVX", "PT", "COVID-19 vaccine", "N", True),
            ],
        )
        con.executemany(
            "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("ICD10CM", "E11.9", "ICD_PT", "C_DIAB", "PT", "Type 2 diabetes mellitus", "N", True, 1),
                ("ICD10CM", "S1", "ICD_SUP", "C_SUP", "PT", "Suppressed only", "Y", False, 1),
                ("CVX", "208", "CVX_208", "C_CVX", "PT", "COVID-19 vaccine", "N", True, 1),
            ],
        )
        engine = LocalDuckDBEngine(con)

        ttys = engine.get_code_ttys([CodeRef("ICD10CM", "E11.9"), CodeRef("ICD10CM", "S1")])
        stats = engine.get_source_stats(["ICD10CM", "CVX"])
        sample = engine.sample_source_codes(["ICD10CM", "CVX"], per_source=2)
    finally:
        con.close()

    assert [row.tty for row in ttys] == ["PT", "SY"]
    assert [(row.source, row.code_count, row.atom_count) for row in stats] == [
        ("CVX", 1, 1),
        ("ICD10CM", 1, 2),
    ]
    assert sample == [
        CodeRef("CVX", "208"),
        CodeRef("ICD10CM", "E11.9"),
    ]


def test_best_atom_order_cpt_prefers_pt_over_etcf():
    """Regression for QC-016 (DATA_INTEGRITY HIGH): CPT prepared-table priority.

    Pre-fix, ``_best_atom_order_sql()`` ranked CPT TTY as ETCF=0 > ETCLIN=1 >
    PT=2 > SY=3, returning the CMS clinical-equivalent atom as the canonical
    display for ~90% of CPT codes instead of the AMA-published PT. The fix
    reorders to PT=0 > ETCF=1 > ETCLIN=2 > SY=3, matching the legacy mrconso
    path's PT-first priority and the AMA's canonical display convention.
    """
    from medterm4ds.engines.duckdb.prepared import _best_atom_order_sql

    sql = _best_atom_order_sql()
    # Build a 1-row synthetic test: same code, multiple CPT TTYs; verify PT wins.
    con = duckdb.connect(database=":memory:")
    try:
        con.execute(
            """
            CREATE TABLE atoms (
                source VARCHAR, code VARCHAR, aui VARCHAR, cui VARCHAR,
                tty VARCHAR, name VARCHAR, suppress VARCHAR, is_active BOOLEAN
            )
            """
        )
        con.executemany(
            "INSERT INTO atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("CPT", "99213", "A_ETCF", "C_ETCF", "ETCF", "ETCF name", "N", True),
                ("CPT", "99213", "A_ETCLIN", "C_ETCLIN", "ETCLIN", "ETCLIN name", "N", True),
                ("CPT", "99213", "A_PT", "C_PT", "PT", "PT name (AMA canonical)", "N", True),
                ("CPT", "99213", "A_SY", "C_SY", "SY", "SY name", "N", True),
            ],
        )
        rows = con.execute(
            f"""
            SELECT tty, name, ROW_NUMBER() OVER (
                PARTITION BY source, code
                ORDER BY CASE WHEN suppress = 'N' THEN 0 ELSE 1 END, {sql}
            ) AS rank
            FROM atoms
            ORDER BY rank
            """
        ).fetchall()
    finally:
        con.close()

    # rank=1 row must be the PT atom — AMA canonical preferred term.
    assert rows[0][0] == "PT", f"Expected PT at rank=1, got tty={rows[0][0]!r}"
    assert "AMA canonical" in rows[0][1]
    # ETCF/ETCLIN/SY are secondary — exact order is ETCF < ETCLIN < SY per the fix.
    rank_by_tty = {row[0]: row[2] for row in rows}
    assert rank_by_tty["PT"] < rank_by_tty["ETCF"] < rank_by_tty["ETCLIN"] < rank_by_tty["SY"]


def test_cli_code_source_pairs_rejects_uri_form_source():
    """Regression for QC-011 (CROSS_SURFACE MEDIUM): CLI silently accepted URI sources.

    Pre-fix, ``--source http://snomed.info/sct`` was uppercased to
    'HTTP://SNOMED.INFO/SCT' and silently returned a null-valued row. The
    CLI uses UMLS SAB strings (e.g. SNOMEDCT_US), not FHIR URIs. The fix
    detects URI/OID-form inputs and rejects them early with a clear message.
    """
    import pytest
    from medterm4ds.apps.cli import _code_source_pairs

    # Valid SAB still works
    pairs = _code_source_pairs(["44054006"], ["SNOMEDCT_US"])
    assert pairs == [("SNOMEDCT_US", "44054006")]

    # URI form rejected
    with pytest.raises(SystemExit, match="UMLS SAB string"):
        _code_source_pairs(["44054006"], ["http://snomed.info/sct"])
    # OID form rejected
    with pytest.raises(SystemExit, match="UMLS SAB string"):
        _code_source_pairs(["44054006"], ["urn:oid:2.16.840.1.113883.6.96"])
