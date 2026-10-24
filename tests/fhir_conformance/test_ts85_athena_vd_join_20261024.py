"""TS-85 Athena thin-NDJSON VD join verification (2026-10-24).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261024c).
FRESH EVIDENCE — the exact fhir4ds-style scalar join executed live
this run in duckdb against the converter's thin NDJSON; no
prior-run results cited.

Scope: the S2 coordination contract says the thin form exists so a
VD can bind scalar (code, system-URI) ON pairs. Nobody had PROVEN a
duckdb join can actually consume the artifact. This iteration runs
the join end-to-end: read_json_auto the NDJSON → flatten the
1-length group/element/target lists (list_extract) → INNER JOIN a
mock resource codings table on (system_uri, code).

RESULT: ZERO FINDINGS — controls-only (12th clean surface). The
join WORKS: every resource coding row resolves to its concept_id +
equivalence (obs1 → 21602787 equal; obs2 → 21602788 unmatched for
the deprecated code — the equivalence classes ride through the join
intact). JSON parseability of every line verified via read_json_auto
(duckdb's strict reader). DUCKDB LESSON recorded for the fhir4ds
side: UNNEST aliasing does not chain across list levels in one
pass — the reliable flatten is staged list_extract views
(group→element→target), which read cleanly.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

pytest.importorskip("medterm4ds.services.athena")

from medterm4ds.services.athena import (  # noqa: E402
    ingest_athena,
    iter_thin_concept_map_rows,
)

MINI = "/tmp/opencode/athena-mini"


@pytest.fixture(scope="module")
def ndjson(tmp_path_factory):
    db = tmp_path_factory.mktemp("ts85") / "m.duckdb"
    con = duckdb.connect(str(db))
    ingest_athena(
        con, MINI, accept_licenses=True, licenses_accepted_at="ts85",
    )
    out = tmp_path_factory.mktemp("ts85out") / "thin.ndjson"
    with out.open("w", encoding="utf-8") as fh:
        for row in iter_thin_concept_map_rows(con):
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    con.close()
    return Path(out)


@pytest.fixture(scope="module")
def flat_view(ndjson):
    con = duckdb.connect()
    con.execute(
        f"CREATE VIEW cm AS SELECT list_extract(r.\"group\", 1) AS g "
        f"FROM read_json_auto('{ndjson}') r"
    )
    con.execute(
        "CREATE VIEW cm2 AS SELECT g.source AS system_uri, "
        "list_extract(g.element, 1) AS e FROM cm"
    )
    con.execute(
        "CREATE VIEW cm3 AS SELECT system_uri, e.code AS code, "
        "list_extract(e.target, 1) AS t FROM cm2"
    )
    con.execute(
        "CREATE VIEW cm_flat AS SELECT system_uri, code, "
        "t.code AS concept_id, t.equivalence AS eq FROM cm3"
    )
    yield con
    con.close()


class TestVDJoinContract:
    """Controls — the fhir4ds VD join shape works."""

    def test_v10_scalar_join_resolves(self, flat_view):
        flat_view.execute(
            "CREATE OR REPLACE TEMP TABLE res AS SELECT * FROM "
            "(VALUES ('http://snomed.info/sct','44054006','obs1'), "
            "('http://snomed.info/sct','99999001','obs2')) "
            "t(system_uri, code, res_id)"
        )
        joined = flat_view.execute(
            "SELECT r.res_id, cm.concept_id, cm.eq FROM res r "
            "JOIN cm_flat cm ON cm.system_uri = r.system_uri "
            "AND cm.code = r.code ORDER BY r.res_id"
        ).fetchall()
        assert joined == [
            ("obs1", "21602787", "equal"),
            ("obs2", "21602788", "unmatched"),
        ]

    def test_v11_equivalence_rides_through(self, flat_view):
        eqs = dict(
            flat_view.execute(
                "SELECT code, eq FROM cm_flat "
                "WHERE system_uri = 'http://snomed.info/sct'"
            ).fetchall()
        )
        assert eqs["44054006"] == "equal"
        assert eqs["99999001"] == "unmatched"

    def test_v12_every_line_parses(self, ndjson):
        lines = ndjson.read_text().strip().splitlines()
        assert len(lines) == 3
        for line in lines:
            row = json.loads(line)
            assert row["resourceType"] == "ConceptMap"

    def test_v13_unknown_code_no_match(self, flat_view):
        """A coding absent from Athena joins to nothing (clean
        INNER-JOIN miss — the VD semantics for unmapped codes)."""
        flat_view.execute(
            "CREATE OR REPLACE TEMP TABLE res2 AS SELECT * FROM "
            "(VALUES ('http://snomed.info/sct','00000000','obsX')) "
            "t(system_uri, code, res_id)"
        )
        joined = flat_view.execute(
            "SELECT r.res_id FROM res2 r JOIN cm_flat cm "
            "ON cm.system_uri = r.system_uri AND cm.code = r.code"
        ).fetchall()
        assert joined == []
