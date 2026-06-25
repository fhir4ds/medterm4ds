"""Tier 1: curated clinical fixture tests.

Each fixture entry is a code with a hand-verified expected patient-friendly
result. Tests call the public service API (get_patient_friendly_names) against
the real DB and assert the result matches the verified expectation.

This catches semantic regressions in the patient-friendly resolver that
structural invariants (Tier 3) and golden-file comparisons (Tier 4) might miss
if the baseline was already blessed with the bug.

To extend: append to tests/regression/fixtures/patient_friendly_verified.jsonl.
Cross-verify each new entry against both the baseline output AND a direct
service call before committing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import pytest

from medterm4ds.core.models import CodeRef
from medterm4ds.engines.duckdb import LocalDuckDBEngine
from medterm4ds.services.patient_friendly import get_patient_friendly_names

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _load_verified_cases() -> list[dict[str, Any]]:
    path = FIXTURES_DIR / "patient_friendly_verified.jsonl"
    cases: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        cases.append(json.loads(line))
    return cases


CASES = _load_verified_cases()


@pytest.fixture(scope="module")
def pf_engine(umls_db_path: Path):
    """One engine per module; prepare_cache may take ~30s on first call."""
    con = duckdb.connect(str(umls_db_path), read_only=True)
    engine = LocalDuckDBEngine(con)
    yield engine
    con.close()


@pytest.mark.realdb
@pytest.mark.slow
@pytest.mark.parametrize(
    "case",
    CASES,
    ids=[f"{c['source']}:{c['code']}" for c in CASES],
)
def test_patient_friendly_pinned(case: dict[str, Any], pf_engine) -> None:
    """Pinned patient-friendly result for a hand-verified code."""
    results = get_patient_friendly_names(
        [CodeRef(case["source"], case["code"])],
        engine=pf_engine,
        max_depth=5,
    )
    assert len(results) == 1
    result = results[0]
    assert result is not None, f"No result returned for {case['source']}:{case['code']}"

    actual = {
        "name": result.name,
        "friendly_source": result.friendly_source,
        "match_type": result.match_type,
    }
    expected = {
        "name": case["expected_name"],
        "friendly_source": case["expected_friendly_source"],
        "match_type": case["expected_match_type"],
    }
    assert actual == expected, (
        f"\n{case['source']}:{case['code']} ({case.get('notes', '')})\n"
        f"  expected: {expected}\n"
        f"  actual:   {actual}"
    )
