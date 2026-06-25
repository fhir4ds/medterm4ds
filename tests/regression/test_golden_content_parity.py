"""Tier 4: full content golden parity tests.

For each fhir4px deliverable, compares the freshly-built output against the
canonical baseline at reports/fhir4px/. Catches ANY field-level drift on ANY
record (synonym reordering, friendly_name case changes, ATC level renames, etc.).

Normalization (strip timestamps, sort unordered lists) is in golden/normalize.py.
Structured diff reporting is in golden/{compare,report}.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .conftest import Fhir4pxBuildResult
from .golden.compare import compare
from .golden.normalize import (
    EMBEDDING_CATEGORIES,
    PATIENT_FRIENDLY_SOURCES,
    detect_kind,
    load_canonical,
)
from .golden.report import format_diff

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _baseline_path(baseline_dir: Path, kind: str, key: str) -> Path:
    """Resolve a baseline file path by (kind, key)."""
    filenames = {
        "patient_json": f"patient_friendly_{key}.json",
        "patient_csv": "patient_friendly_names.csv",
        "embedding": f"embedding_index_{key}.jsonl",
        "associations": "condition_associations.json",
        "rxnorm_ingredients": "rxnorm-ingredients.json",
    }
    return baseline_dir / filenames[kind]


def _assert_parity(name: str, expected_path: Path, actual_path: Path) -> None:
    """Compare two files after canonicalization; fail with structured diff."""
    expected = load_canonical(expected_path)
    actual = load_canonical(actual_path)
    diff = compare(expected, actual)
    if diff:
        pytest.fail(format_diff(name, diff))


# ---------- patient_friendly JSONs ----------


@pytest.mark.fhir4px_smoke
@pytest.mark.parametrize("source", list(PATIENT_FRIENDLY_SOURCES))
def test_golden_patient_friendly_json(
    source: str, fhir4px_built: Fhir4pxBuildResult, fhir4px_baseline_dir: Path
) -> None:
    """Full content parity for patient_friendly_<source>.json."""
    _assert_parity(
        f"patient_friendly_{source}.json",
        _baseline_path(fhir4px_baseline_dir, "patient_json", source),
        fhir4px_built.patient_friendly_jsons[source],
    )


# ---------- patient_friendly CSV ----------


@pytest.mark.fhir4px_smoke
def test_golden_patient_friendly_csv(
    fhir4px_built: Fhir4pxBuildResult, fhir4px_baseline_dir: Path
) -> None:
    """Full content parity for patient_friendly_names.csv (1.13M rows)."""
    _assert_parity(
        "patient_friendly_names.csv",
        _baseline_path(fhir4px_baseline_dir, "patient_csv", ""),
        fhir4px_built.patient_friendly_csv,
    )


# ---------- embedding JSONLs ----------


@pytest.mark.fhir4px_smoke
@pytest.mark.parametrize("category", list(EMBEDDING_CATEGORIES))
def test_golden_embedding_index(
    category: str, fhir4px_built: Fhir4pxBuildResult, fhir4px_baseline_dir: Path
) -> None:
    """Full content parity for embedding_index_<category>.jsonl."""
    _assert_parity(
        f"embedding_index_{category}.jsonl",
        _baseline_path(fhir4px_baseline_dir, "embedding", category),
        fhir4px_built.embedding_jsonls[category],
    )


# ---------- associations ----------


@pytest.mark.fhir4px_smoke
def test_golden_associations(
    fhir4px_built: Fhir4pxBuildResult, fhir4px_baseline_dir: Path
) -> None:
    """Full content parity for condition_associations.json (102K conditions, 2.86M meds).

    Note: _meta.generated_at is stripped during normalization.
    """
    _assert_parity(
        "condition_associations.json",
        _baseline_path(fhir4px_baseline_dir, "associations", ""),
        fhir4px_built.associations,
    )


# ---------- rxnorm-ingredients ----------


@pytest.mark.fhir4px_smoke
def test_golden_rxnorm_ingredients(
    fhir4px_built: Fhir4pxBuildResult, fhir4px_baseline_dir: Path
) -> None:
    """Full content parity for rxnorm-ingredients.json (63K products)."""
    _assert_parity(
        "rxnorm-ingredients.json",
        _baseline_path(fhir4px_baseline_dir, "rxnorm_ingredients", ""),
        fhir4px_built.rxnorm_ingredients,
    )
