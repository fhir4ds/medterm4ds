"""TS-75 FHIR /health + settings-boundary contracts (2026-10-22).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261022).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the FHIR app's /health payload semantics (never audited —
TS-49 covered methods, the MCP runtime health was TS-72) and the
FhirApiSettings boundary.

HT1 (LOW, NEW) — /health reports status="ok" with ready=false and no
    diagnostic. The payload is {status, ready} only: no DB identity,
    no version, no degraded-component list (contrast the MCP
    runtime's richer health: database/sources/memory_profile/
    cache_prepared). A monitoring client distinguishing 'starting'
    from 'serving' gets a boolean; distinguishing 'DB missing' from
    'index absent' gets nothing — and status stays the literal "ok"
    whenever the process answers at all. Informational: the field set
    is a design choice; registered so the day /health grows (or the
    docker HEALTHCHECK needs more than ready), the contract decision
    is on record. Pinned ht10/ht11.

HT2 (LOW-MED, NEW) — FhirApiSettings accepts str db_path but the
    lifespan requires Path: a str path to a MISSING database crashes
    startup with a raw AttributeError ('str' object has no attribute
    'exists') — a traceback-class failure at the config boundary,
    where an actionable 'database not found: <path>' belongs (the
    sibling CLI/API paths validate with clear errors). NOTE: a str
    path to an EXISTING db happens to work downstream only where
    duckdb.connect(str) is called directly; every Path-typed consumer
    (.exists(), .parent, / operator) is one str away from the same
    crash. Fix shape: coerce in __post_init__ (Path(db_path)) or
    validate-with-message in the lifespan. Pinned ht20/ht21.

Controls (fresh): /health 200 {status: ok, ready: true} inside the
    lifespan; executor-isolation design note (the /health + /fhir/
    metadata responsiveness under a busy DB executor is architectural
    — pinned as source-level documentation, c10); missing-DB with a
    PATH-typed setting fails with the actionable message (contrast
    for ht20).
"""

from __future__ import annotations

import pathlib

import pytest

pytest.importorskip("fastapi")


class TestHT1HealthPayload:
    """HT1 — {status, ready} only; status literal 'ok'."""

    def test_ht10_payload_shape(self, fhir_client):
        r = fhir_client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == {"status", "ready"}
        assert body["status"] == "ok"
        assert body["ready"] is True

    def test_ht11_no_diagnostic_fields(self, fhir_client):
        """No DB identity / version / degraded-component list — the
        MCP runtime's health carries more. Flip when /health grows
        (or stays by design — this pin records the contract)."""
        r = fhir_client.get("/health")
        body = r.json()
        for absent in ("database", "version", "sources", "degraded"):
            assert absent not in body


class TestHT2SettingsStrPath:
    """HT2 — str db_path to a missing DB: raw AttributeError."""

    def test_ht20_str_path_crashes_unactionable(self, tmp_path):
        """A str path (settings accepts it silently) to a missing DB
        crashes the lifespan with AttributeError at db_path.exists().
        Flip when the settings coerces to Path or the lifespan
        validates with 'database not found: <path>'."""
        from starlette.testclient import TestClient

        from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app

        settings = FhirApiSettings(
            db_path=str(tmp_path / "missing.duckdb"),
            memory_profile="low",
            search_index_dir=str(tmp_path),
            prepare_cache=False,
        )
        assert isinstance(settings.db_path, str)  # premise: accepted
        app = create_fhir_app(settings)
        with pytest.raises(AttributeError, match="exists"):
            with TestClient(app, raise_server_exceptions=True):
                pass

    def test_ht21_path_typed_missing_db_actionable(self, tmp_path):
        """Contrast: a Path-typed missing db fails with the
        actionable not-found message (the intended boundary
        behavior)."""
        from starlette.testclient import TestClient

        from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app

        settings = FhirApiSettings(
            db_path=pathlib.Path(tmp_path) / "missing.duckdb",
            memory_profile="low",
            search_index_dir=str(tmp_path),
            prepare_cache=False,
        )
        app = create_fhir_app(settings)
        with pytest.raises((FileNotFoundError, RuntimeError)) as exc_info:
            with TestClient(app, raise_server_exceptions=True):
                pass
        assert "missing.duckdb" in str(exc_info.value) or (
            "not found" in str(exc_info.value).lower()
        )


class TestTS75Controls:
    """Controls — health lifecycle + design documentation."""

    def test_c10_executor_isolation_documented(self):
        """Source-level control: the lifespan builds SEPARATE db and
        ner executors so a busy $extract cannot block $lookup (and
        neither blocks the event loop — /health and /fhir/metadata
        stay responsive). Pinned so the isolation is not refactored
        away silently."""
        import inspect
        import re

        from medterm4ds.apps import fhir_api

        src = inspect.getsource(fhir_api.create_fhir_app)
        assert re.search(
            r'ThreadPoolExecutor\(max_workers=1, thread_name_prefix="fhir-db"\)',
            src,
        )
        assert re.search(
            r'ThreadPoolExecutor\(max_workers=1, thread_name_prefix="fhir-ner"\)',
            src,
        )
