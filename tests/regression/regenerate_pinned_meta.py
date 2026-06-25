#!/usr/bin/env python3
"""Regenerate tests/regression/fixtures/pinned_meta.json from the current baseline.

Reads each deliverable in reports/fhir4px/, canonicalizes it using the same
golden/normalize.py the regression tests use, and records:
  - record count
  - SHA256 of the canonical JSON serialization (sorted keys, no whitespace)
  - UMLS release tag (resolved from the DB symlink)

Run this after intentionally rebuilding the baseline. The resulting file is the
audit trail: if the baseline on disk drifts without this file being updated,
the regression tests fail loudly.

Usage:
    PYTHONPATH=tests python3 tests/regression/regenerate_pinned_meta.py
    MEDTERM4DS_REGRESSION_DB=/path/umls.duckdb \\
    MEDTERM4DS_FHIR4PX_BASELINE=/path/reports/fhir4px \\
    PYTHONPATH=tests python3 tests/regression/regenerate_pinned_meta.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tests"))

from regression.golden.normalize import (  # noqa: E402
    EMBEDDING_CATEGORIES,
    PATIENT_FRIENDLY_SOURCES,
    load_canonical,
)

DEFAULT_DB = "/mnt/d/medterm4ds/data/umls_current.duckdb"
DEFAULT_BASELINE = "/mnt/d/medterm4ds/reports/fhir4px"
OUTPUT_PATH = REPO_ROOT / "tests" / "regression" / "fixtures" / "pinned_meta.json"

_RELEASE_RE = re.compile(r"umls_([0-9]{4}[A-B]{2})\.duckdb$", re.IGNORECASE)


def _resolve_release(db_path: Path) -> str | None:
    candidate = db_path
    try:
        if candidate.is_symlink():
            candidate = Path(os.readlink(candidate))
            if not candidate.is_absolute():
                candidate = (db_path.parent / candidate).resolve()
    except OSError:
        return None
    match = _RELEASE_RE.search(str(candidate))
    return match.group(1).upper() if match else None


def _to_hashable(obj: object) -> object:
    """Convert a canonical record set to a JSON-serializable form.

    Canonical forms may use tuple keys (e.g. patient_friendly CSV uses
    (source, code)). JSON doesn't accept tuple keys, so we serialize as a
    sorted list of [key, value] pairs. Tuples become JSON arrays; sorting
    by string repr ensures determinism.
    """
    if isinstance(obj, dict):
        items = [(_to_hashable_key(k), _to_hashable(v)) for k, v in obj.items()]
        items.sort(key=lambda kv: repr(kv[0]))
        return items
    if isinstance(obj, list):
        return [_to_hashable(v) for v in obj]
    if isinstance(obj, tuple):
        return [_to_hashable(v) for v in obj]
    return obj


def _to_hashable_key(key: object) -> object:
    if isinstance(key, tuple):
        return [_to_hashable(v) for v in key]
    return key


def _canonical_sha256(canonical: object) -> str:
    payload = json.dumps(_to_hashable(canonical), separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _entry(path: Path) -> dict:
    canonical = load_canonical(path)
    return {"count": len(canonical), "sha256": _canonical_sha256(canonical)}


def main() -> int:
    db_path = Path(os.getenv("MEDTERM4DS_REGRESSION_DB", DEFAULT_DB))
    baseline_dir = Path(os.getenv("MEDTERM4DS_FHIR4PX_BASELINE", DEFAULT_BASELINE))

    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 1
    if not baseline_dir.is_dir():
        print(f"ERROR: baseline not found at {baseline_dir}", file=sys.stderr)
        return 1

    release = _resolve_release(db_path)
    if release is None:
        print(f"ERROR: could not resolve UMLS release from {db_path}", file=sys.stderr)
        return 1

    print(f"Canonicalizing baseline at {baseline_dir} (UMLS release: {release})...")

    patient_friendly = {}
    for src in PATIENT_FRIENDLY_SOURCES:
        path = baseline_dir / f"patient_friendly_{src}.json"
        if path.exists():
            patient_friendly[src] = _entry(path)
            print(f"  patient_friendly_{src}.json: {patient_friendly[src]['count']:,} records")

    csv_path = baseline_dir / "patient_friendly_names.csv"
    csv_entry = _entry(csv_path) if csv_path.exists() else None
    if csv_entry:
        print(f"  patient_friendly_names.csv: {csv_entry['count']:,} records")

    embedding = {}
    for cat in EMBEDDING_CATEGORIES:
        path = baseline_dir / f"embedding_index_{cat}.jsonl"
        if path.exists():
            embedding[cat] = _entry(path)
            print(f"  embedding_index_{cat}.jsonl: {embedding[cat]['count']:,} records")

    assoc_path = baseline_dir / "condition_associations.json"
    associations = _entry(assoc_path) if assoc_path.exists() else None
    if associations:
        print(f"  condition_associations.json: {associations['count']:,} conditions")

    rxnorm_path = baseline_dir / "rxnorm-ingredients.json"
    rxnorm = _entry(rxnorm_path) if rxnorm_path.exists() else None
    if rxnorm:
        print(f"  rxnorm-ingredients.json: {rxnorm['count']:,} products")

    pinned = {
        "umls_release": release,
        "patient_friendly": patient_friendly,
        "patient_friendly_csv": csv_entry,
        "embedding_index": embedding,
        "associations": associations,
        "rxnorm_ingredients": rxnorm,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(pinned, indent=2) + "\n")
    print(f"\nWrote {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
