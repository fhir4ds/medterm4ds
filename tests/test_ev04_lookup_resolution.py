"""EV-04 (lookup-resolution) QA regression pins, v0.0.13 run 1 iterations 10-12.

Bug IDs follow the loop docket: EV04-HISTORIAN-1 (patient-friendly
resolve_mode forwarding) and EV04-EXPLORER-1 (prepared preferred-atom
lookup is_active gate).
"""
from __future__ import annotations

import duckdb

from medterm4ds import CodeInfo, CodeRef, CodeResolution, FriendlyNameResult
from medterm4ds.services.lookup import get_code_infos_prepared
from medterm4ds.services.patient_friendly import get_patient_friendly_names
from medterm4ds.services.prepared_primitives import preferred_atom_lookup


class _HistoricalBrandEngine:
    """Synthetic double mirroring the real DuckDB engine's split behavior.

    Active-only lookups cannot see the obsolete brand atom (every atom is
    SUPPRESS='O'), but the resolution subsystem CAN see it (raw-mrconso
    historical lookup) and the patient-friendly machinery CAN name it via
    the has_tradename/ingredient edges — the real-data shapes for
    RxNorm 128793 (Vicodin) and 284743 (Glucovance).
    """

    def get_code_infos(self, codes):
        # Active-only view: the obsolete brand is invisible.
        return [None for _ in codes]

    def resolve_codes(self, codes):
        out = []
        for c in codes:
            if c.code == "128793":
                out.append(
                    CodeResolution(
                        input=c,
                        resolved=c,
                        status="historical",
                        match_type="historical_exact",
                        input_display="Vicodin",
                        resolved_display="Vicodin",
                        input_suppress="O",
                        resolved_suppress="O",
                    )
                )
            else:
                out.append(
                    CodeResolution(
                        input=c,
                        resolved=None,
                        status="not_found",
                        match_type="not_found",
                    )
                )
        return out

    def get_patient_friendly_names(self, codes, max_depth=5):
        return [
            FriendlyNameResult(
                code=c,
                name="Acetaminophen / Hydrocodone",
                friendly_source="RXNORM",
                match_type="ingredient",
                match_depth=1,
            )
            for c in codes
        ]


class TestEv04HistorianPatientFriendlyResolveMode:
    """EV04-HISTORIAN-1 (HIGH): the known-atom pre-check in
    ``services/patient_friendly.py`` computed ``known_mask`` with the
    DEFAULT ``resolve_mode='active_only'`` instead of the caller's mode,
    so historical/resolve_current calls on obsolete codes were silently
    replaced with ``name='' match_type='none'`` miss rows even though the
    engine resolves them (real-DB repro: Vicodin 128793 — engine answers
    'Acetaminophen / Hydrocodone' via ingredient edges; service answered
    ''/'none'). The deferred QC-017 sibling from EC-01, confirmed.
    """

    def test_historical_obsolete_code_consults_engine(self):
        rows = get_patient_friendly_names(
            [CodeRef("RXNORM", "128793")],
            engine=_HistoricalBrandEngine(),
            resolve_mode="historical",
        )
        assert rows[0].match_type == "ingredient"
        assert rows[0].name  # non-empty engine answer, not the '' miss row

    def test_resolve_current_obsolete_code_consults_engine(self):
        rows = get_patient_friendly_names(
            [CodeRef("RXNORM", "128793")],
            engine=_HistoricalBrandEngine(),
            resolve_mode="resolve_current",
        )
        assert rows[0].match_type == "ingredient"
        assert rows[0].name

    def test_active_only_still_returns_miss_row(self):
        # Mode distinction preserved: active_only must NOT resolve the
        # obsolete brand (it is not an active code).
        rows = get_patient_friendly_names(
            [CodeRef("RXNORM", "128793")],
            engine=_HistoricalBrandEngine(),
            resolve_mode="active_only",
        )
        assert rows[0].match_type == "none"
        assert rows[0].name == ""

    def test_batch_mixed_modes_order_preserved(self):
        rows = get_patient_friendly_names(
            [CodeRef("RXNORM", "128793"), CodeRef("RXNORM", "99999")],
            engine=_HistoricalBrandEngine(),
            resolve_mode="historical",
        )
        assert rows[0].match_type == "ingredient"
        assert rows[1].match_type == "none"  # unknown sibling stays a miss


def _make_prepared_con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("CREATE SCHEMA mt4ds")
    con.execute(
        """
        CREATE TABLE mt4ds.best_atoms (
            source VARCHAR, code VARCHAR, aui VARCHAR, cui VARCHAR,
            tty VARCHAR, name VARCHAR, suppress VARCHAR,
            is_active BOOLEAN, rank INTEGER
        )
        """
    )
    con.executemany(
        "INSERT INTO mt4ds.best_atoms VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ICD10CM", "E11.9", "ICD_PT", "C_DIAB", "PT", "Type 2 diabetes mellitus", "N", True, 1),
            # ALL atoms suppressed: rank-1 row is inactive (per the
            # _prepare_best_atoms builder — active atoms rank first, so a
            # rank-1 inactive row means the code has no active atom).
            ("ICD10CM", "OLD1", "OLD_AUI", "C_OLD", "PT", "Old concept name", "O", False, 1),
        ],
    )
    return con


class TestEv04ExplorerPreferredAtomIsActiveGate:
    """EV04-EXPLORER-1 (MEDIUM): ``preferred_atom_lookup`` selected
    ``rank = 1`` from ``mt4ds.best_atoms`` WITHOUT the ``is_active`` gate
    the engine's prepared lookup path applies
    (``engines/duckdb/_mixins/_LookupOps.py`` → ``AND is_active = true``).
    A code whose every atom is suppressed carries a rank-1 INACTIVE row,
    so the exported ``get_code_infos_prepared`` surface returned the
    suppressed atom as the canonical lookup answer — the engine answered
    None for the same question (two answers, one engine).
    """

    def test_prepared_lookup_excludes_inactive_rank1_atom(self):
        con = _make_prepared_con()
        try:
            infos = get_code_infos_prepared(
                [CodeRef("ICD10CM", "E11.9"), CodeRef("ICD10CM", "OLD1")], con
            )
        finally:
            con.close()
        assert infos[0] is not None and infos[0].name == "Type 2 diabetes mellitus"
        assert infos[1] is None  # inactive rank-1 row must not leak

    def test_preferred_atom_lookup_primitive_gates_is_active(self):
        con = _make_prepared_con()
        try:
            hits = preferred_atom_lookup(con, "ICD10CM", ["E11.9", "OLD1"])
        finally:
            con.close()
        assert set(hits) == {"E11.9"}
        assert all(
            isinstance(info, CodeInfo) and info.suppress == "N" for info in hits.values()
        )

    def test_source_display_lookup_does_not_surface_suppressed_display(self):
        from medterm4ds.services.prepared_primitives import source_display_lookup

        con = _make_prepared_con()
        try:
            displays = source_display_lookup(con, "ICD10CM", ["E11.9", "OLD1"])
        finally:
            con.close()
        assert displays["E11.9"][0] == "Type 2 diabetes mellitus"
        assert "OLD1" not in displays  # no retired-atom display on crosswalks
