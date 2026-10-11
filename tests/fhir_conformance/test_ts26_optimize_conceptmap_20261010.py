"""TS-26 optimize + conceptmap-export contracts (2026-10-10).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261010e).
FRESH EVIDENCE — live probes executed this run on the REST facade and
the CLI; no prior-run results cited.

Scope: the last unswept REST endpoints — /optimize (value-set
compaction) and /conceptmap/patient-friendly (the ConceptMap-shaped
export of the friendly layer) — plus CLI parity for optimize.

OP1 (MEDIUM, NEW) — optimize emits VALUE-SET RULES for nonexistent
    codes.
    Unknown code → 200 with rules=[{include: '99999999',
    include_source: 'SNOMEDCT_US'}] on BOTH surfaces (REST + CLI).
    Root cause (_OptimizeOps._normalize_optimize_input): a code with
    no descendant leaves falls through to `leaves.add(code)` — the
    no-descendants fallback conflates LEAF concepts with NONEXISTENT
    concepts. QC-195 fixed SOURCE existence at the boundary; CODE
    existence is never checked. A downstream system materializing the
    rule builds a value set referencing a void concept, silently.
    Fix shape: get_code_infos existence check at the service boundary
    → ValueError → 400 (REST) / 'Error:' exit 1 (CLI), mirroring
    QC-195's source fix. Flip pins o10-o12.

OP2 (LOW-MED, NEW) — relationship free-string unvalidated.
    relationship='bogus' → 200 with the value echoed verbatim
    (relationship='bogus' in the result) on BOTH surfaces. Only
    'prefix' is special-cased; every other string flows into the
    hierarchy walk, matches nothing, and yields degenerate echo rules
    labeled with the bogus relationship. Fix shape: enum-gate against
    the known hierarchy relationships at the boundary (the set
    _DEFAULT_OPTIMIZE_REL draws from). Flip pin o20/o21.

OP3 (LOW, NEW) — ConceptMap export labels success as not-translated.
    /conceptmap/patient-friendly for a successfully-crosswalked code
    (matched_via shows the ICD10CM friendly atom; target_display
    carries the friendly name; match_type='original') emits
    relationship='not-translated' — the R4 ConceptMapRelationship
    failure value on the SUCCESS path. Also target_code uses the
    composite 'SNOMEDCT_US:44054006' (the INPUT's own code) under
    target_source='PATIENT_FRIENDLY' — a pseudo-system + composite
    form no FHIR consumer can dereference. Fix shape: relationship
    'equivalent' (or 'relatedto') on translated rows; target_code =
    the friendly grain actually served. Flip pin o30/o31.

Controls (fresh): optimize happy shape (strategy greedy_hierarchy,
    reduction computed, compact rules); output_format validated
    (compact|flat ValueError); source-existence rejection held
    (QC-195 — 'has no codes in this database'); QC-199 source-override
    conflict rejected; /conceptmap/patient-friendly matched_via
    provenance chain (input → friendly_atom steps) present and
    truthful.
"""

from __future__ import annotations

import pytest

SNOMED = "SNOMEDCT_US"
T2DM = "44054006"


@pytest.fixture(scope="module")
def rest_client(tmp_path_factory):
    pytest.importorskip("fastapi")
    from starlette.testclient import TestClient

    from medterm4ds.apps.api import ApiSettings, create_app

    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts26_rest") / "umls.duckdb"
    _make_conformance_db(db)
    app = create_app(ApiSettings(db_path=db))
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _code(c: str, source: str = SNOMED) -> dict:
    return {"source": source, "code": c}


class TestOP1RulesForNonexistentCodes:
    """OP1 — unknown codes become include rules."""

    def test_o10_rest_unknown_code_rule(self, rest_client):
        """Unknown code → 200 with an include rule FOR the void
        concept. Flip when the boundary existence check lands
        (400/match_type=none)."""
        r = rest_client.post(
            "/optimize", json={"codes": [_code("99999999")]}
        )
        assert r.status_code == 200
        rec = r.json()["results"][0]
        assert rec["rules"][0]["include"] == "99999999"

    def test_o11_rest_valid_still_works(self, rest_client):
        """Premise control: valid codes produce the normal compact
        rule (the defect is the unknown-code path, not the walk)."""
        r = rest_client.post(
            "/optimize", json={"codes": [_code(T2DM)]}
        )
        rec = r.json()["results"][0]
        assert rec["rules"][0]["include"] == T2DM
        assert rec["strategy"] == "greedy_hierarchy"

    def test_o12_rest_mixed_poisons_ruleset(self, rest_client):
        """[valid, unknown] → BOTH in the ruleset: one void concept
        smuggled into an otherwise-valid value set (the silent
        downstream harm)."""
        r = rest_client.post(
            "/optimize", json={"codes": [_code(T2DM), _code("99999999")]}
        )
        includes = [
            rule["include"]
            for rec in r.json()["results"]
            for rule in rec["rules"]
        ]
        assert "99999999" in includes


class TestOP2RelationshipUnvalidated:
    """OP2 — free-string relationship echoed at 200."""

    def test_o20_rest_bogus_relationship(self, rest_client):
        """relationship='bogus' → 200, echoed verbatim in the result;
        the hierarchy walk silently matched nothing (degenerate echo
        rules). Flip when enum-gated (400 naming valid values)."""
        r = rest_client.post(
            "/optimize",
            json={"codes": [_code(T2DM)], "relationship": "bogus"},
        )
        assert r.status_code == 200
        assert r.json()["results"][0]["relationship"] == "bogus"

    def test_o21_prefix_still_rejected(self, rest_client):
        """Premise control: the ONE special-cased value ('prefix')
        400s — validation infrastructure exists, the enum just never
        extended."""
        r = rest_client.post(
            "/optimize",
            json={"codes": [_code(T2DM)], "relationship": "prefix"},
        )
        assert r.status_code == 400


class TestOP3ConceptMapLabelsSuccessNotTranslated:
    """OP3 — success path emits not-translated."""

    def test_o30_relationship_not_translated_on_success(
        self, rest_client
    ):
        """A fully-successful crosswalk (target_display carries the
        friendly name; matched_via shows the friendly atom) is labeled
        relationship='not-translated'. Flip when translated rows
        carry equivalent/relatedto."""
        r = rest_client.post(
            "/conceptmap/patient-friendly",
            json={"codes": [_code(T2DM)]},
        )
        rec = r.json()["results"][0]
        assert rec["target_display"] == "Type 2 Diabetes Mellitus"
        assert rec["match_type"] == "original"
        assert rec["relationship"] == "not-translated"

    def test_o31_target_code_composite_form(self, rest_client):
        """target_code is the composite 'SNOMEDCT_US:44054006' (the
        input's own code) under pseudo-system PATIENT_FRIENDLY — not
        dereferenceable by a FHIR consumer. Pinned as part of OP3's
        semantic garble."""
        r = rest_client.post(
            "/conceptmap/patient-friendly",
            json={"codes": [_code(T2DM)]},
        )
        rec = r.json()["results"][0]
        assert rec["target_source"] == "PATIENT_FRIENDLY"
        assert rec["target_code"] == f"{SNOMED}:{T2DM}"


class TestOptimizeControls:
    """Controls — held contracts, fresh."""

    def test_c10_happy_shape(self, rest_client):
        r = rest_client.post(
            "/optimize", json={"codes": [_code(T2DM)]}
        )
        rec = r.json()["results"][0]
        assert rec["source"] == SNOMED
        assert rec["original_count"] == 1
        assert rec["optimized_count"] == 1
        assert rec["reduction"] == 0.0

    def test_c11_output_format_validated(self, rest_client):
        r = rest_client.post(
            "/optimize",
            json={"codes": [_code(T2DM)], "output_format": "bogus"},
        )
        assert r.status_code == 422  # pydantic Literal gate

    def test_c12_source_existence_held(self, rest_client):
        """QC-195 held: unknown SOURCE rejected at the boundary —
        the exact check OP1 asks to extend to codes."""
        r = rest_client.post(
            "/optimize", json={"codes": [_code("X", "NOTASAB")]}
        )
        assert r.status_code == 400
        assert "has no codes" in r.json()["detail"]

    def test_c13_source_override_conflict(self, rest_client):
        """QC-199 held: mixed-source codes + source override → 400."""
        r = rest_client.post(
            "/optimize",
            json={
                "codes": [_code(T2DM), _code("E11", "ICD10CM")],
                "source": SNOMED,
            },
        )
        assert r.status_code == 400

    def test_c14_cm_pf_provenance_chain(self, rest_client):
        """matched_via carries the truthful crosswalk provenance
        (input → friendly_atom steps) — the basis of OP3's 'success
        mislabeled' claim."""
        r = rest_client.post(
            "/conceptmap/patient-friendly",
            json={"codes": [_code(T2DM)]},
        )
        rec = r.json()["results"][0]
        steps = rec["matched_via"]["steps"]
        assert steps[0]["op"] == "input"
        assert steps[-1]["op"] == "friendly_atom"
        assert rec["friendly_source"] == "ICD10CM"
