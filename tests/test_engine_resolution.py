"""Direct unit tests for engines/duckdb/resolution.py.

Tests code resolution: active codes, nonexistent codes, and NDC normalization.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from medterm4ds.core.models import CodeRef
from medterm4ds.engines.duckdb import LocalDuckDBEngine


def _make_resolution_db(path: Path) -> None:
    con = duckdb.connect(str(path))
    con.execute("""CREATE TABLE mrconso (
        CODE VARCHAR, TTY VARCHAR, STR VARCHAR, AUI VARCHAR,
        SUPPRESS VARCHAR, SAB VARCHAR, CUI VARCHAR
    )""")
    con.executemany(
        "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("E11", "PT", "Type 2 diabetes", "AUI_E11", "N", "ICD10CM", "C0011860"),
            ("OLD_CODE", "PT", "Deprecated term", "AUI_OLD", "O", "ICD10CM", "C_OLD"),
            # QC-406 fixture: the active replacement for OLD_CODE.
            ("NEW_CODE", "PT", "Replacement term", "AUI_NEW", "N", "ICD10CM", "C_NEW"),
            # QC-401 fixture: the RXNORM drug the NDC attribute points at.
            ("860975", "SCD", "24 HR metformin 500 MG Oral Tablet", "AUI_860975", "N", "RXNORM", "C0978484"),
        ],
    )
    con.execute("""CREATE TABLE mrrel (
        AUI1 VARCHAR, AUI2 VARCHAR, RELA VARCHAR, REL VARCHAR
    )""")
    con.executemany(
        "INSERT INTO mrrel VALUES (?, ?, ?, ?)",
        [
            # QC-406 fixture: obsolete -> active replacement edge.
            ("AUI_OLD", "AUI_NEW", "same_as", "RO"),
        ],
    )
    con.execute("""CREATE TABLE mrsat (
        CODE VARCHAR, SAB VARCHAR, ATN VARCHAR, ATV VARCHAR
    )""")
    con.executemany(
        "INSERT INTO mrsat VALUES (?, ?, ?, ?)",
        [
            ("860975", "RXNORM", "NDC", "12345678901"),
        ],
    )
    con.close()


@pytest.fixture
def engine(tmp_path):
    db = tmp_path / "resolution.duckdb"
    _make_resolution_db(db)
    con = duckdb.connect(str(db))
    return LocalDuckDBEngine(con)


class TestCodeResolution:
    def test_resolve_active_code(self, engine):
        """Active code resolves to itself."""
        results = engine.resolve_codes([CodeRef("ICD10CM", "E11")])
        assert len(results) == 1
        assert results[0].status == "active"
        assert results[0].resolved.code == "E11"

    def test_resolve_nonexistent_code(self, engine):
        """Nonexistent code returns not-found status."""
        results = engine.resolve_codes([CodeRef("ICD10CM", "FAKE999")])
        assert len(results) == 1
        assert results[0].status == "not_found"

    def test_empty_input(self, engine):
        results = engine.resolve_codes([])
        assert results == []

    def test_resolve_obsolete_code_finds_replacement(self, engine):
        """QC-398 baseline: the live-MRREL fallback resolves OLD_CODE to
        NEW_CODE when no prepared code_replacements table exists."""
        results = engine.resolve_codes([CodeRef("ICD10CM", "OLD_CODE")])
        assert results[0].status == "replaced"
        assert results[0].resolved.code == "NEW_CODE"
        assert results[0].resolved_display == "Replacement term"

    def test_prepare_cache_preserves_replacement_resolution_qc406(self, tmp_path):
        """QC-406 (HIGH): prepare_cache's active-only TEMP mrconso/mrrel shadow
        must not change obsolete-code resolution. The shadow hides the
        SUPPRESS='O' atoms (so code_auis found none) and drops the
        obsolete-to-active mrrel edges — pre-fix, the prepared engine
        degraded status='replaced' to 'historical' with resolved=None."""
        db = tmp_path / "resolution_qc406.duckdb"
        _make_resolution_db(db)
        con = duckdb.connect(str(db))
        try:
            prepared = LocalDuckDBEngine(con)
            prepared.prepare_cache(create_indexes=False)
            results = prepared.resolve_codes([CodeRef("ICD10CM", "OLD_CODE")])
        finally:
            con.close()
        assert results[0].status == "replaced", results[0].status
        assert results[0].resolved.code == "NEW_CODE"
        assert results[0].resolved_display == "Replacement term"

    def test_prepared_single_successor_not_ambiguous_multi_atom_target(self, tmp_path):
        """Post-rebuild regression: when the prepared code_replacements table
        has ONE replacement row but the target code has MULTIPLE active atoms
        in best_atoms, the candidate join must not fan out to N identical
        candidates (misclassifying 'replaced' as 'ambiguous'). One distinct
        target -> one candidate, best atom by rank."""
        db = tmp_path / "resolution_fanout.duckdb"
        _make_resolution_db(db)
        con = duckdb.connect(str(db))
        try:
            con.execute("CREATE SCHEMA mt4ds")
            con.execute("""CREATE TABLE mt4ds.prepare_manifest (
                key VARCHAR, value VARCHAR
            )""")
            con.executemany(
                "INSERT INTO mt4ds.prepare_manifest VALUES (?, ?)",
                [("prepared_schema_version", "0.9")],
            )
            con.execute("""CREATE TABLE mt4ds.code_replacements (
                source VARCHAR, old_code VARCHAR, new_code VARCHAR, rela VARCHAR
            )""")
            con.execute(
                "INSERT INTO mt4ds.code_replacements VALUES "
                "('ICD10CM', 'OLD_CODE', 'NEW_CODE', 'same_as')"
            )
            con.execute("""CREATE TABLE mt4ds.best_atoms (
                source VARCHAR, code VARCHAR, aui VARCHAR, cui VARCHAR,
                tty VARCHAR, name VARCHAR, suppress VARCHAR,
                is_active BOOLEAN, rank INTEGER
            )""")
            # Target with 4 active atoms: rank-1 PT plus FN/SY siblings.
            con.executemany(
                "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("ICD10CM", "NEW_CODE", "AUI_NEW", "C_NEW", "PT",
                     "Replacement term", "N", True, 1),
                    ("ICD10CM", "NEW_CODE", "AUI_NEW_FN", "C_NEW", "FN",
                     "Replacement term (finding)", "N", True, 2),
                    ("ICD10CM", "NEW_CODE", "AUI_NEW_SY1", "C_NEW", "SY",
                     "Replacement synonym", "N", True, 3),
                    ("ICD10CM", "NEW_CODE", "AUI_NEW_SY2", "C_NEW", "SY",
                     "Replacement synonym two", "N", True, 4),
                ],
            )
            engine = LocalDuckDBEngine(con)
            results = engine.resolve_codes([CodeRef("ICD10CM", "OLD_CODE")])
        finally:
            con.close()
        assert results[0].status == "replaced", results[0].status
        assert results[0].resolved.code == "NEW_CODE"
        assert results[0].candidates == (CodeRef("ICD10CM", "NEW_CODE"),)

    def test_ndc_lookup_default_mode_returns_resolved_drug_qc401(self, tmp_path):
        """QC-401 (HIGH): active_only lookup of an NDC code must return the
        resolved RXNORM drug record. The historical fallthrough previously
        echoed the raw NDC string as the display while cui/aui/tty/suppress
        came from the resolved atom."""
        from medterm4ds.services.lookup import get_code_info

        db = tmp_path / "resolution_qc401.duckdb"
        _make_resolution_db(db)
        con = duckdb.connect(str(db))
        try:
            engine = LocalDuckDBEngine(con)
            info = get_code_info(
                ("NDC", "12345678901"), engine=engine, resolve_mode="active_only"
            )
        finally:
            con.close()
        assert info is not None
        assert (info.code.source, info.code.code) == ("RXNORM", "860975")
        assert info.name == "24 HR metformin 500 MG Oral Tablet"
        assert info.name != "12345678901"


class TestActiveOnlyBatchIndependenceEV04QA02:
    """EV04-QA-02 (HIGH): active_only answers must not depend on batch
    composition.

    Pre-fix, the NDC exception on the effective_code_refs fast path was
    batch-GLOBAL, so ONE NDC code anywhere in the batch made resolutions
    available for EVERY element; a same-source obsolete code then fell into
    lookup.py's historical branch and surfaced the SUPPRESSED atom
    (name='Deprecated term', suppress='O') — data active_only must not
    return, and a batch-vs-single parity break (single calls and NDC-free
    batches correctly returned None).
    """

    def test_obsolete_is_none_single(self, engine):
        from medterm4ds.services.lookup import get_code_infos

        infos = get_code_infos(
            [("ICD10CM", "OLD_CODE")], engine=engine, resolve_mode="active_only"
        )
        assert infos == [None]

    def test_obsolete_is_none_in_ndc_free_batch(self, engine):
        from medterm4ds.services.lookup import get_code_infos

        infos = get_code_infos(
            [("ICD10CM", "OLD_CODE"), ("ICD10CM", "E11")],
            engine=engine,
            resolve_mode="active_only",
        )
        assert infos[0] is None
        assert infos[1] is not None and infos[1].name == "Type 2 diabetes"

    def test_obsolete_is_none_in_batch_with_ndc(self, engine):
        """The core EV04-QA-02 pin: the NDC sibling must NOT flip the
        obsolete code's active_only answer from None to the suppressed
        atom. Pre-fix this returned CodeInfo(name='Deprecated term',
        suppress='O')."""
        from medterm4ds.services.lookup import get_code_infos

        infos = get_code_infos(
            [("NDC", "12345678901"), ("ICD10CM", "OLD_CODE")],
            engine=engine,
            resolve_mode="active_only",
        )
        assert infos[1] is None, f"batch-composition leak: {infos[1]!r}"
        # QC-401 must keep working in the same batch: the NDC entry still
        # resolves cross-source to the RXNORM drug display.
        assert infos[0] is not None
        assert (infos[0].code.source, infos[0].code.code) == ("RXNORM", "860975")
        assert infos[0].name == "24 HR metformin 500 MG Oral Tablet"

    def test_modes_stay_distinct_for_obsolete_code(self, engine):
        """QC-017 guard: the EV04-QA-02 fix must not flatten the three
        resolve_modes — historical returns the historical atom, resolve_current
        the active replacement, active_only None."""
        from medterm4ds.services.lookup import get_code_infos

        historical = get_code_infos(
            [("ICD10CM", "OLD_CODE")], engine=engine, resolve_mode="historical"
        )[0]
        resolve_current = get_code_infos(
            [("ICD10CM", "OLD_CODE")], engine=engine, resolve_mode="resolve_current"
        )[0]
        active_only = get_code_infos(
            [("ICD10CM", "OLD_CODE")], engine=engine, resolve_mode="active_only"
        )[0]

        assert historical is not None and historical.name == "Deprecated term"
        assert historical.suppress == "O"
        assert resolve_current is not None and resolve_current.name == "Replacement term"
        assert resolve_current.code.code == "NEW_CODE"
        assert active_only is None
