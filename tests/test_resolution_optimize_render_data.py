from __future__ import annotations

import gzip
import zipfile
from pathlib import Path

import duckdb
import pytest

from medterm4ds import CodeRef, optimize_codes, resolve_codes
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.engines.duckdb.prepared import prepare_mt4ds_schema
from medterm4ds.outputs import render_table, render_tree
from medterm4ds.services.data_setup import build_duckdb_from_rrf, verify_duckdb
from medterm4ds.services.lookup import get_code_info
from medterm4ds.services.patient_friendly import get_patient_friendly_name


def _make_db(path: Path) -> None:
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
        con.execute(
            """
            CREATE TABLE mrsat (
                CODE VARCHAR,
                SAB VARCHAR,
                ATN VARCHAR,
                ATV VARCHAR
            )
            """
        )
        rows = [
            ("OLD", "PT", "Old diabetes code", "A_OLD", "O", "ICD10CM", "C_OLD"),
            ("NEW", "PT", "New diabetes code", "A_NEW", "N", "ICD10CM", "C_NEW"),
            ("E11", "PT", "Type 2 diabetes mellitus", "A_E11", "N", "ICD10CM", "C_E11"),
            ("E11.40", "PT", "Type 2 diabetes with neuropathy", "A_E1140", "N", "ICD10CM", "C_E1140"),
            ("E11.41", "PT", "Type 2 diabetes with mononeuropathy", "A_E1141", "N", "ICD10CM", "C_E1141"),
            ("E11.42", "PT", "Type 2 diabetes with polyneuropathy", "A_E1142", "N", "ICD10CM", "C_E1142"),
            ("E11.43", "PT", "Type 2 diabetes with autonomic neuropathy", "A_E1143", "N", "ICD10CM", "C_E1143"),
            ("E11.44", "PT", "Type 2 diabetes with amyotrophy", "A_E1144", "N", "ICD10CM", "C_E1144"),
            ("E11.49", "PT", "Type 2 diabetes with other neuropathy", "A_E1149", "N", "ICD10CM", "C_E1149"),
            ("12345", "SCD", "Insulin 100 UNT/ML Injection", "A_RX", "N", "RXNORM", "C_RX"),
        ]
        con.executemany("INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        con.executemany(
            "INSERT INTO mrrel VALUES (?, ?, ?, ?)",
            [
                ("A_NEW", "A_OLD", "replaced_by", "RO"),
                ("A_E1140", "A_E11", "isa", "PAR"),
                ("A_E1141", "A_E11", "isa", "PAR"),
                ("A_E1142", "A_E11", "isa", "PAR"),
                ("A_E1143", "A_E11", "isa", "PAR"),
                ("A_E1144", "A_E11", "isa", "PAR"),
                ("A_E1149", "A_E11", "isa", "PAR"),
            ],
        )
        con.execute("INSERT INTO mrsat VALUES ('12345', 'RXNORM', 'NDC', '00002082101')")
    finally:
        con.close()


def test_resolve_codes_handles_obsolete_and_ndc(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_db(db_path)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        engine = LocalDuckDBEngine(con)
        obsolete, ndc = resolve_codes(
            [CodeRef("ICD10CM", "OLD"), CodeRef("NDC", "0002-0821-01")],
            engine=engine,
        )
        info = get_code_info(CodeRef("NDC", "0002-0821-01"), engine=engine, resolve_mode="resolve_current")
        friendly = get_patient_friendly_name(CodeRef("NDC", "0002-0821-01"), engine=engine, resolve_mode="resolve_current")
    finally:
        con.close()

    assert obsolete.status == "replaced"
    assert obsolete.resolved == CodeRef("ICD10CM", "NEW")
    assert ndc.status == "ndc_resolved"
    assert ndc.resolved == CodeRef("RXNORM", "12345")
    assert info.name == "Insulin 100 UNT/ML Injection"
    assert friendly.code == CodeRef("RXNORM", "12345")


def test_resolve_codes_uses_prepared_code_replacements_without_raw_mrrel(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_db(db_path)
    con = duckdb.connect(str(db_path))
    try:
        prepare_mt4ds_schema(con)
        con.execute("DROP TABLE mrrel")
        con.execute("DROP TABLE mrconso")
        engine = LocalDuckDBEngine(con)
        (obsolete,) = resolve_codes(
            [CodeRef("ICD10CM", "OLD")],
            engine=engine,
        )
    finally:
        con.close()

    assert obsolete.status == "replaced"
    assert obsolete.resolved == CodeRef("ICD10CM", "NEW")
    assert obsolete.replacement_relationship == "replaced_by"


def test_get_code_info_resolve_modes_distinguish_historical_and_resolved(tmp_path):
    """Regression for QC-017 (DATA_INTEGRITY HIGH): resolve_mode was no-op.

    Pre-fix, ``get_code_infos`` called ``effective_code_refs`` which returned
    ``(normalized, resolutions)`` for historical mode, then queried the active
    table with ``normalized`` (the unchanged suppressed code) and discarded
    ``resolutions``. All three modes returned None for suppressed-only codes.
    The fix builds CodeInfo from the resolution record: historical returns the
    historical atom's display; resolve_current returns the active replacement.
    """
    db_path = tmp_path / "umls.duckdb"
    _make_db(db_path)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        engine = LocalDuckDBEngine(con)
        # 'OLD' is suppressed=O in mrconso; 'NEW' (replaced_by) is active.
        info_active = get_code_info(CodeRef("ICD10CM", "OLD"), engine=engine, resolve_mode="active_only")
        info_hist = get_code_info(CodeRef("ICD10CM", "OLD"), engine=engine, resolve_mode="historical")
        info_resolved = get_code_info(CodeRef("ICD10CM", "OLD"), engine=engine, resolve_mode="resolve_current")
    finally:
        con.close()

    # active_only: OLD is suppressed-only -> no active atom -> None.
    assert info_active is None
    # historical: returns the historical atom's display ("Old diabetes code").
    assert info_hist is not None
    assert info_hist.name == "Old diabetes code"
    assert info_hist.cui == "C_OLD"
    # resolve_current: returns the active replacement's display ("New diabetes code").
    assert info_resolved is not None
    assert info_resolved.name == "New diabetes code"
    assert info_resolved.cui == "C_NEW"


def test_effective_code_refs_rejects_invalid_resolve_mode(tmp_path):
    """Regression for QC-002 (EDGE_CASE MEDIUM): Python API didn't validate resolve_mode.

    Pre-fix, typos like ``'historica'`` and empty string silently fell through
    to resolve_current behavior. The CLI validated via argparse choices=, but
    the engine layer didn't. The fix adds validation at the single chokepoint
    (``effective_code_refs``) shared by all services + CLI imports.
    """
    from medterm4ds.services.resolution import effective_code_refs

    db_path = tmp_path / "umls.duckdb"
    _make_db(db_path)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        engine = LocalDuckDBEngine(con)
        for bad in ("bogus", "", "historica", "INVALID_MODE", None):
            with pytest.raises(ValueError, match="resolve_mode must be one of"):
                effective_code_refs(
                    [CodeRef("ICD10CM", "E11")],
                    engine=engine,
                    resolve_mode=bad,
                )
    finally:
        con.close()


def test_optimize_codes_compacts_hierarchy(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_db(db_path)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        engine = LocalDuckDBEngine(con)
        result = optimize_codes(
            [
                CodeRef("ICD10CM", "E11.40"),
                CodeRef("ICD10CM", "E11.41"),
                CodeRef("ICD10CM", "E11.42"),
                CodeRef("ICD10CM", "E11.43"),
                CodeRef("ICD10CM", "E11.44"),
            ],
            engine=engine,
            include_codes=True,
        )
    finally:
        con.close()

    assert result.original_count == 5
    assert result.rules[0].include == CodeRef("ICD10CM", "E11")
    assert result.rules[0].exclude == (CodeRef("ICD10CM", "E11.49"),)


def test_optimize_codes_uses_prepared_walk_edges_for_icd10cm() -> None:
    con = duckdb.connect(":memory:")
    try:
        con.execute("CREATE SCHEMA mt4ds")
        con.execute(
            """
            CREATE TABLE mt4ds.walk_edges (
                source VARCHAR,
                from_code VARCHAR,
                from_aui VARCHAR,
                from_cui VARCHAR,
                from_tty VARCHAR,
                to_code VARCHAR,
                to_aui VARCHAR,
                to_cui VARCHAR,
                to_tty VARCHAR,
                relationship VARCHAR,
                direction VARCHAR,
                edge_source VARCHAR
            )
            """
        )
        con.executemany(
            "INSERT INTO mt4ds.walk_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("ICD10CM", "E11.40", "A_E1140", "C_E1140", "PT", "E11", "A_E11", "C_E11", "PT", "isa", "parent", "umls_mrrel"),
                ("ICD10CM", "E11.41", "A_E1141", "C_E1141", "PT", "E11", "A_E11", "C_E11", "PT", "isa", "parent", "umls_mrrel"),
                ("ICD10CM", "E11.42", "A_E1142", "C_E1142", "PT", "E11", "A_E11", "C_E11", "PT", "isa", "parent", "umls_mrrel"),
                ("ICD10CM", "E11.43", "A_E1143", "C_E1143", "PT", "E11", "A_E11", "C_E11", "PT", "isa", "parent", "umls_mrrel"),
                ("ICD10CM", "E11.44", "A_E1144", "C_E1144", "PT", "E11", "A_E11", "C_E11", "PT", "isa", "parent", "umls_mrrel"),
                ("ICD10CM", "E11.49", "A_E1149", "C_E1149", "PT", "E11", "A_E11", "C_E11", "PT", "isa", "parent", "umls_mrrel"),
            ],
        )
        engine = LocalDuckDBEngine(con)
        result = optimize_codes(
            [
                CodeRef("ICD10CM", "E11.40"),
                CodeRef("ICD10CM", "E11.41"),
                CodeRef("ICD10CM", "E11.42"),
                CodeRef("ICD10CM", "E11.43"),
                CodeRef("ICD10CM", "E11.44"),
            ],
            engine=engine,
            include_codes=True,
        )
    finally:
        con.close()

    assert result.rules[0].include == CodeRef("ICD10CM", "E11")
    assert result.rules[0].exclude == (CodeRef("ICD10CM", "E11.49"),)


def test_optimize_codes_rejects_explicit_prefix_mode(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_db(db_path)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        engine = LocalDuckDBEngine(con)
        with pytest.raises(ValueError, match="prefix optimize is not supported"):
            optimize_codes(
                [
                    CodeRef("ICD10CM", "E11.40"),
                    CodeRef("ICD10CM", "E11.41"),
                    CodeRef("ICD10CM", "E11.42"),
                    CodeRef("ICD10CM", "E11.43"),
                    CodeRef("ICD10CM", "E11.44"),
                ],
                engine=engine,
                relationship="prefix",
                include_codes=True,
            )
    finally:
        con.close()


def test_renderers_are_compact():
    table = render_table([{"source": "ICD10CM", "code": "E11.9", "name": "Type 2 diabetes mellitus"}])
    tree = render_tree({"results": [{"source": "ICD10CM", "code": "E11.9"}]})

    assert "source" in table
    assert "ICD10CM" in table
    assert "results:" in tree


def test_build_duckdb_from_rrf_and_verify(tmp_path):
    rrf_dir = tmp_path / "rrf"
    rrf_dir.mkdir()
    (rrf_dir / "MRCONSO.RRF").write_text(
        "C1|ENG|P|L1|PF|S1|Y|A1||||ICD10CM|PT|E11.9|Type 2 diabetes mellitus|0|N|\n",
        encoding="utf-8",
    )
    (rrf_dir / "MRREL.RRF").write_text(
        "C1|A1|AUI|PAR|C2|A2|AUI|isa|R1||ICD10CM|ICD10CM|||N|\n",
        encoding="utf-8",
    )
    (rrf_dir / "MRSAT.RRF").write_text(
        "C3|||A3|CODE|12345|||NDC|RXNORM|00002082101|N|\n",
        encoding="utf-8",
    )

    db_path = build_duckdb_from_rrf(rrf_dir=rrf_dir, output_db=tmp_path / "built.duckdb")
    report = verify_duckdb(db_path, sources=["ICD10CM"])

    assert report["has_required_tables"] is True
    assert report["source_counts"] == {"ICD10CM": 1}


def test_build_duckdb_from_extracted_umls_nlm_archives(tmp_path):
    release_dir = tmp_path / "2026AA-full"
    release_dir.mkdir()
    with zipfile.ZipFile(release_dir / "2026aa-1-meta.nlm", "w") as archive:
        archive.writestr(
            "2026AA/META/MRCONSO.RRF.aa.gz",
            gzip.compress(
                b"C1|ENG|P|L1|PF|S1|Y|A1||||ICD10CM|PT|E11.9|Type 2 diabetes mellitus|0|N|\n"
            ),
        )
    with zipfile.ZipFile(release_dir / "2026aa-2-meta.nlm", "w") as archive:
        archive.writestr(
            "2026AA/META/MRREL.RRF.aa.gz",
            gzip.compress(b"C1|A1|AUI|PAR|C2|A2|AUI|isa|R1||ICD10CM|ICD10CM|||N|\n"),
        )
        archive.writestr(
            "2026AA/META/MRSAT.RRF.aa.gz",
            gzip.compress(b"C3|||A3|CODE|12345|||NDC|RXNORM|00002082101|N|\n"),
        )

    db_path = build_duckdb_from_rrf(rrf_dir=release_dir, output_db=tmp_path / "built-nlm.duckdb")
    report = verify_duckdb(db_path, sources=["ICD10CM"])

    assert report["has_required_tables"] is True
    assert report["source_counts"] == {"ICD10CM": 1}
