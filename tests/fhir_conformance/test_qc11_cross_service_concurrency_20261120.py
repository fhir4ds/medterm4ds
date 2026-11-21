"""QC-11 cross-service concurrency (2026-11-20).

New QC domain added after FHIR4-22/FHIR4-23 (2026-11-20): concurrent
native inference (SapBERT search on the DB executor while GLiNER/medspaCy
extract runs on the NER executor) corrupted native heap state in the
fhir4ds sidecar — transient TypeErrors in unrelated pure-Python frames
(~1.5% of requests) and same-process segfaults. The prior concurrency
coverage (TS-75/TS-76) pinned the executor-serialization design for
DB-path endpoints; it never fired the two native-inference executors at
each other, and the QC rotation probed each service domain in isolation.

This domain pins the cross-service seam:
  x10 — concurrent $search (hybrid) + $extract fan-out: every request
        200, zero 5xx (the exact sidecar shape that produced the wild
        9/597 corruption).
  x20 — library-level concurrent hybrid() from caller threads: results
        returned, no exceptions (the in-process adapter contract,
        FHIR4-23).
  x30 — simultaneous identical hybrid searches: byte-identical
        responses (determinism control, TS-76 x30 carried over to the
        search surface).

NOTE (probe power): heap corruption is probabilistic — a clean pass
here is absence-of-symptom evidence, not proof of safety. The
serialization contract itself is pinned structurally in
tests/test_search_thread_safety.py (peak tokenizer/forward concurrency
== 1); this module covers the live surface above it.
"""

from __future__ import annotations

import threading

import pytest

SNOMED_URI = "http://snomed.info/sct"
EXTRACT_TEXT = (
    "Patient with type 2 diabetes mellitus on metformin 500mg twice "
    "daily. Recent HbA1c 8.1%. No evidence of diabetic retinopathy."
)


@pytest.fixture(scope="module")
def qc11_client(tmp_path_factory):
    from starlette.testclient import TestClient

    from medterm4ds.apps.fhir_api import FhirApiSettings, create_fhir_app
    from medterm4ds.services.search import DEFAULT_SEARCH_INDEX_DIR

    from .conftest import _make_conformance_db

    db_path = tmp_path_factory.mktemp("qc11_conc") / "umls.duckdb"
    _make_conformance_db(db_path)
    settings = FhirApiSettings(
        db_path=db_path,
        memory_profile="low",
        search_index_dir=DEFAULT_SEARCH_INDEX_DIR,
        prepare_cache=False,
    )
    app = create_fhir_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        # Warm both native stacks once (model/pipeline load) so the
        # threaded phase measures concurrency, not cold-start.
        r_search = client.get(
            "/fhir/CodeSystem/$search",
            params={"query": "diabetes", "searchMode": "hybrid", "count": 5},
        )
        r_extract = client.post(
            "/fhir/CodeSystem/$extract",
            json={
                "resourceType": "Parameters",
                "parameter": [{"name": "text", "valueString": EXTRACT_TEXT}],
            },
        )
        assert r_search.status_code == 200, r_search.text
        assert r_extract.status_code == 200, r_extract.text
        yield client


class TestCrossExecutorFanOut:
    """x10 — $search and $extract running simultaneously (both native)."""

    def test_x10_search_extract_concurrent_no_5xx(self, qc11_client):
        outcomes: list[tuple[str, int]] = []
        lock = threading.Lock()

        def do_search(i: int):
            r = qc11_client.get(
                "/fhir/CodeSystem/$search",
                params={
                    "query": ["diabetes", "metformin", "myocardial infarction"][i % 3],
                    "searchMode": "hybrid",
                    "count": 10,
                    "system": SNOMED_URI,
                },
            )
            with lock:
                outcomes.append(("search", r.status_code))

        def do_extract():
            r = qc11_client.post(
                "/fhir/CodeSystem/$extract",
                json={
                    "resourceType": "Parameters",
                    "parameter": [
                        {"name": "text", "valueString": EXTRACT_TEXT},
                    ],
                },
            )
            with lock:
                outcomes.append(("extract", r.status_code))

        threads = [threading.Thread(target=do_search, args=(i,)) for i in range(6)]
        threads += [threading.Thread(target=do_extract) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=600)
        assert not any(t.is_alive() for t in threads), "fan-out thread hung"
        assert len(outcomes) == 9
        bad = [(k, s) for k, s in outcomes if s != 200]
        assert not bad, f"non-200 outcomes under concurrency: {bad}"


class TestLibraryLevelConcurrency:
    """x20 — in-process adapter contract: caller threads into hybrid()."""

    def test_x20_threaded_hybrid_no_exceptions(self, qc11_client):
        """The FHIR4-23 embedding shape: the host calls the library from
        its own threads (here exercised alongside the live app so both
        the library singleton and the app's engines coexist)."""
        from medterm4ds.services.search import get_search_service

        service = get_search_service()
        errors: list[str] = []
        lock = threading.Lock()

        def worker():
            for query in ("diabetes", "metformin", "myocardial infarction"):
                try:
                    results = service.hybrid(query, count=10)
                    assert results is not None
                except Exception as exc:  # noqa: BLE001 — recorded, asserted below
                    with lock:
                        errors.append(f"{query}: {exc!r}")

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=600)
        assert not any(t.is_alive() for t in threads), "library thread hung"
        assert not errors, f"hybrid() raised under caller threads: {errors}"


class TestSearchDeterminism:
    """x30 — simultaneous identical hybrid searches: byte-identical."""

    def test_x30_identical_searches_byte_identical(self, qc11_client):
        bodies: list[bytes] = []
        lock = threading.Lock()

        def do_search():
            r = qc11_client.get(
                "/fhir/CodeSystem/$search",
                params={"query": "diabetes", "searchMode": "hybrid", "count": 10},
            )
            with lock:
                bodies.append(r.content)

        threads = [threading.Thread(target=do_search) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=600)
        assert not any(t.is_alive() for t in threads), "search thread hung"
        assert len(bodies) == 6
        assert all(b == bodies[0] for b in bodies), (
            "identical concurrent hybrid searches diverged"
        )
