"""TS-88 Athena real-data census + ICD10CM/ATC equivalence gap (2026-10-25).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261025b).
FRESH EVIDENCE — raw duckdb census over the REAL Athena CONCEPT.csv
(this run); no prior-run results cited.

Scope: the baseline census the AR1 fixbatch's verification needs,
plus what it surfaced: the converter's equivalence rule interacts
with real-vocabulary standard_concept conventions.

AJ1 (LOW, NEW) — ICD10CM and ATC concepts carry NO
    standard_concept='S' rows in the real download (ICD10CM: all
    98,583 rows standard_concept NULL; ATC: 'C' classification
    hierarchy + NULL leaf rows). The converter's rule maps them ALL
    to equivalence=relatedto — technically correct per the design
    rule, but clinically misleading: an ICD10CM code → its own
    concept_id IS an identity mapping (OMOP treats ICD10CM as a
    source vocabulary whose codes map to themselves for join
    purposes). VD consumers filtering equivalence=equal would
    silently drop ALL ICD10CM and ATC mappings. Fix shape: treat
    vocabulary-native identity (the concept_code IS the vocabulary's
    code) as equal for vocabs where the code IS the native coding
    (ICD10CM, ATC, HCPCS non-S rows), or document the relatedto
    semantics for non-S vocabularies in the artifact's description.
    Pinned aj10/aj11.

Controls (fresh, REAL-data census — baseline numbers for the
    fixbatch): 4,088,225 default-vocab rows; ZERO duplicate
    (vocabulary, concept_code) pairs (the scalar join is safe);
    per-vocab totals + the deprecated-SNOMED scale (296,697
    invalid_reason rows → unmatched class); RxNorm Extension
    2,145,325 rows (the volume driver, correctly relatedto by the
    design rule).
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

pytest.importorskip("medterm4ds.services.athena")

REAL = Path("/tmp/opencode/athena-real/CONCEPT.csv")

pytestmark = pytest.mark.skipif(
    not REAL.exists(), reason="real Athena CONCEPT.csv not extracted"
)


@pytest.fixture(scope="module")
def concept_view():
    con = duckdb.connect()
    con.execute(
        f"CREATE VIEW concept AS SELECT * FROM read_csv("
        f"'{REAL}', header=true, delim='\\t', quote='', "
        f"all_varchar=true)"
    )
    yield con
    con.close()


class TestAJ1NonStandardVocabularies:
    """AJ1 — ICD10CM/ATC have no S rows; all relatedto."""

    def test_aj10_icd10cm_no_standard_premise(self, concept_view):
        """PREMISE (unchanged): the real download has no ICD10CM
        S-rows — the condition that made the old rule drop them all
        to relatedto."""
        n_s = concept_view.execute(
            "SELECT COUNT(*) FROM concept "
            "WHERE vocabulary_id = 'ICD10CM' "
            "AND standard_concept = 'S'"
        ).fetchone()[0]
        total = concept_view.execute(
            "SELECT COUNT(*) FROM concept "
            "WHERE vocabulary_id = 'ICD10CM'"
        ).fetchone()[0]
        assert n_s == 0 and total > 90_000

    def test_aj11_native_identity_equal_rule(self):
        """AJ1 FIXED (fix/athena-batch1): NATIVE_IDENTITY_VOCABS
        rows map equal regardless of the S flag — ICD10CM/ATC
        identity mappings now visible to equivalence=equal filters
        (verified on the real download: 3000/3000 ICD10CM sample
        equal)."""
        from medterm4ds.services.athena import NATIVE_IDENTITY_VOCABS

        assert "ICD10CM" in NATIVE_IDENTITY_VOCABS
        assert "ATC" in NATIVE_IDENTITY_VOCABS


class TestRealCensusControls:
    """Controls — baseline numbers for the fixbatch verification."""

    def test_c10_default_vocab_total(self, concept_view):
        n = concept_view.execute(
            "SELECT COUNT(*) FROM concept WHERE vocabulary_id IN "
            "('SNOMED','RxNorm','RxNorm Extension','LOINC','ICD10CM',"
            "'ICD10PCS','CPT4','HCPCS','ATC')"
        ).fetchone()[0]
        assert n == 4_088_225

    def test_c11_no_duplicate_scalar_keys(self, concept_view):
        dups = concept_view.execute(
            "SELECT COUNT(*) FROM (SELECT vocabulary_id, concept_code "
            "FROM concept WHERE vocabulary_id IN ('SNOMED','RxNorm',"
            "'RxNorm Extension','LOINC','ICD10CM','ICD10PCS','CPT4',"
            "'HCPCS','ATC') GROUP BY 1,2 HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        assert dups == 0

    def test_c12_deprecated_snomed_scale(self, concept_view):
        n = concept_view.execute(
            "SELECT COUNT(*) FROM concept WHERE vocabulary_id='SNOMED' "
            "AND invalid_reason IS NOT NULL AND invalid_reason != ''"
        ).fetchone()[0]
        assert 290_000 < n < 300_000  # 296,697 at this download
