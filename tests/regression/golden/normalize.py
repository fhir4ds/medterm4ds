"""Per-deliverable canonicalization for golden-file comparison.

Each deliverable type has different non-determinism sources (timestamps, list
order, line order). These loaders read a file, strip volatile fields, sort
unordered collections, and return a dict keyed by the deliverable's primary
key so two builds can be compared regardless of insertion order.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

PATIENT_FRIENDLY_SOURCES = (
    "icd10cm",
    "icd10pcs",
    "snomedct_us",
    "rxnorm",
    "lnc",
    "cpt",
    "hcpcs",
    "cvx",
)
EMBEDDING_CATEGORIES = (
    "condition",
    "lab",
    "medication",
    "procedure",
    "vaccine",
    "body_structure",
)


def canonical_patient_friendly_json(path: Path) -> dict[str, dict[str, Any]]:
    """Load patient_friendly_<src>.json -> {code: {name, friendly_source, match_type, cui}} sorted."""
    with path.open() as f:
        raw = json.load(f)
    return {str(k): raw[k] for k in sorted(raw)}


def canonical_patient_friendly_csv(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Load patient_friendly_names.csv -> {(source, code): row} sorted by (source, code)."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            key = (row["source"], row["code"])
            out[key] = {k: v for k, v in row.items() if k not in ("source", "code")}
    return dict(sorted(out.items()))


def canonical_embedding_jsonl(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Load embedding_index_<cat>.jsonl -> {(code.source, code.code): record}.

    Sorts volatile list fields (vectors.synonyms, semantic_types) so re-orderings
    don't trigger false diffs.
    """
    out: dict[tuple[str, str], dict[str, Any]] = {}
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            code = rec["code"]
            key = (code["source"], code["code"])
            rec = _normalize_embedding_record(rec)
            out[key] = rec
    return dict(sorted(out.items()))


def _normalize_embedding_record(rec: dict[str, Any]) -> dict[str, Any]:
    rec = dict(rec)
    vectors = rec.get("vectors")
    if isinstance(vectors, dict):
        vectors = dict(vectors)
        synonyms = vectors.get("synonyms")
        if isinstance(synonyms, list):
            vectors["synonyms"] = sorted(synonyms)
        rec["vectors"] = vectors
    semantic_types = rec.get("semantic_types")
    if isinstance(semantic_types, list):
        rec["semantic_types"] = sorted(semantic_types)
    ingredient_codes = rec.get("ingredient_codes")
    if isinstance(ingredient_codes, list):
        rec["ingredient_codes"] = sorted(ingredient_codes)
    return rec


def canonical_associations(path: Path) -> dict[str, dict[str, list]]:
    """Load condition_associations.json -> {condition_code: {labs, medications}}.

    Strips `_meta` (contains `generated_at`). Sorts medication lists by
    (code, relationship, depth) and lab lists by code so re-orderings don't
    trigger false diffs.
    """
    with path.open() as f:
        raw = json.load(f)
    out: dict[str, dict[str, list]] = {}
    for key, value in raw.items():
        if key == "_meta":
            continue
        normalized = {
            "labs": sorted(value.get("labs", []), key=lambda e: e.get("code", "")),
            "medications": sorted(
                value.get("medications", []),
                key=lambda e: (e.get("code", ""), e.get("relationship", ""), e.get("depth", 0)),
            ),
        }
        out[str(key)] = normalized
    return dict(sorted(out.items()))


def canonical_rxnorm_ingredients(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Load rxnorm-ingredients.json -> {rxnorm_code: [{c, n}, ...]} with sorted ingredients.

    Strips `_meta`. Sorts each ingredient list by `c`.
    """
    with path.open() as f:
        raw = json.load(f)
    out: dict[str, list[dict[str, Any]]] = {}
    for key, value in raw.items():
        if key == "_meta":
            continue
        ingredients = sorted(
            (dict(ing) for ing in value),
            key=lambda e: e.get("c", ""),
        )
        out[str(key)] = ingredients
    return dict(sorted(out.items()))


KIND_PATIENT_JSON = "patient_json"
KIND_PATIENT_CSV = "patient_csv"
KIND_EMBEDDING = "embedding"
KIND_ASSOCIATIONS = "associations"
KIND_RXNORM_INGREDIENTS = "rxnorm_ingredients"


def detect_kind(path: Path) -> str:
    """Infer the deliverable kind from a filename."""
    name = path.name
    if name == "patient_friendly_names.csv":
        return KIND_PATIENT_CSV
    if name.startswith("patient_friendly_") and name.endswith(".json"):
        return KIND_PATIENT_JSON
    if name.startswith("embedding_index_") and name.endswith(".jsonl"):
        return KIND_EMBEDDING
    if name == "condition_associations.json":
        return KIND_ASSOCIATIONS
    if name == "rxnorm-ingredients.json":
        return KIND_RXNORM_INGREDIENTS
    raise ValueError(f"Unknown deliverable file pattern: {path}")


def load_canonical(path: Path) -> dict[Any, Any]:
    """Load and canonicalize a deliverable file. Raises ValueError on unknown pattern."""
    kind = detect_kind(path)
    dispatch = {
        KIND_PATIENT_JSON: canonical_patient_friendly_json,
        KIND_PATIENT_CSV: canonical_patient_friendly_csv,
        KIND_EMBEDDING: canonical_embedding_jsonl,
        KIND_ASSOCIATIONS: canonical_associations,
        KIND_RXNORM_INGREDIENTS: canonical_rxnorm_ingredients,
    }
    return dispatch[kind](path)
