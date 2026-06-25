"""Tier 3: per-record property/contract invariants.

Validates structural invariants from data-delivery-spec.md against EVERY record
in each fhir4px output. Catches schema breakage that spot-checks miss.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from .conftest import Fhir4pxBuildResult
from .golden.normalize import (
    EMBEDDING_CATEGORIES,
    PATIENT_FRIENDLY_SOURCES,
    canonical_associations,
    canonical_embedding_jsonl,
    canonical_patient_friendly_csv,
    canonical_patient_friendly_json,
    canonical_rxnorm_ingredients,
)

VALID_MATCH_TYPES = {
    "exact", "original", "broader", "narrower", "fallback", "source",
    # medterm4ds-specific match types produced by patient_friendly_prepared.py
    "first_axis", "group", "ingredient", "same_cui",
    "broader_ingredient", "broader_group",
    "snomed_fallback", "snomed_to_target_snomed_fallback", "snomed_to_target_native_hierarchy",
    "cvx_group",
}
VALID_RELATIONSHIPS = {"treats", "prevents"}
VALID_CATEGORIES = set(EMBEDDING_CATEGORIES)
VALID_RXNORM_TTYS = {
    "IN", "MIN", "SCD", "SBD", "SCDG", "SCDC", "SBDC", "SBDF", "BPCK", "GPCK", "BN", "PIN",
}
# Sources allowed in the medication embedding index. ATC records have their own atc field
# populated (they ARE the ATC entry); other non-RxNorm sources must have atc=null.
EMBEDDING_ATC_ALLOWED_SOURCES = {"RXNORM", "ATC"}
KNOWN_SOURCES = {"ICD10CM", "ICD10PCS", "SNOMEDCT_US", "RXNORM", "LNC", "CPT", "HCPCS", "CVX", "ATC", "MSH"}


# ---------- patient_friendly invariants ----------


@pytest.mark.fhir4px_smoke
@pytest.mark.parametrize("source", list(PATIENT_FRIENDLY_SOURCES))
def test_patient_friendly_json_invariants(source: str, fhir4px_built: Fhir4pxBuildResult) -> None:
    """Every entry: {name, friendly_source, match_type, cui}; name non-empty."""
    records = canonical_patient_friendly_json(fhir4px_built.patient_friendly_jsons[source])
    errors: list[str] = []
    for code, entry in records.items():
        if not entry.get("name"):
            errors.append(f"{source}/{code}: empty name")
        if entry.get("match_type") and entry["match_type"] not in VALID_MATCH_TYPES:
            errors.append(f"{source}/{code}: match_type={entry['match_type']!r}")
        if len(errors) >= 20:
            break
    assert not errors, "First 20 invariant violations:\n" + "\n".join(errors)


@pytest.mark.fhir4px_smoke
def test_patient_friendly_csv_invariants(fhir4px_built: Fhir4pxBuildResult) -> None:
    """CSV: every row has source, code, name non-empty; match_type valid."""
    records = canonical_patient_friendly_csv(fhir4px_built.patient_friendly_csv)
    errors: list[str] = []
    for (source, code), row in records.items():
        if not source:
            errors.append(f"row {code}: empty source")
        if not code:
            errors.append(f"row {source}: empty code")
        if not row.get("name"):
            errors.append(f"{source}/{code}: empty name")
        if row.get("match_type") and row["match_type"] not in VALID_MATCH_TYPES:
            errors.append(f"{source}/{code}: match_type={row['match_type']!r}")
        if len(errors) >= 20:
            break
    assert not errors, "First 20 invariant violations:\n" + "\n".join(errors)


# ---------- embedding_index invariants ----------


def _validate_embedding_record(rec: dict[str, Any], category: str) -> list[str]:
    """Return list of error strings for one embedding record."""
    errors: list[str] = []
    if rec.get("category") != category:
        errors.append(f"category={rec.get('category')!r} != {category!r}")

    code = rec.get("code")
    if not isinstance(code, dict):
        errors.append("code is not a dict")
        return errors
    if code.get("source") not in KNOWN_SOURCES:
        errors.append(f"code.source={code.get('source')!r}")
    if not code.get("code"):
        errors.append("code.code empty")
    if not code.get("name"):
        errors.append("code.name empty")

    vectors = rec.get("vectors")
    if not isinstance(vectors, dict):
        errors.append("vectors is not a dict")
        return errors
    if not vectors.get("technical"):
        errors.append("vectors.technical empty")
    if not vectors.get("friendly"):
        errors.append("vectors.friendly empty")
    if not isinstance(vectors.get("synonyms"), list):
        errors.append("vectors.synonyms is not a list")
    if not isinstance(vectors.get("hierarchy"), list):
        errors.append("vectors.hierarchy is not a list")

    # RXNORM-specific: ingredient_codes must be list (possibly empty for BN/PIN).
    if code.get("source") == "RXNORM":
        ingredient_codes = rec.get("ingredient_codes")
        if not isinstance(ingredient_codes, list):
            errors.append(f"RXNORM ingredient_codes not list: {type(ingredient_codes).__name__}")
        if code.get("tty") and code["tty"] not in VALID_RXNORM_TTYS:
            errors.append(f"RXNORM tty={code['tty']!r}")
    # ingredient_codes is null for non-RxNorm, list for RxNorm.
    if code.get("source") != "RXNORM" and rec.get("ingredient_codes") is not None:
        errors.append(f"non-RxNorm has ingredient_codes={rec.get('ingredient_codes')!r}")
    # atc field: dict for RXNORM and ATC standalone records; null otherwise.
    if rec.get("atc") is not None:
        if not isinstance(rec.get("atc"), dict):
            errors.append("atc is not dict or null")
        elif code.get("source") not in EMBEDDING_ATC_ALLOWED_SOURCES:
            errors.append(f"non-RxNorm/ATC has atc={rec.get('atc')!r}")

    return errors


@pytest.mark.fhir4px_smoke
@pytest.mark.parametrize("category", list(EMBEDDING_CATEGORIES))
def test_embedding_index_invariants(category: str, fhir4px_built: Fhir4pxBuildResult) -> None:
    """Every embedding record conforms to the documented schema."""
    records = canonical_embedding_jsonl(fhir4px_built.embedding_jsonls[category])
    errors: list[str] = []
    for key, rec in records.items():
        for err in _validate_embedding_record(rec, category):
            errors.append(f"{key}: {err}")
        if len(errors) >= 20:
            break
    assert not errors, f"First 20 invariant violations in {category}:\n" + "\n".join(errors)


# ---------- associations invariants ----------


def _depth_to_strength(depth: int) -> str:
    if depth <= 1:
        return "strong"
    if depth == 2:
        return "moderate"
    return "weak"


@pytest.mark.fhir4px_smoke
def test_associations_invariants(fhir4px_built: Fhir4pxBuildResult) -> None:
    """Every med entry: {code, strength, relationship, depth}; depth 0-5; strength matches depth."""
    records = canonical_associations(fhir4px_built.associations)
    errors: list[str] = []
    for cond_code, entry in records.items():
        for med in entry.get("medications", []):
            if not med.get("code"):
                errors.append(f"{cond_code}: med.code empty")
            if med.get("relationship") not in VALID_RELATIONSHIPS:
                errors.append(f"{cond_code}: relationship={med.get('relationship')!r}")
            depth = med.get("depth")
            if not isinstance(depth, int) or not (0 <= depth <= 5):
                errors.append(f"{cond_code}/{med.get('code')}: depth={depth!r}")
            elif med.get("strength") != _depth_to_strength(depth):
                errors.append(
                    f"{cond_code}/{med.get('code')}: strength={med.get('strength')!r} "
                    f"expected {_depth_to_strength(depth)!r} for depth={depth}"
                )
            if len(errors) >= 20:
                break
        if len(errors) >= 20:
            break
    assert not errors, "First 20 invariant violations:\n" + "\n".join(errors)


# ---------- rxnorm-ingredients invariants ----------


@pytest.mark.fhir4px_smoke
def test_rxnorm_ingredients_invariants(fhir4px_built: Fhir4pxBuildResult) -> None:
    """Every value is list[{c, n}]; c is non-empty string; n is string."""
    records = canonical_rxnorm_ingredients(fhir4px_built.rxnorm_ingredients)
    errors: list[str] = []
    for code, ingredients in records.items():
        if not isinstance(ingredients, list):
            errors.append(f"{code}: value not list")
            continue
        for ing in ingredients:
            if not isinstance(ing, dict):
                errors.append(f"{code}: ingredient not dict")
                continue
            if not ing.get("c"):
                errors.append(f"{code}: ingredient.c empty")
            if not isinstance(ing.get("n"), str):
                errors.append(f"{code}: ingredient.n not string")
            if len(errors) >= 20:
                break
        if len(errors) >= 20:
            break
    assert not errors, "First 20 invariant violations:\n" + "\n".join(errors)
