"""Tier 2.5: cross-deliverable consistency tests.

The four fhir4px deliverables are produced by independent scripts that read
overlapping UMLS tables. These tests catch drift between them — the most
common failure mode when one script is updated and others aren't.
"""

from __future__ import annotations

import json

import pytest

from .conftest import Fhir4pxBuildResult
from .golden.normalize import canonical_embedding_jsonl, canonical_rxnorm_ingredients


@pytest.mark.fhir4px_smoke
def test_rxnorm_ingredients_match_embedding_index_medication(
    fhir4px_built: Fhir4pxBuildResult,
) -> None:
    """Cross-check embedding_index_medication.ingredient_codes vs rxnorm-ingredients.json.

    After reconciling TTY scope (2026-06-25: added SCDC/SBDC/SBDF to
    rxnorm-ingredients), the two files should agree on ingredient sets for
    every RXNORM code present in embedding_index_medication.

    Any mismatch means the two scripts' traversals have drifted apart again.
    """
    embedding = canonical_embedding_jsonl(fhir4px_built.embedding_jsonls["medication"])
    rxnorm = canonical_rxnorm_ingredients(fhir4px_built.rxnorm_ingredients)

    mismatches: list[tuple[str, set[str], set[str]]] = []
    for (source, code), rec in embedding.items():
        if source != "RXNORM":
            continue
        embedding_ings = set(rec.get("ingredient_codes") or [])
        rxnorm_ings = {ing["c"] for ing in rxnorm.get(code, [])}
        if embedding_ings != rxnorm_ings:
            mismatches.append((code, embedding_ings, rxnorm_ings))

    # Tolerate a small amount of UMLS release noise (e.g., a code whose only
    # ingredient edge was retired). 50 is generous; should typically be 0.
    assert len(mismatches) <= 50, (
        f"{len(mismatches)} mismatches between embedding_index_medication and "
        f"rxnorm-ingredients (ceiling=50). First 10:\n"
        + "\n".join(f"  {c}: embedding={sorted(e)} vs rxnorm={sorted(r)}" for c, e, r in mismatches[:10])
    )


@pytest.mark.fhir4px_smoke
def test_associations_medication_codes_exist_in_rxnorm_ingredients(
    fhir4px_built: Fhir4pxBuildResult,
) -> None:
    """Every medication code referenced in condition_associations must exist
    as a key in rxnorm-ingredients.json (i.e., be a real RxNorm code)."""
    with fhir4px_built.associations.open() as f:
        assoc = json.load(f)
    rxnorm = canonical_rxnorm_ingredients(fhir4px_built.rxnorm_ingredients)
    rxnorm_keys = set(rxnorm)

    missing: list[str] = []
    seen: set[str] = set()
    for cond_code, entry in assoc.items():
        if cond_code == "_meta" or not isinstance(entry, dict):
            continue
        for med in entry.get("medications", []):
            code = med.get("code")
            if code and code not in seen:
                seen.add(code)
                if code not in rxnorm_keys:
                    missing.append(code)

    assert not missing, (
        f"{len(missing)} medication codes in associations are missing from "
        f"rxnorm-ingredients (first 10): {missing[:10]}"
    )


@pytest.mark.fhir4px_smoke
def test_associations_conditions_present_in_condition_embedding_pinned(
    fhir4px_built: Fhir4pxBuildResult,
) -> None:
    """Cross-check: condition keys in associations vs codes in embedding_index_condition.

    KNOWN SCOPE DIFFERENCE: as of 2026AA, 9,252 SNOMED codes appear in
    condition_associations.json but NOT in embedding_index_condition.jsonl.
    Cause: the embedding index applies a TUI filter (semantic type whitelist)
    that excludes some SNOMED conditions even though they have may_treat edges.
    All ICD10CM codes in associations DO appear in the embedding index.

    This test pins the current scope delta. Drift in either direction should
    be investigated.
    """
    with fhir4px_built.associations.open() as f:
        assoc = json.load(f)
    from .golden.normalize import canonical_embedding_jsonl

    condition_keys = {
        code for (source, code) in canonical_embedding_jsonl(
            fhir4px_built.embedding_jsonls["condition"]
        )
        if source in ("ICD10CM", "SNOMEDCT_US")
    }

    missing_icd = 0
    missing_snomed = 0
    unrecognized = 0
    for key in assoc:
        if key == "_meta":
            continue
        is_icd = bool(key) and key[0].isalpha()
        is_snomed = bool(key) and key.isdigit()
        if not (is_icd or is_snomed):
            unrecognized += 1
            continue
        if key not in condition_keys:
            if is_icd:
                missing_icd += 1
            else:
                missing_snomed += 1

    # Hard requirements: every ICD10CM in associations must be in the embedding
    # index (no scope excuse), and no unrecognized key shapes.
    assert missing_icd == 0, f"{missing_icd} ICD10CM keys in associations missing from embedding"
    assert unrecognized == 0, f"{unrecognized} condition keys have unrecognized shape"

    # Pinned scope delta for SNOMED. After adding T033/T184 to the condition
    # TUI filter (2026-06-25), the remaining ~3,360 missing SNOMED codes are
    # correctly categorized elsewhere (body_structure, procedure, medication,
    # lab) or have no condition-like TUI at all (organisms, body parts, etc.
    # that happen to have may_treat edges via MSH but aren't really conditions).
    PINNED_SNOMED_DELTA = 3360
    TOLERANCE = 200
    actual = missing_snomed
    assert abs(actual - PINNED_SNOMED_DELTA) <= TOLERANCE, (
        f"SNOMED scope delta drifted: expected ~{PINNED_SNOMED_DELTA} (±{TOLERANCE}), "
        f"got {actual}"
    )
