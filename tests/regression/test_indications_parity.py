"""Parity test: drugs_for_indication vs build_fhir4px_associations.

Two implementations of the may_treat/may_prevent traversal exist:
  - scripts/build_fhir4px_associations.py (inline SQL, produces condition_associations.json)
  - src/medterm4ds/engines/duckdb/indications.py (called by the domain layer's
    drugs_for_indication)

Both walk condition hierarchies -> MSH MH -> may_treat/may_prevent -> RXNORM IN.
Drift between them means downstream consumers see different medication lists
depending on which API they hit.

KNOWN SEMANTIC DIFFERENCE (surfaced 2026-06-26):
  - build_fhir4px_associations captures EVERY may_treat/may_prevent edge at any
    hierarchy depth (broad recall: "all drugs ever related to this condition").
  - engines/duckdb/indications picks the NEAREST-DEPTH edge per relationship
    type via the `nearest_depth` CTE (precision: "the closest drug-condition
    relationship").

The engine's result is therefore a STRICT SUBSET of associations for most
conditions. This test verifies the subset property holds and pins the
mismatch count so future drift is visible.

If the two implementations are ever unified (e.g., the engine gains a
`recall_mode='all_depths'` parameter), this test should be updated to
assert set equality instead of subset.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.engines.duckdb.indications import (
    format_condition_medication_row,
    validate_indication_relationships,
)

_RELATIONSHIP_TO_LABEL = {"may_treat": "treats", "may_prevent": "prevents"}


def _load_top_conditions(assoc_path: Path, per_source: int = 20) -> list[tuple[str, str]]:
    """Return top-N conditions by medication-association count, split by source."""
    with assoc_path.open() as f:
        assoc = json.load(f)
    icd: list[tuple[str, int]] = []
    snomed: list[tuple[str, int]] = []
    for key, entry in assoc.items():
        if key == "_meta" or not isinstance(entry, dict):
            continue
        meds = entry.get("medications", [])
        if not meds:
            continue
        if key and key[0].isalpha():
            icd.append((key, len(meds)))
        elif key and key.isdigit():
            snomed.append((key, len(meds)))
    icd.sort(key=lambda x: -x[1])
    snomed.sort(key=lambda x: -x[1])
    return ([("ICD10CM", k) for k, _ in icd[:per_source]]
            + [("SNOMEDCT_US", k) for k, _ in snomed[:per_source]])


@pytest.mark.realdb
@pytest.mark.slow
def test_drugs_for_indication_is_subset_of_build_fhir4px_associations(
    umls_db_path: Path, fhir4px_baseline_dir: Path
) -> None:
    """For sampled conditions, drugs_for_indication's (relationship, code) set
    must be a SUBSET of condition_associations.json's set.

    The engine uses nearest-depth filtering (precision); associations captures
    every depth (recall). Subset property must hold; if it doesn't, the engine
    is returning codes that the batch build didn't find -- real drift.
    """
    conditions = _load_top_conditions(fhir4px_baseline_dir / "condition_associations.json")
    assert len(conditions) >= 30, f"Expected 40 sample conditions, got {len(conditions)}"

    with (fhir4px_baseline_dir / "condition_associations.json").open() as f:
        assoc = json.load(f)

    con = duckdb.connect(str(umls_db_path), read_only=True)
    try:
        engine = LocalDuckDBEngine(con)
        relationships = validate_indication_relationships(("may_treat", "may_prevent"))

        subset_violations: list[tuple[str, set[tuple[str, str]]]] = []
        recall_ratios: list[float] = []
        for source, code in conditions:
            raw_rows = engine.get_drugs_for_indication(
                [(source, code, 1)],
                relationships=relationships,
                max_depth=5,
                limit=10000,
                include_product_groups=False,
            )
            engine_pairs: set[tuple[str, str]] = set()
            for raw in raw_rows:
                formatted = format_condition_medication_row(raw)
                label = _RELATIONSHIP_TO_LABEL.get(formatted["relationship"], formatted["relationship"])
                engine_pairs.add((label, str(formatted["relationship_target_code"])))

            assoc_pairs: set[tuple[str, str]] = set()
            for med in assoc.get(code, {}).get("medications", []):
                assoc_pairs.add((med["relationship"], str(med["code"])))

            # Subset property: every engine pair must appear in assoc.
            extra_in_engine = engine_pairs - assoc_pairs
            if extra_in_engine:
                subset_violations.append((code, extra_in_engine))

            # Recall ratio: how much of assoc the engine returns.
            if assoc_pairs:
                recall_ratios.append(len(engine_pairs & assoc_pairs) / len(assoc_pairs))

        # Hard requirement: subset property must hold for all sampled conditions.
        assert not subset_violations, (
            f"Engine returned {len(subset_violations)} conditions with codes NOT in "
            f"condition_associations.json. This is real drift (not the documented "
            f"nearest-depth subset behavior). First 3 violations:\n"
            + "\n".join(
                f"  {code}: engine-only pairs = {sorted(extra)[:5]}..."
                for code, extra in subset_violations[:3]
            )
        )

        # Pin the average recall ratio. The engine returns ~30-50% of assoc's
        # codes (only nearest-depth). If this drops below 20%, the engine is
        # being too aggressive with the nearest-depth filter; if it goes above
        # 70%, the engine may have dropped the filter (which would be a silent
        # behavior change worth investigating).
        avg_recall = sum(recall_ratios) / len(recall_ratios) if recall_ratios else 0.0
        assert 0.20 <= avg_recall <= 0.70, (
            f"Average recall ratio drifted outside [0.20, 0.70]: {avg_recall:.3f}. "
            f"This pins the documented nearest-depth subset behavior. "
            f"If you intentionally changed the engine's depth filtering, "
            f"update this range."
        )
    finally:
        con.close()
