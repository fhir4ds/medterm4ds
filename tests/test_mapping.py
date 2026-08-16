from __future__ import annotations

import duckdb
import pytest

from medterm4ds import CodeRef, get_code_mappings
from medterm4ds.engines.duckdb import LocalDuckDBEngine


def _make_mapping_db(con: duckdb.DuckDBPyConnection) -> None:
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
            ("E11.9", "PT", "Suppressed source", "ICD_SUP", "Y", "ICD10CM", "C_SUP"),
            ("A1", "PT", "Specific source concept", "ICD_A1", "N", "ICD10CM", "C_CHILD"),
            ("A0", "PT", "Broader source concept", "ICD_A0", "N", "ICD10CM", "C_PARENT"),
            ("44054006", "SY", "Diabetes type 2 synonym", "SNOMED_SY", "N", "SNOMEDCT_US", "C_DIAB"),
            ("44054006", "PT", "Diabetes mellitus type 2", "SNOMED_PT", "N", "SNOMEDCT_US", "C_DIAB"),
            ("73211009", "PT", "Diabetes mellitus", "SNOMED_DM", "N", "SNOMEDCT_US", "C_DIAB"),
            ("999000", "PT", "Broader target concept", "SNOMED_A0", "N", "SNOMEDCT_US", "C_PARENT"),
            ("111111", "PT", "Target parent", "SNOMED_PARENT", "N", "SNOMEDCT_US", "C_PARENT_TARGET"),
            ("222222", "PT", "Target child", "SNOMED_CHILD", "N", "SNOMEDCT_US", "C_CHILD_TARGET"),
            ("999999", "PT", "Suppressed target", "SNOMED_SUP", "Y", "SNOMEDCT_US", "C_DIAB"),
            ("208", "PT", "COVID-19 vaccine", "CVX_208", "N", "CVX", "C_CVX"),
            ("840539006", "PT", "COVID-19 vaccine product", "SNOMED_CVX", "N", "SNOMEDCT_US", "C_CVX"),
            ("2345-7", "LN", "Glucose [Mass/volume] in Serum or Plasma", "LNC_GLU", "N", "LNC", "C_GLU"),
            (
                "0010U",
                "PT",
                "Infectious disease bacterial strain typing by sequencing",
                "CPT_0010U_PT",
                "N",
                "CPT",
                "C_CPT_0010U_PT",
            ),
            ("0010U", "ETCF", "Typing of bacterial strain", "CPT_0010U_ETCF", "N", "CPT", "C_BACTERIAL_TYPING"),
            ("76208001", "PT", "Bacterial strain typing", "SNOMED_BACTERIAL", "N", "SNOMEDCT_US", "C_BACTERIAL_TYPING"),
            ("S1", "PT", "Suppressed only", "ICD_SUP_ONLY", "Y", "ICD10CM", "C_ONLY_SUP"),
        ],
    )
    con.executemany(
        "INSERT INTO mrrel VALUES (?, ?, ?, ?)",
        [
            ("ICD_A1", "ICD_A0", "isa", "PAR"),
            ("SNOMED_PT", "SNOMED_PARENT", "isa", "PAR"),
            ("SNOMED_CHILD", "SNOMED_PT", "isa", "PAR"),
        ],
    )


def _add_snomed_depths(con: duckdb.DuckDBPyConnection, rows: list[tuple[str, int]]) -> None:
    con.execute("CREATE TABLE snomed_top_level_depth (code VARCHAR, min_top_depth INTEGER)")
    con.executemany("INSERT INTO snomed_top_level_depth VALUES (?, ?)", rows)


def test_get_code_mappings_returns_same_cui_active_targets_in_input_order():
    con = duckdb.connect(database=":memory:")
    try:
        _make_mapping_db(con)
        engine = LocalDuckDBEngine(con)

        rows = get_code_mappings(
            [
                CodeRef("CVX", "208"),
                ("ICD10-CM", "E11.9"),
                CodeRef("ICD10CM", "NOPE"),
                CodeRef("ICD10CM", "S1"),
            ],
            engine=engine,
            target_sources=["SNOMED"],
        )
    finally:
        con.close()

    assert [(row.source.source, row.source.code, row.target.code) for row in rows] == [
        ("CVX", "208", "840539006"),
        ("ICD10CM", "E11.9", "44054006"),
        ("ICD10CM", "E11.9", "73211009"),
    ]
    assert rows[1].to_dict() == {
        "source": "ICD10CM",
        "code": "E11.9",
        "source_display": "Type 2 diabetes mellitus",
        "target_source": "SNOMEDCT_US",
        "target_code": "44054006",
        "target_display": "Diabetes mellitus type 2",
        "relationship": "equivalent",
        "match_type": "same_cui",
        "match_depth": 0,
        "source_cui": "C_DIAB",
        "target_cui": "C_DIAB",
        "source_aui": "ICD_E119",
        "target_aui": "SNOMED_PT",
        "target_tty": "PT",
        "matched_via": {
            "strategy": "same_cui",
            "steps": [
                {
                    "op": "input_atom",
                    "source": "ICD10CM",
                    "code": "E11.9",
                    "cui": "C_DIAB",
                    "aui": "ICD_E119",
                    "name": "Type 2 diabetes mellitus",
                },
                {
                    "op": "same_cui",
                    "source": "ICD10CM",
                    "code": "E11.9",
                    "target_source": "SNOMEDCT_US",
                    "target_code": "44054006",
                    "cui": "C_DIAB",
                },
                {
                    "op": "target_atom",
                    "source": "SNOMEDCT_US",
                    "code": "44054006",
                    "cui": "C_DIAB",
                    "aui": "SNOMED_PT",
                    "tty": "PT",
                    "name": "Diabetes mellitus type 2",
                },
            ],
        },
    }


def test_get_code_mappings_caps_results_and_validates_args():
    con = duckdb.connect(database=":memory:")
    try:
        _make_mapping_db(con)
        engine = LocalDuckDBEngine(con)

        rows = get_code_mappings(
            [CodeRef("ICD10CM", "E11.9")],
            engine=engine,
            target_sources=["SNOMEDCT_US"],
            max_results_per_code=1,
        )
    finally:
        con.close()

    assert [(row.target.code, row.target_display) for row in rows] == [
        ("44054006", "Diabetes mellitus type 2")
    ]

    with pytest.raises(ValueError, match="target_sources"):
        get_code_mappings([CodeRef("ICD10CM", "E11.9")], engine=engine, target_sources=[])
    with pytest.raises(ValueError, match="max_results_per_code"):
        get_code_mappings(
            [CodeRef("ICD10CM", "E11.9")],
            engine=engine,
            target_sources=["SNOMEDCT_US"],
            max_results_per_code=0,
        )
    with pytest.raises(ValueError, match="max_depth"):
        get_code_mappings(
            [CodeRef("ICD10CM", "E11.9")],
            engine=engine,
            target_sources=["SNOMEDCT_US"],
            max_depth=-1,
        )


def test_get_code_mappings_considers_all_active_source_cuis():
    con = duckdb.connect(database=":memory:")
    try:
        _make_mapping_db(con)
        engine = LocalDuckDBEngine(con)

        rows = get_code_mappings(
            [CodeRef("CPT", "0010U")],
            engine=engine,
            target_sources=["SNOMEDCT_US"],
        )
    finally:
        con.close()

    assert [(row.target.code, row.source_display, row.source_cui) for row in rows] == [
        ("76208001", "Typing of bacterial strain", "C_BACTERIAL_TYPING")
    ]
    assert rows[0].matched_via.steps[0].aui == "CPT_0010U_ETCF"


def test_get_code_mappings_can_use_source_ancestor_fallback():
    con = duckdb.connect(database=":memory:")
    try:
        _make_mapping_db(con)
        engine = LocalDuckDBEngine(con)

        rows = get_code_mappings(
            [CodeRef("ICD10CM", "A1")],
            engine=engine,
            target_sources=["SNOMEDCT_US"],
            max_depth=1,
        )
    finally:
        con.close()

    assert [(row.target.code, row.relationship, row.match_type, row.match_depth) for row in rows] == [
        ("999000", "source-is-narrower-than-target", "source_ancestor_same_cui", 1)
    ]
    assert rows[0].matched_via.strategy == "source_ancestor_same_cui"
    assert [step.op for step in rows[0].matched_via.steps] == [
        "input_atom",
        "source_ancestor",
        "same_cui",
        "target_atom",
    ]


def test_get_code_mappings_filters_top_level_snomed_source_ancestor_fallback():
    con = duckdb.connect(database=":memory:")
    try:
        _make_mapping_db(con)
        _add_snomed_depths(con, [("999000", 3)])
        engine = LocalDuckDBEngine(con)

        rows = get_code_mappings(
            [CodeRef("ICD10CM", "A1")],
            engine=engine,
            target_sources=["SNOMEDCT_US"],
            max_depth=1,
        )
    finally:
        con.close()

    assert rows == []


def test_get_code_mappings_keeps_deeper_snomed_source_ancestor_fallback():
    con = duckdb.connect(database=":memory:")
    try:
        _make_mapping_db(con)
        _add_snomed_depths(con, [("999000", 4)])
        engine = LocalDuckDBEngine(con)

        rows = get_code_mappings(
            [CodeRef("ICD10CM", "A1")],
            engine=engine,
            target_sources=["SNOMEDCT_US"],
            max_depth=1,
        )
    finally:
        con.close()

    assert [(row.target.code, row.match_type) for row in rows] == [
        ("999000", "source_ancestor_same_cui")
    ]


def test_get_code_mappings_can_include_target_hierarchy():
    con = duckdb.connect(database=":memory:")
    try:
        _make_mapping_db(con)
        engine = LocalDuckDBEngine(con)

        rows = get_code_mappings(
            [CodeRef("ICD10CM", "E11.9")],
            engine=engine,
            target_sources=["SNOMEDCT_US"],
            max_depth=1,
            include_target_ancestors=True,
            include_target_descendants=True,
        )
    finally:
        con.close()

    selected = {
        (row.target.code, row.relationship, row.match_type, row.match_depth)
        for row in rows
    }
    assert ("44054006", "equivalent", "same_cui", 0) in selected
    assert ("111111", "source-is-narrower-than-target", "target_ancestor", 1) in selected
    assert ("222222", "source-is-broader-than-target", "target_descendant", 1) in selected


def test_get_code_mappings_filters_top_level_snomed_target_hierarchy():
    con = duckdb.connect(database=":memory:")
    try:
        _make_mapping_db(con)
        _add_snomed_depths(con, [("44054006", 4), ("111111", 3), ("222222", 3)])
        engine = LocalDuckDBEngine(con)

        rows = get_code_mappings(
            [CodeRef("ICD10CM", "E11.9")],
            engine=engine,
            target_sources=["SNOMEDCT_US"],
            max_depth=1,
            include_target_ancestors=True,
            include_target_descendants=True,
        )
    finally:
        con.close()

    selected = {
        (row.target.code, row.relationship, row.match_type, row.match_depth)
        for row in rows
    }
    assert ("44054006", "equivalent", "same_cui", 0) in selected
    assert ("111111", "source-is-narrower-than-target", "target_ancestor", 1) not in selected
    assert ("222222", "source-is-broader-than-target", "target_descendant", 1) not in selected


# ---------------------------------------------------------------------------
# Input-validation regression tests (QC-020, QC-021, QC-027)
# ---------------------------------------------------------------------------


def _minimal_engine() -> tuple[duckdb.DuckDBPyConnection, LocalDuckDBEngine]:
    con = duckdb.connect(database=":memory:")
    _make_mapping_db(con)
    return con, LocalDuckDBEngine(con)


def test_get_code_mappings_rejects_none_target_sources():
    """QC-020: target_sources=None must raise ValueError, not TypeError."""
    con, engine = _minimal_engine()
    try:
        with pytest.raises(ValueError, match="target_sources must not be empty"):
            get_code_mappings(
                [CodeRef("SNOMEDCT_US", "44054006")],
                engine=engine,
                target_sources=None,
            )
    finally:
        con.close()


def test_get_code_mappings_rejects_empty_string_target_source():
    """QC-021: target_sources=[''] must raise ValueError, not silently return []."""
    con, engine = _minimal_engine()
    try:
        with pytest.raises(ValueError, match="empty or None entries"):
            get_code_mappings(
                [CodeRef("SNOMEDCT_US", "44054006")],
                engine=engine,
                target_sources=[""],
            )
    finally:
        con.close()


def test_get_code_mappings_rejects_none_in_target_sources():
    """QC-021 sibling: target_sources=[None] must raise ValueError."""
    con, engine = _minimal_engine()
    try:
        with pytest.raises(ValueError, match="empty or None entries"):
            get_code_mappings(
                [CodeRef("SNOMEDCT_US", "44054006")],
                engine=engine,
                target_sources=[None],
            )
    finally:
        con.close()


def test_get_code_mappings_rejects_string_max_results_per_code():
    """QC-027: max_results_per_code='50' (string) must raise TypeError with clear message."""
    con, engine = _minimal_engine()
    try:
        with pytest.raises(TypeError, match="max_results_per_code must be int"):
            get_code_mappings(
                [CodeRef("SNOMEDCT_US", "44054006")],
                engine=engine,
                target_sources=["ICD10CM"],
                max_results_per_code="50",  # type: ignore[arg-type]
            )
    finally:
        con.close()


def test_get_code_mappings_rejects_string_max_depth():
    """QC-027 sibling: max_depth='1' (string) must raise TypeError with clear message."""
    con, engine = _minimal_engine()
    try:
        with pytest.raises(TypeError, match="max_depth must be int"):
            get_code_mappings(
                [CodeRef("SNOMEDCT_US", "44054006")],
                engine=engine,
                target_sources=["ICD10CM"],
                max_depth="1",  # type: ignore[arg-type]
            )
    finally:
        con.close()


# ---------------------------------------------------------------------------
# CLI run_mapping regression tests (QC-022, QC-023, QC-029)
# ---------------------------------------------------------------------------


def test_run_mapping_rejects_empty_target_source():
    """QC-023: --target-source '' must exit non-zero with a clean message."""
    import argparse
    from medterm4ds.apps.cli import run_mapping

    args = argparse.Namespace(
        db="data/umls_current.duckdb",
        memory_profile="low",
        memory_limit=None,
        temp_dir=None,
        threads=None,
        query_chunk_size=None,
        code=["44054006"],
        source=["SNOMEDCT_US"],
        target_source=[""],
        max_results_per_code=50,
        max_depth=0,
        include_target_ancestors=False,
        include_target_descendants=False,
        resolve_mode="active_only",
        output=None,
        format="json",
    )

    with pytest.raises(SystemExit, match="non-empty vocabulary name"):
        run_mapping(args)


def test_run_mapping_rejects_uri_form_target_source():
    """QC-032: --target-source http://... must exit non-zero (sibling of FIX-010).

    Pre-fix, ``--target-source http://hl7.org/fhir/sid/icd-10-cm`` was
    uppercased to 'HTTP://HL7.ORG/...' and silently returned no matches.
    The fix applies the same URI/OID rejection that --source uses (QC-011).
    """
    import argparse
    from medterm4ds.apps.cli import run_mapping

    args = argparse.Namespace(
        db="data/umls_current.duckdb",
        memory_profile="low",
        memory_limit=None,
        temp_dir=None,
        threads=None,
        query_chunk_size=None,
        code=["44054006"],
        source=["SNOMEDCT_US"],
        target_source=["http://hl7.org/fhir/sid/icd-10-cm"],
        max_results_per_code=50,
        max_depth=0,
        include_target_ancestors=False,
        include_target_descendants=False,
        resolve_mode="active_only",
        output=None,
        format="json",
    )

    with pytest.raises(SystemExit, match="UMLS SAB string"):
        run_mapping(args)



def test_run_mapping_rejects_negative_max_depth():
    """QC-022: --max-depth -5 must exit 1 with a clean message, not a traceback.

    This tests the CLI-layer catch of ValueError from the service layer. The
    service-layer validation itself is tested above (test_get_code_mappings_*
    tests); this test confirms the CLI renders it as a SystemExit.
    """
    import argparse
    import inspect
    from medterm4ds.apps.cli import run_mapping

    # Source-read audit: run_mapping must wrap get_code_mappings in
    # try/except (ValueError, TypeError) so input-validation errors surface
    # as clean SystemExit messages rather than raw Python tracebacks.
    source = inspect.getsource(run_mapping)
    assert "except (ValueError, TypeError)" in source, (
        "QC-022 regression: run_mapping must catch ValueError/TypeError from "
        "get_code_mappings and render as SystemExit('Error: ...')."
    )


def test_run_mapping_disables_progress_bar_for_stdout():
    """QC-029/QC-360: DuckDB progress bar must be disabled when writing JSON
    to stdout.

    DuckDB 1.5+ auto-enables a progress bar that writes directly to the
    terminal fd (bypassing sys.stdout), corrupting JSON output for downstream
    subprocess parsers. QC-360 generalized the QC-029 run_mapping-only guard
    into the shared ``_connect_read_only`` helper used by every stdout-
    printing query command; run_mapping must route its connection through it.
    """
    import inspect
    from medterm4ds.apps.cli import _connect_read_only, run_mapping

    source = inspect.getsource(run_mapping)
    assert "_connect_read_only(db_path, output=args.output)" in source, (
        "QC-360 regression: run_mapping must open its read-only connection "
        "via _connect_read_only so the DuckDB progress bar is disabled on "
        "the stdout path (corrupting JSON otherwise)."
    )

    helper_source = inspect.getsource(_connect_read_only)
    assert "enable_progress_bar" in helper_source, (
        "QC-029/QC-360 regression: _connect_read_only must SET "
        "enable_progress_bar = false when output is None."
    )
    # The disable must be conditional on stdout output (not --output file path).
    assert "if not output" in helper_source, (
        "QC-029/QC-360 regression: progress-bar disable must be gated on "
        "output being None (stdout path only)."
    )


