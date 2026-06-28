"""Tier 2: fhir4px build smoke tests.

Validates that each build_fhir4px_*.py script produces its expected output
file with the expected record count. The build itself runs once via the
session-scoped `fhir4px_built` fixture; these tests inspect the result.

Pinned counts live in tests/regression/fixtures/pinned_meta.json. If a count
drifts, the test name encodes why (e.g. `_atc_standalone_not_in_spec`).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .conftest import (
    ASSOCIATIONS_DELIVERABLE,
    EMBEDDING_DELIVERABLES,
    PATIENT_FRIENDLY_CSV_DELIVERABLE,
    PATIENT_FRIENDLY_DELIVERABLES,
    RXNORM_INGREDIENTS_DELIVERABLE,
)
from .golden.normalize import (
    EMBEDDING_CATEGORIES,
    PATIENT_FRIENDLY_SOURCES,
    load_canonical,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
PINNED_META = json.loads((FIXTURES_DIR / "pinned_meta.json").read_text())


def _pinned_count(section: str, key: str | None = None) -> int:
    section_data = PINNED_META[section]
    if key is None:
        return section_data["count"]
    return section_data[key]["count"]


# ---------- patient_friendly deliverables ----------

_PF_BY_SOURCE = {
    "icd10cm": "patient_friendly_icd10cm.json",
    "icd10pcs": "patient_friendly_icd10pcs.json",
    "snomedct_us": "patient_friendly_snomedct_us.json",
    "rxnorm": "patient_friendly_rxnorm.json",
    "lnc": "patient_friendly_lnc.json",
    "cpt": "patient_friendly_cpt.json",
    "hcpcs": "patient_friendly_hcpcs.json",
    "cvx": "patient_friendly_cvx.json",
}


@pytest.mark.fhir4px_smoke
@pytest.mark.parametrize("source", list(PATIENT_FRIENDLY_SOURCES))
def test_patient_friendly_json_count_pinned(source: str, fhir4px_built) -> None:
    """Per-source patient_friendly JSON record count must match pinned baseline."""
    path = fhir4px_built.patient_friendly_jsons[source]
    assert path.exists(), f"Missing {path}"
    actual = len(load_canonical(path))
    expected = _pinned_count("patient_friendly", source)
    assert actual == expected, f"patient_friendly_{source}: {actual} != {expected}"


@pytest.mark.fhir4px_smoke
def test_patient_friendly_csv_count_pinned_at_1127094(fhir4px_built) -> None:
    """Combined CSV must match pinned row count (1,127,094 as of 2026AA)."""
    path = fhir4px_built.patient_friendly_csv
    assert path.exists()
    actual = len(load_canonical(path))
    expected = _pinned_count("patient_friendly_csv")
    assert actual == expected, f"patient_friendly_names.csv: {actual} != {expected}"


# ---------- embedding_index deliverables ----------

_EMBEDDING_MEDICATION_COUNT_NOTE = (
    "Spec says 117,544 but actual is 124,540 — the 6,996 delta is ATC standalone "
    "records written into embedding_index_medication.jsonl (see scripts/"
    "build_fhir4px_embedding_index.py atc_standalone rule). Spec needs updating."
)


@pytest.mark.fhir4px_smoke
@pytest.mark.parametrize("category", list(EMBEDDING_CATEGORIES))
def test_embedding_index_count_pinned(category: str, fhir4px_built) -> None:
    """Per-category embedding_index JSONL record count must match pinned baseline."""
    path = fhir4px_built.embedding_jsonls[category]
    assert path.exists(), f"Missing {path}"
    actual = len(load_canonical(path))
    expected = _pinned_count("embedding_index", category)
    assert actual == expected, f"embedding_index_{category}: {actual} != {expected}"


@pytest.mark.fhir4px_smoke
def test_embedding_index_medication_count_pinned_at_135469_post_tty_fix(
    fhir4px_built,
) -> None:
    """Embedding medication count pinned at 135,469 (post-TTY-FIX 2026-06-26).

    Was 124,540 before TTY-FIX. The fix corrected 11,410 RxNorm codes' TTYs
    from SY/TMSY/PSN (synonym-class, not in the medication TTY filter) to
    SBD/SCD/SCDG/etc. (clinically-specific, in the filter). Those codes
    now correctly appear in the medication embedding.

    Spec still says 117,544 — original spec was stale before the TTY fix
    and is even staler now. Update pending.
    """
    path = fhir4px_built.embedding_jsonls["medication"]
    actual = len(load_canonical(path))
    assert actual == 135469


# ---------- associations ----------


@pytest.mark.fhir4px_smoke
def test_associations_count_pinned(fhir4px_built) -> None:
    """condition_associations.json condition count must match pinned baseline."""
    path = fhir4px_built.associations
    assert path.exists()
    actual = len(load_canonical(path))
    expected = _pinned_count("associations")
    assert actual == expected, f"condition_associations: {actual} != {expected}"


@pytest.mark.fhir4px_smoke
def test_associations_lab_associations_pinned_at_283_with_synthea(
    fhir4px_built,
) -> None:
    """Associations file has 283 lab associations from Synthea (34 conditions).

    Previously 0 because build_fhir4px_all.py did not pass --synthea-labs.
    Fixed 2026-06-27: orchestrator now passes --synthea-labs by default.
    """
    with fhir4px_built.associations.open() as f:
        raw = json.load(f)
    total_labs = sum(len(v.get("labs", [])) for v in raw.values() if isinstance(v, dict))
    assert total_labs == 283, f"Expected 283 lab associations, got {total_labs}"


# ---------- rxnorm-ingredients ----------


@pytest.mark.fhir4px_smoke
def test_rxnorm_ingredients_count_pinned(fhir4px_built) -> None:
    """rxnorm-ingredients.json product count must match pinned baseline."""
    path = fhir4px_built.rxnorm_ingredients
    assert path.exists()
    actual = len(load_canonical(path))
    expected = _pinned_count("rxnorm_ingredients")
    assert actual == expected, f"rxnorm-ingredients: {actual} != {expected}"


# ---------- UMLS release pin ----------


@pytest.mark.fhir4px_smoke
def test_umls_release_pinned(umls_release_tag: str) -> None:
    """The DB under test must match the pinned UMLS release.

    If this fails: the DB was rebuilt against a new UMLS release. Re-run
    scripts/build_fhir4px_all.py, review the diff vs reports/fhir4px/, and
    re-run tests/regression/regenerate_pinned_meta.py.
    """
    expected = PINNED_META["umls_release"]
    assert umls_release_tag == expected, (
        f"UMLS release changed: {expected} -> {umls_release_tag}. "
        "Rebuild baseline and regenerate pinned_meta.json."
    )
