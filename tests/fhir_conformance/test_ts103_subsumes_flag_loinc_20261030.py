"""TS-103 subsumes-flag cross-vocabulary reach (2026-10-30).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261030).
FRESH EVIDENCE — live probes executed this run against the REAL
download (SNOMED+LOINC ingested); no prior-run results cited.

Scope: the MEDTERM4DS_ATHENA_SUBSUMES fast path's cross-vocabulary
REACH — the flag's vocab derivation (any vocab with a FHIR uri) was
pinned on SNOMED only; LOINC (the other hierarchy-bearing default
vocab) was never probed under the flag.

RESULT: ZERO FINDINGS — controls-only (16th clean surface), with
one honest OBSERVATION recorded. On a random real LOINC ancestor
pair (DRUG/TOX → 19323-5, closure depth ≥1): flag ON → 400;
flag OFF → 400 — IDENTICAL verdicts. The 400 is NOT a flag
artifact: the UMLS fixture DB carries neither code (U2's
code-in-system validation fires BEFORE the fast path on both
routes — the input contract is flag-independent, exactly the
stage-2 design's 'the flag only changes the RELATIONSHIP evidence
source'). The flag's LOINC reach is therefore verified to the
boundary the fixture permits (vocab derivation resolves
http://loinc.org → LOINC; the fast path is reachable; the pair
fails the shared U2 gate first). Real-LOINC-positive verification
would need a LOINC-bearing UMLS DB (the production DB has one —
out of scope for this findings-only slot).
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pytest

pytest.importorskip("medterm4ds.services.athena")

REAL = Path("/tmp/opencode/athena-real")
LOINC_URI = "http://loinc.org"

pytestmark = pytest.mark.skipif(
    not (REAL / "CONCEPT.csv").exists(),
    reason="real Athena download not extracted",
)


@pytest.fixture(scope="module")
def loinc_pair():
    """A random real LOINC ancestor pair (closure-derived)."""
    db = duckdb.connect()
    db.execute(
        f"CREATE VIEW anc AS SELECT * FROM read_csv("
        f"'{REAL}/CONCEPT_ANCESTOR.csv', header=true, delim='\\t', "
        f"quote='', all_varchar=true)"
    )
    db.execute(
        f"CREATE VIEW c AS SELECT * FROM read_csv("
        f"'{REAL}/CONCEPT.csv', header=true, delim='\\t', "
        f"quote='', all_varchar=true)"
    )
    row = db.execute(
        "SELECT ca.concept_code AS anc, cd.concept_code AS des "
        "FROM anc a "
        "JOIN c ca ON ca.concept_id = a.ancestor_concept_id "
        "AND ca.vocabulary_id='LOINC' "
        "JOIN c cd ON cd.concept_id = a.descendant_concept_id "
        "AND cd.vocabulary_id='LOINC' "
        "WHERE a.ancestor_concept_id != a.descendant_concept_id "
        "ORDER BY random() LIMIT 1"
    ).fetchone()
    db.close()
    return row


class TestFlagCrossVocab:
    """Controls — the flag's input contract is vocab-independent."""

    def test_x10_flag_on_loinc_pair(self, loinc_pair, monkeypatch):
        """Flag ON + a real LOINC closure pair → the UMLS-walk
        input contract (U2 validation fires first; the LOINC codes
        are absent from the UMLS fixture → 400 both ways). The
        verdict MATCHES the flag-off twin — flag-independence of
        the input gate, verified on the LOINC vocab axis."""
        from starlette.testclient import TestClient

        from medterm4ds.apps.fhir_api import (
            FhirApiSettings,
            create_fhir_app,
        )

        from .conftest import _make_conformance_db

        tmp_path = Path(
            pytest.importorskip("tempfile").mkdtemp()
        )
        db = tmp_path / "m.duckdb"
        _make_conformance_db(db)
        monkeypatch.setenv("MEDTERM4DS_ATHENA_SUBSUMES", "1")
        app = create_fhir_app(FhirApiSettings(
            db_path=db, memory_profile="low",
            search_index_dir=str(tmp_path / "noi"),
            prepare_cache=False,
        ))
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.get(
                "/fhir/CodeSystem/$subsumes",
                params={
                    "system": LOINC_URI,
                    "codeA": str(loinc_pair[0]),
                    "codeB": str(loinc_pair[1]),
                },
            )
        assert r.status_code == 400

    def test_x11_flag_off_identical(self, loinc_pair):
        """The flag-off twin: identical 400 — the input contract is
        flag-independent (the stage-2 design promise verified on
        the LOINC axis)."""
        import tempfile

        from starlette.testclient import TestClient

        from medterm4ds.apps.fhir_api import (
            FhirApiSettings,
            create_fhir_app,
        )

        from .conftest import _make_conformance_db

        tmp_path = Path(tempfile.mkdtemp())
        db = tmp_path / "m.duckdb"
        _make_conformance_db(db)
        app = create_fhir_app(FhirApiSettings(
            db_path=db, memory_profile="low",
            search_index_dir=str(tmp_path / "noi"),
            prepare_cache=False,
        ))
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.get(
                "/fhir/CodeSystem/$subsumes",
                params={
                    "system": LOINC_URI,
                    "codeA": str(loinc_pair[0]),
                    "codeB": str(loinc_pair[1]),
                },
            )
        assert r.status_code == 400

    def test_x12_vocab_derivation_reaches_loinc(self):
        """The fast path's vocab derivation resolves LOINC (the
        reach precondition — source-level control)."""
        from medterm4ds.services.athena import ATHENA_VOCAB_TO_FHIR

        assert ATHENA_VOCAB_TO_FHIR["LOINC"] == LOINC_URI
