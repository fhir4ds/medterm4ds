# FHIR R4 Conformance Test Suite — Repeatable Process

## Context

The FHIR R4 terminology facade is implemented (7 operations, 365 tests). The existing
`tests/test_fhir_conformance.py` validates response builders against the FHIR schema via
`fhir.resources`. But a true "Touchstone-level" process needs to:

1. Exercise each operation via HTTP against a running server (not just builders)
2. Validate responses against the full FHIR R4 spec (not just pydantic schema)
3. Cover edge cases and error paths systematically
4. Be repeatable via a single command (`make fhir-conformance`)
5. Work in CI (Docker-based, no manual steps)

The HL7 Touchstone service (touchstone.aegis.net) requires a publicly-accessible endpoint.
Our facade is localhost-only. Instead of tunneling, we build a **local conformance harness**
that uses the HAPI FHIR validator (Java/Docker) for spec-level validation and a structured
test script for operation-level testing.

## Architecture

```
make fhir-conformance
  │
  ├─ scripts/run_fhir_conformance.py
  │    │
  │    ├─ 1. Start FHIR server (localhost:8001) or use TestClient
  │    ├─ 2. Load test cases from tests/fhir_conformance/cases.json
  │    ├─ 3. For each case:
  │    │    a. Send HTTP request (method, URL, params/body)
  │    │    b. Capture response
  │    │    c. Validate schema via fhir.resources
  │    │    d. Validate content (expected fields/values)
  │    │    e. Validate via HAPI FHIR validator (Docker, optional)
  │    └─ 4. Report: X passed, Y failed (with details)
  │
  └─ tests/fhir_conformance/
       ├── cases.json           # Test case definitions (declarative)
       ├── test_runner.py        # pytest-based runner for CI
       └── README.md             # How to run + extend
```

## Test case format

Declarative JSON so non-developers can add cases:

```json
{
  "id": "lookup-snomed-valid",
  "description": "Lookup a known SNOMED code",
  "method": "GET",
  "path": "/fhir/CodeSystem/$lookup",
  "params": {"system": "http://snomed.info/sct", "code": "44054006"},
  "expected_status": 200,
  "expected_resource_type": "Parameters",
  "expected_fields": [
    {"name": "display", "contains": "diabetes"},
    {"name": "system", "equals": "http://snomed.info/sct"}
  ],
  "validate_schema": true
}
```

Each test case specifies:
- HTTP method + path + params/body
- Expected status code
- Expected resourceType
- Expected field values (exact match, contains, or presence check)
- Whether to run full HAPI validator (slower, optional)

## Test case inventory (~40 cases)

### $lookup (6 cases)
- Valid SNOMED code → Parameters with display + system + code
- Valid RxNorm code → Parameters with tty property
- Invalid code → OperationOutcome
- Unknown system → OperationOutcome 400
- Missing system param → OperationOutcome 400
- POST with Coding body → same as GET

### $validate-code (5 cases)
- Valid code → result=true
- Invalid code → result=false
- Missing code param → 400
- Unknown system → 400
- POST variant

### $translate (5 cases)
- SNOMED → ICD-10 → result=true with match
- SNOMED → all targets → result=true with matches
- No mapping available → result=false
- Unknown source system → 400
- Missing params → 400

### $subsumes (5 cases)
- Parent subsumes child → "subsumes"
- Child subsumed by parent → "subsumed-by"
- Identical codes → "equivalent"
- Unrelated codes → "not-subsumed"
- Unknown system → 400

### $expand (8 cases)
- Filter text "diabetes" → ValueSet with matches
- Filter with system restriction → ValueSet scoped to one system
- Intensional is-a → ValueSet with descendants
- Intensional descendant-of → ValueSet without root
- Explicit concept list → ValueSet with listed codes
- compose.exclude → excluded codes removed
- fhir_vs URL pattern → ValueSet expansion
- Missing filter → 400

### $closure (4 cases)
- Initialize empty → version hash + 0 concepts
- Add concepts → version hash changes + concepts listed
- Missing name → 400
- Add + verify subsumption via internal check

### $search (4 cases)
- Lexical query → Bundle (if BM25 available, else skip)
- Semantic query → Bundle (if model available, else skip)
- Hybrid query → Bundle (if both available, else skip)
- Unsupported mode → 503

### CapabilityStatement + metadata (3 cases)
- /fhir/metadata → valid CapabilityStatement
- All 7 operations advertised
- fhirVersion = 4.0.1

## HAPI FHIR validator integration (optional, Docker)

The HAPI FHIR validator (`hapiproject/validator-api`) provides deeper validation than
pydantic-based `fhir.resources`. It checks:
- FHIR R4 invariants (not just schema)
- Profile conformance (if profiles are specified)
- Terminology binding validation

Integrated via Docker:
```bash
docker run --rm -v /path/to/response.json:/tmp/resource.json \
  hapiproject/validator:latest \
  /tmp/resource.json -version 4.0.1
```

Wrapped in the conformance script as an optional `--deep-validate` flag. Default: schema
validation only (fast). With `--deep-validate`: runs HAPI validator (slower, catches more).

## Files to create

- `tests/fhir_conformance/__init__.py`
- `tests/fhir_conformance/cases.json` — declarative test case definitions (~40 cases)
- `tests/fhir_conformance/test_runner.py` — pytest parametrized runner that loads cases.json
- `tests/fhir_conformance/conftest.py` — shared fixture (TestClient + synthetic DB)
- `scripts/run_fhir_conformance.py` — standalone runner (starts server or uses TestClient)
- `tests/fhir_conformance/README.md` — how to run + extend

## Makefile target

```makefile
fhir-conformance:
	PYTHONPATH=src $(PYTHON) -m pytest tests/fhir_conformance/ -v --tb=short
```

## Verification

```bash
# Run the conformance suite
make fhir-conformance

# Or directly
PYTHONPATH=src pytest tests/fhir_conformance/ -v

# Expected output:
# tests/fhir_conformance/test_runner.py::test_case[lookup-snomed-valid] PASSED
# tests/fhir_conformance/test_runner.py::test_case[validate-code-invalid] PASSED
# ...
# 40 passed in 15.2s
```

## Time estimate

| Component | Effort |
|---|---|
| cases.json (~40 declarative test cases) | 2 hours |
| test_runner.py (parametrized pytest runner) | 1 hour |
| conftest.py (TestClient + synthetic DB with hierarchy) | 30 min |
| run_fhir_conformance.py (standalone runner) | 30 min |
| Makefile target + README | 15 min |
| **Total** | **~4 hours** |
