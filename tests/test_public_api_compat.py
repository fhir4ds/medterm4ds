from __future__ import annotations

import re
from pathlib import Path

import medterm4ds as mt


def test_version_matches_pyproject():
    """QA-002: the two version sync points must agree with each other.

    A hardcoded literal here went stale at the 0.0.3 bump (the release
    commit updated pyproject + __init__ but not this test, so the shipped
    tag failed its own suite). importlib.metadata is NOT a viable source:
    editable dev installs carry a dist-info frozen at install time.
    tomllib is 3.11+ and we support 3.10, so parse the one line we need.
    """
    pyproject = Path(mt.__file__).resolve().parents[2] / "pyproject.toml"
    match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(), re.M)
    assert match is not None, f"no version line found in {pyproject}"
    assert mt.__version__ == match.group(1)


def test_readme_release_tag_matches_default():
    """RUN-2/EV-09: README's "default release tag, `X` today" must equal
    DEFAULT_TERMINOLOGY_RELEASE. The README tag went stale at the
    2026.11.12.2 bump (docs said 2026.11.12.1-era tags), so pin the docs
    claim to the code's single source of truth.
    """
    from medterm4ds.core.terminology import DEFAULT_TERMINOLOGY_RELEASE

    readme = Path(mt.__file__).resolve().parents[2] / "README.md"
    match = re.search(
        r"the default release tag, `([^`]+)` today", readme.read_text()
    )
    assert match is not None, (
        f"no 'default release tag, `X` today' line found in {readme}"
    )
    assert match.group(1) == DEFAULT_TERMINOLOGY_RELEASE, (
        f"README says default release tag {match.group(1)!r} but "
        f"DEFAULT_TERMINOLOGY_RELEASE is {DEFAULT_TERMINOLOGY_RELEASE!r}"
    )


def test_local_duckdb_compatibility_aliases():
    from medterm4ds import (
        LOCAL_DUCKDB_MEMORY_PROFILES,
        LOCAL_LITE_MEMORY_PROFILES,
        LocalDuckDBConfig,
        LocalDuckDBEngine,
        LocalLiteConfig,
        LocalLiteEngine,
        local_duckdb_config,
        local_lite_config,
    )

    assert LocalLiteEngine is LocalDuckDBEngine
    assert LocalLiteConfig is LocalDuckDBConfig
    assert LOCAL_LITE_MEMORY_PROFILES is LOCAL_DUCKDB_MEMORY_PROFILES
    assert local_lite_config is local_duckdb_config
