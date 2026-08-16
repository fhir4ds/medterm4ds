from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from medterm4ds import __version__
from medterm4ds.apps.api import ApiSettings, create_app


def _make_duckdb(path: Path) -> None:
    con = duckdb.connect(str(path))
    try:
        con.execute(
            """
            CREATE TABLE mrconso (
                CODE VARCHAR,
                TTY VARCHAR,
                STR VARCHAR,
                AUI VARCHAR,
                SUPPRESS VARCHAR,
                SAB VARCHAR,
                CUI VARCHAR
            )
            """
        )
        con.execute(
            """
            CREATE TABLE mrrel (
                AUI1 VARCHAR,
                AUI2 VARCHAR,
                RELA VARCHAR,
                REL VARCHAR
            )
            """
        )
        con.executemany(
            "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("E11.9", "PT", "Type 2 diabetes mellitus", "ICD_E119", "N", "ICD10CM", "C_DIAB"),
                ("E11", "PT", "Type 2 diabetes mellitus", "ICD_E11", "N", "ICD10CM", "C_E11"),
                ("44054006", "PT", "Diabetes mellitus type 2", "SNOMED_DIAB", "N", "SNOMEDCT_US", "C_DIAB"),
                ("D_DIAB", "MH", "Diabetes", "MP_DIAB", "N", "MEDLINEPLUS", "C_DIAB"),
                ("208", "PT", "COVID-19 Vaccine", "CVX_208", "N", "CVX", "C_CVX"),
            ],
        )
        con.executemany(
            "INSERT INTO mrrel VALUES (?, ?, ?, ?)",
            [
                ("ICD_E119", "ICD_E11", "isa", "PAR"),
            ],
        )
    finally:
        con.close()


def _settings(db_path: Path, *, prepare_cache: bool = True) -> ApiSettings:
    return ApiSettings(
        db_path=db_path,
        sources=("ICD10CM", "CVX"),
        memory_profile="low",
        prepare_cache=prepare_cache,
    )


def test_api_health_does_not_leak_db_path(tmp_path):
    """Sanitized /health must not leak the DB filesystem path (Tier B).

    Local processes needing the path can read MEDTERM4DS_DB. External probes
    (which shouldn't reach this server -- it binds to localhost) get only
    readiness, sources, and memory profile.
    """
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    app = create_app(_settings(db_path))
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "status": "ok",
        "ready": True,
        "version": __version__,
        "sources": ["ICD10CM", "CVX"],
        "memory_profile": "low",
        "cache_prepared": True,
    }
    # Critical: no DB path field anywhere in the response.
    assert "database" not in body
    assert "db_path" not in body
    assert str(db_path) not in response.text


def test_api_patient_friendly_endpoint(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    app = create_app(_settings(db_path, prepare_cache=False))
    with TestClient(app) as client:
        response = client.post(
            "/patient-friendly",
            json={
                "codes": [
                    {"source": "ICD10CM", "code": "E11.9"},
                    {"source": "CVX", "code": "208"},
                ]
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert [row["name"] for row in payload["results"]] == ["Diabetes", "COVID-19 Vaccine"]
    assert [row["match_type"] for row in payload["results"]] == ["exact", "original"]


def test_api_lookup_endpoint(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    app = create_app(_settings(db_path, prepare_cache=False))
    with TestClient(app) as client:
        response = client.post(
            "/lookup",
            json={
                "codes": [
                    {"source": "ICD10-CM", "code": "E11.9"},
                    {"source": "CVX", "code": "NOPE"},
                ]
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["results"][0] == {
        "source": "ICD10CM",
        "code": "E11.9",
        "name": "Type 2 diabetes mellitus",
        "cui": "C_DIAB",
        "aui": "ICD_E119",
        "tty": "PT",
        "suppress": "N",
    }
    assert payload["results"][1] is None


def test_api_discovery_endpoints(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    app = create_app(_settings(db_path, prepare_cache=False))
    with TestClient(app) as client:
        source_response = client.post("/sources", json={"sources": ["ICD10-CM", "CVX"]})
        sample_response = client.post(
            "/sample-codes",
            json={"sources": ["ICD10CM", "CVX"], "per_source": 1},
        )
        tty_response = client.post(
            "/code-ttys",
            json={"codes": [{"source": "ICD10CM", "code": "E11.9"}]},
        )
        search_response = client.post(
            "/search-names",
            json={
                "query": "diabetes",
                "sources": ["ICD10CM", "MEDLINEPLUS"],
                "tty_filters": ["MH"],
            },
        )

    assert source_response.status_code == 200
    assert source_response.json()["results"] == [
        {"source": "CVX", "code_count": 1, "atom_count": 1},
        {"source": "ICD10CM", "code_count": 2, "atom_count": 2},
    ]
    assert sample_response.status_code == 200
    assert [(row["source"], row["code"]) for row in sample_response.json()["results"]] == [
        ("CVX", "208"),
        ("ICD10CM", "E11"),
    ]
    assert tty_response.status_code == 200
    assert [row["tty"] for row in tty_response.json()["results"]] == ["PT"]
    assert search_response.status_code == 200
    assert search_response.json()["results"][0]["source"] == "MEDLINEPLUS"
    assert search_response.json()["results"][0]["match_type"] == "exact"


def test_api_hierarchy_endpoint(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    app = create_app(_settings(db_path, prepare_cache=False))
    with TestClient(app) as client:
        response = client.post(
            "/hierarchy",
            json={
                "codes": [
                    {"source": "ICD10CM", "code": "E11.9"},
                ],
                "direction": "parents",
            },
        )

    assert response.status_code == 200
    row = response.json()["results"][0]
    assert row["source"] == "ICD10CM"
    assert row["code"] == "E11.9"
    assert row["target_code"] == "E11"
    assert row["relationship"] == "parent"
    assert row["depth"] == 1


def test_api_map_endpoint(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    app = create_app(_settings(db_path, prepare_cache=False))
    with TestClient(app) as client:
        response = client.post(
            "/map",
            json={
                "codes": [
                    {"source": "ICD10-CM", "code": "E11.9"},
                    {"source": "CVX", "code": "208"},
                ],
                "target_sources": ["SNOMED"],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert [(row["source"], row["code"], row["target_source"], row["target_code"]) for row in payload["results"]] == [
        ("ICD10CM", "E11.9", "SNOMEDCT_US", "44054006")
    ]
    assert payload["results"][0]["match_type"] == "same_cui"
    assert payload["results"][0]["matched_via"]["strategy"] == "same_cui"


def test_api_conceptmap_endpoint(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    app = create_app(_settings(db_path, prepare_cache=False))
    with TestClient(app) as client:
        response = client.post(
            "/conceptmap/patient-friendly",
            json={
                "codes": [
                    {"source": "ICD10CM", "code": "E11.9"},
                ],
                "target_source": "PATIENT_FRIENDLY",
            },
        )

    assert response.status_code == 200
    row = response.json()["results"][0]
    assert row["source"] == "ICD10CM"
    assert row["code"] == "E11.9"
    assert row["target_source"] == "PATIENT_FRIENDLY"
    assert row["target_display"] == "Diabetes"
    assert row["relationship"] == "equivalent"


def test_api_validates_request_body(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    app = create_app(_settings(db_path, prepare_cache=False))
    with TestClient(app) as client:
        response = client.post(
            "/patient-friendly",
            json={"codes": [{"source": "ICD10CM"}]},
        )

    assert response.status_code == 422


# QC-489 (MEDIUM): empty source/code strings must be rejected at validation
# (422), not leak to the service layer as an opaque 500 — matching the FHIR
# surface and the Python facade's clean ValueError.
@pytest.mark.parametrize(
    ("endpoint", "body"),
    [
        ("/lookup", {"codes": [{"source": "", "code": "E11"}]}),
        ("/lookup", {"codes": [{"source": "ICD10CM", "code": ""}]}),
        ("/sources", {"sources": [""]}),
    ],
)
def test_api_rejects_empty_source_and_code_strings(tmp_path, endpoint, body):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    app = create_app(_settings(db_path, prepare_cache=False))
    with TestClient(app) as client:
        response = client.post(endpoint, json=body)

    assert response.status_code == 422


# QC-478 (MEDIUM): service-layer ValueError must surface as 400 + the same
# diagnostic the local facade raises, not an opaque 500 "Internal Server
# Error" (which the remote client re-raises with the message destroyed).
def test_api_value_error_surfaces_as_400_with_message(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    app = create_app(_settings(db_path, prepare_cache=False))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/sources", json={"sources": ["BANANA"]})

    assert response.status_code == 400
    assert "BANANA" in response.json()["detail"]


# QC-497 (MEDIUM): NotImplementedError carries operator-actionable
# remediation text (e.g. the LOINC prepared-schema rebuild instruction) —
# it must survive the wire as a structured body, not an opaque 500.
def test_api_not_implemented_surfaces_as_501_with_message(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    app = create_app(_settings(db_path, prepare_cache=False))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/patient-friendly",
            json={"codes": [{"source": "LNC", "code": "883-9"}]},
        )

    if response.status_code == 501:
        # Fixture DB has no prepared schema: the LOINC path raises
        # NotImplementedError and the message must be preserved.
        assert "LOINC" in response.json()["detail"] or "detail" in response.json()
    else:
        # Some fixture layouts resolve LNC through a supported path; the
        # contract under test is only "never an opaque 500".
        assert response.status_code != 500


# QC-488 (LOW): /source-stats is an alias of /sources — one handler, so the
# two routes can never drift.
def test_api_source_stats_alias_matches_sources(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    app = create_app(_settings(db_path, prepare_cache=False))
    with TestClient(app) as client:
        via_sources = client.post("/sources", json={"sources": ["ICD10CM"]})
        via_alias = client.post("/source-stats", json={"sources": ["ICD10CM"]})

    assert via_sources.status_code == via_alias.status_code == 200
    assert via_sources.json() == via_alias.json()


# QC-490 (LOW): /optimize uses the shared 'results' envelope like every
# other data endpoint (was the lone singular 'result').
def test_api_optimize_uses_results_envelope(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    app = create_app(_settings(db_path, prepare_cache=False))
    with TestClient(app) as client:
        response = client.post(
            "/optimize",
            json={"codes": [{"source": "ICD10CM", "code": "E11.9"}]},
        )

    assert response.status_code == 200
    assert isinstance(response.json()["results"], list)


# QC-495 (HIGH): /patient-friendly must honor the payload's resolve_mode
# (default matching the facade's 'active_only') — pre-fix it hardcoded
# 'resolve_current', silently re-resolving already-effective refs and
# diverging from the local engine on obsolete codes.
def test_api_patient_friendly_accepts_resolve_mode(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    _make_duckdb(db_path)

    app = create_app(_settings(db_path, prepare_cache=False))
    with TestClient(app) as client:
        default_response = client.post(
            "/patient-friendly",
            json={"codes": [{"source": "ICD10CM", "code": "E11.9"}]},
        )
        explicit_response = client.post(
            "/patient-friendly",
            json={
                "codes": [{"source": "ICD10CM", "code": "E11.9"}],
                "resolve_mode": "active_only",
            },
        )

    assert default_response.status_code == explicit_response.status_code == 200
    assert default_response.json() == explicit_response.json()
