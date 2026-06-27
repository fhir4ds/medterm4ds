"""Pinned TTY values for known RxNorm codes.

Regression guard for the TTY-FIX (2026-06-26) that corrected 11,410 RxNorm
codes from SY/TMSY/PSN to canonical TTYs (SBD/SCD/SCDG/etc.). If someone
reverts the fix or changes the atom-ranking logic, these assertions catch it.

Each entry includes a code, expected TTY, and a note about WHY this TTY is
expected. The codes span the TTY categories that were affected by the fix
(were SY before, now SBD/SCD/etc.).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# (code, expected_tty, reason)
PINNED_TTYS = [
    # Clinical drugs (correctly identified both before and after fix)
    ("860975", "SCD", "Metformin 500mg ER Oral Tablet — always SCD"),
    ("6809", "IN", "Metformin ingredient — always IN"),
    # Codes that were SY before the fix, now correctly SBD/GPCK/BPCK
    ("1000000", "SBD", "Was SY; SBD available (Amlodipine/HCTZ/Olmesartan combo)"),
    ("1000089", "SBD", "Was SY; SBD available (Alcaftadine Ophthalmic)"),
    ("1000479", "GPCK", "Was SY; GPCK available (Bisacodyl pack)"),
    ("1000487", "BPCK", "Was SY; BPCK available (Estrogens/Medroxyprogesterone pack)"),
    # PIN codes (ingredient-level, correctly identified after fix expanded priority)
    # TMSY codes where no preferred TTY exists (should remain TMSY)
    # -- not pinned here because their identity depends on UMLS release drift
]


@pytest.mark.realdb
@pytest.mark.parametrize(
    "code, expected_tty, reason",
    PINNED_TTYS,
    ids=[f"{code}:{tty}" for code, tty, _ in PINNED_TTYS],
)
def test_rxnorm_tty_pinned(
    code: str, expected_tty: str, reason: str, fhir4px_baseline_dir: Path
) -> None:
    """Verify TTY for specific known RxNorm codes in the baseline JSON."""
    json_path = fhir4px_baseline_dir / "patient_friendly_rxnorm.json"
    with json_path.open() as f:
        data = json.load(f)

    assert code in data, f"Code {code} not found in {json_path.name}"
    entry = data[code]
    actual_tty = entry.get("tty")
    assert actual_tty == expected_tty, (
        f"TTY mismatch for {code}: expected {expected_tty}, got {actual_tty}. "
        f"Reason: {reason}"
    )


@pytest.mark.realdb
def test_rxnorm_synonym_class_tty_under_2_percent(fhir4px_baseline_dir: Path) -> None:
    """Synonym-class TTYs (SY/TMSY/PSN/ET) should be under 2% of total.

    Before TTY-FIX: 12,842 / 124,919 = 10.3%.
    After TTY-FIX: ~1,432 / 124,919 = 1.1%.

    If this exceeds 2%, the TTY selection logic has regressed.
    """
    from collections import Counter

    json_path = fhir4px_baseline_dir / "patient_friendly_rxnorm.json"
    with json_path.open() as f:
        data = json.load(f)

    tty_counts = Counter(entry.get("tty") for entry in data.values())
    synonym_class = sum(tty_counts.get(t, 0) for t in ("SY", "TMSY", "PSN", "ET"))
    total = len(data)
    pct = synonym_class / total * 100

    assert pct < 2.0, (
        f"Synonym-class TTYs at {pct:.1f}% ({synonym_class:,}/{total:,}) — "
        f"threshold 2%. Before TTY-FIX this was 10.3%. If this regressed, "
        f"check _source_atom_order_sql and build_fhir4px_patient_friendly.py "
        f"ranking SQL."
    )
