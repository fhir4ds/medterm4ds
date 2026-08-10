# FHIR R4 Terminology Service — Performance Benchmarks

**Generated**: 2026-08-10T19:56:04+00:00 (UTC)
**Iterations per probe**: 25 (>= 20 per USER_DIRECTIVES contract)
**Fixture DB**: tests/fhir_conformance/conftest.py (4 codes, 1 mrrel row)

## Methodology

- Each probe runs `warmup=3` throwaway iterations to populate caches,
  then `iterations` measured iterations using `time.perf_counter()`.
- The **first** request after engine load is recorded separately as
  the cold-start reading (engine init + prepared-statement compilation).
- Percentiles use the nearest-rank method (deterministic for n>=20).
- The fixture DB has 4 codes and 1 mrrel row, so absolute timings are
  sub-millisecond; the **relative overhead** between operations and the
  **cold-vs-warm differential** are the load-bearing signals.
- Probe **flagged** if warm p95 exceeds 500ms (release-notes candidate).

## Cold-Start vs Warm Differential

Full engine load measured by constructing a fresh TestClient (DuckDB
connection, schema inspection, prepared-statement cache), then issuing
the first $lookup:

| Stage | Time (ms) |
|---|---|
| Client construction (engine load) | 10657.50 |
| First $lookup (cold) | 26.97 |
| Second $lookup (warm) | 6.32 |

- **Cold/warm ratio**: 4.26x
- First-request HTTP status: 200

## Per-Probe Results

All times in milliseconds.

| Probe | Cold (first) | Warm p50 | Warm p95 | Warm p99 | Warm mean | min | max | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| lookup_cs_valid | 6.72 | 2.293 | 5.160 | 5.594 | 2.660 | 2.117 | 5.594 | 200 |
| validate_cs_valid | 3.62 | 2.642 | 4.701 | 5.795 | 3.000 | 2.018 | 5.795 | 200 |
| subsumes_parent_child | 17.56 | 5.135 | 10.430 | 10.630 | 5.965 | 3.909 | 10.630 | 200 |
| expand_filter | 5.66 | 2.334 | 5.243 | 5.854 | 2.706 | 1.989 | 5.854 | 200 |
| expand_intensional | 9.69 | 8.896 | 21.614 | 24.160 | 10.765 | 7.865 | 24.160 | 200 |
| expand_explicit_list | 4.59 | 0.882 | 1.192 | 1.213 | 0.935 | 0.775 | 1.213 | 200 |
| validate_vs | 6.69 | 2.251 | 4.228 | 4.858 | 2.455 | 1.999 | 4.858 | 200 |
| translate_snomed_icd10 | 10.28 | 3.525 | 8.162 | 8.676 | 4.113 | 3.070 | 8.676 | 200 |
| closure_init | 10.93 | 8.370 | 19.314 | 31.111 | 10.360 | 7.065 | 31.111 | 200 |
| batch_endpoint | 25.89 | 20.447 | 30.103 | 32.859 | 21.540 | 15.993 | 32.859 | 200 |
| serialization_json | 2.38 | 2.511 | 5.412 | 5.466 | 3.256 | 2.039 | 5.466 | 200 |
| serialization_xml | 2.63 | 2.845 | 5.363 | 6.004 | 3.275 | 2.244 | 6.004 | 200 |

## Batch Endpoint Overhead

Batch size: 10 $lookup entries per Bundle.

| Metric | Time (ms) |
|---|---:|
| Standalone $lookup warm p50 (baseline) | 2.628 |
| Batch total warm p50 (10 entries) | 20.447 |
| Batch per-entry p50 | 2.045 |
| Batch per-entry p95 | 3.010 |
| **Per-entry overhead vs standalone** | **-0.584** |

## Serialization Overhead (XML vs JSON)

Measured on CodeSystem $lookup (same valid SNOMED code).

| Format | Warm p50 (ms) | Warm p95 (ms) |
|---|---:|---:|
| JSON | 2.511 | 5.412 |
| XML | 2.845 | 5.363 |

- **XML/JSON p50 ratio**: 1.13x
- **XML overhead**: +0.334 ms

## Probes Flagged (p95 > 500 ms)

_(none — no probe exceeded 500 ms p95)_

## Environment

- Python: 3.13.13
- Platform: Linux (WSL2)
- memory_profile: low (conformance-test config)
- prepare_cache: false (no patient_friendly JSON preloaded)
- search_index_dir: empty (BM25 not loaded; $expand?filter uses
  fallback path)

## Release-Notes Candidates

_(none surfaced from this run — see `Probes Flagged` above)_

## Caveats

1. **Absolute timings are fixture-bound.** 4 mrconso rows + 1 mrrel row
   make most ops return in microseconds; production UMLS (56 GB) will
   be 3-5 orders of magnitude slower. The relative ordering and cold/warm
   differential are the transferable signals.
2. **TestClient measurement overhead.** Starlette's TestClient uses
   httpx over an in-process ASGI transport; the absolute latency
   includes that overhead. Production ASGI server (uvicorn) would
   remove ~1-2 ms per request.
3. **XML serialization.** medterm4ds's XML serializer is a minimal
   hand-rolled builder (engines/fhir/xml.py); production XML tooling
   (lxml) would change this profile.
4. **$closure** maintains in-process state. The probe uses unique
   closure names per iteration to avoid cross-iteration interference;
   this measures the add-concepts work (2 BFS walks per source).
