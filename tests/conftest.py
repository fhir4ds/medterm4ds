from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("MEDTERM4DS_DISABLE_CVX_GROUPS", "1")

DEFAULT_REGRESSION_DB = "/mnt/d/medterm4ds/data/umls_current.duckdb"
DEFAULT_FHIR4PX_BASELINE = "/mnt/d/medterm4ds/reports/fhir4px"

_RELEASE_RE = re.compile(r"umls_([0-9]{4}[A-B]{2})\.duckdb$", re.IGNORECASE)


def _resolve_umls_release(db_path: Path) -> str | None:
    """Extract the UMLS release tag (e.g. '2026AA') from the DB path or symlink target."""
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


@pytest.fixture(scope="session")
def umls_db_path() -> Path:
    """Path to the regression DuckDB. Skips if missing."""
    path = Path(os.getenv("MEDTERM4DS_REGRESSION_DB", DEFAULT_REGRESSION_DB))
    if not path.exists():
        pytest.skip(f"Regression DB not found at {path} (set MEDTERM4DS_REGRESSION_DB)")
    return path


@pytest.fixture(scope="session")
def fhir4px_baseline_dir() -> Path:
    """Path to the canonical fhir4px baseline outputs. Skips if missing."""
    path = Path(os.getenv("MEDTERM4DS_FHIR4PX_BASELINE", DEFAULT_FHIR4PX_BASELINE))
    if not path.is_dir():
        pytest.skip(f"fhir4px baseline not found at {path} (set MEDTERM4DS_FHIR4PX_BASELINE)")
    return path


@pytest.fixture(scope="session")
def umls_release_tag(umls_db_path: Path) -> str:
    """UMLS release tag (e.g. '2026AA') resolved from the DB symlink."""
    tag = _resolve_umls_release(umls_db_path)
    if tag is None:
        pytest.skip(f"Could not resolve UMLS release tag from {umls_db_path}")
    return tag


@pytest.fixture(autouse=True)
def _reset_closure_singleton():
    """Reset the ClosureManager module-level singleton before each test.

    Closure tests across test_fhir_conformance.py, test_fhir_api.py, and
    fhir_conformance/test_runner.py all share get_closure_manager(); without
    a reset, registrations from one test leak into the next, causing
    order-dependent failures.
    """
    from medterm4ds.engines.fhir import closure
    closure._manager = None
    yield
    closure._manager = None
