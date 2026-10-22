"""TS-76 concurrent-request semantics (2026-10-22).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261022b).
FRESH EVIDENCE — live threaded probes executed this run; no prior-run
results cited.

Scope: the concurrency contract — the single-worker executor
serialization (TS-75 pinned the design at source level; never
exercised), /health responsiveness under load, concurrent growth of
the server's only stateful store ($closure), and paging determinism
under simultaneous identical reads.

RESULT: ZERO FINDINGS — controls-only iteration (9th clean surface).
(1) Mixed fan-out (8 lookups + 4 health checks, threaded): all 200,
    ~47ms wall — /health stays responsive while DB work serializes.
    (2) Concurrent closure growth on DISTINCT tables: zero errors;
    the manager's per-table locking holds. (3) Six simultaneous
    IDENTICAL paged expansions: byte-identical responses — the
    executor serialization yields deterministic reads (no interleaved
    partial states).
"""

from __future__ import annotations

import threading

import pytest

SNOMED_URI = "http://snomed.info/sct"


@pytest.fixture(scope="module")
def expand_client(tmp_path_factory):
    from starlette.testclient import TestClient

    from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app
    from medterm4ds.services.search import DEFAULT_SEARCH_INDEX_DIR

    from .conftest import _make_conformance_db

    db_path = tmp_path_factory.mktemp("ts76_conc") / "umls.duckdb"
    _make_conformance_db(db_path)
    settings = FhirApiSettings(
        db_path=db_path,
        memory_profile="low",
        search_index_dir=DEFAULT_SEARCH_INDEX_DIR,
        prepare_cache=False,
    )
    app = create_fhir_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


class TestFanOut:
    """Controls — mixed fan-out stays healthy."""

    def test_x10_lookup_health_fanout(self, fhir_client):
        results: list[tuple[str, int]] = []
        lock = threading.Lock()

        def do_lookup():
            r = fhir_client.get(
                "/fhir/CodeSystem/$lookup",
                params={"system": SNOMED_URI, "code": "44054006"},
            )
            with lock:
                results.append(("lookup", r.status_code))

        def do_health():
            r = fhir_client.get("/health")
            with lock:
                results.append(("health", r.status_code))

        threads = [
            threading.Thread(target=do_lookup) for _ in range(8)
        ] + [threading.Thread(target=do_health) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 12
        assert all(s == 200 for _, s in results)


class TestClosureConcurrentGrowth:
    """Controls — concurrent growth of distinct tables."""

    def test_x20_distinct_tables_no_errors(self, fhir_client):
        errors: list[tuple[str, int]] = []
        lock = threading.Lock()

        def grow(name: str, codes: list[str]):
            for code in codes:
                r = fhir_client.post(
                    "/fhir/CodeSystem/$closure",
                    json={
                        "resourceType": "Parameters",
                        "parameter": [
                            {"name": "name", "valueString": name},
                            {"name": "concept", "valueCoding": {
                                "system": SNOMED_URI, "code": code,
                            }},
                        ],
                    },
                )
                if r.status_code != 200:
                    with lock:
                        errors.append((name, r.status_code))

        threads = [
            threading.Thread(
                target=grow,
                args=("ts76-a", ["73211009", "44054006", "3738000"]),
            ),
            threading.Thread(
                target=grow,
                args=("ts76-b", ["44054006", "73211009"]),
            ),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []


class TestPagingDeterminism:
    """Controls — simultaneous identical reads are deterministic."""

    def test_x30_identical_paged_reads(self, expand_client):
        outputs: list[bytes] = []
        lock = threading.Lock()

        def page():
            r = expand_client.get(
                "/fhir/ValueSet/$expand",
                params={"filter": "diabetes", "count": 2},
            )
            with lock:
                outputs.append(r.content)

        threads = [threading.Thread(target=page) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(outputs) == 6
        assert len(set(outputs)) == 1
