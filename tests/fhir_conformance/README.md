# FHIR R4 Conformance Test Suite

Declarative test cases that exercise all FHIR R4 terminology operations against
the medterm4ds FHIR facade. Each case specifies an HTTP request and expected
response, validated automatically.

## Running

```bash
# Full suite
make fhir-conformance

# Or directly
PYTHONPATH=src pytest tests/fhir_conformance/ -v

# Specific operation
PYTHONPATH=src pytest tests/fhir_conformance/ -v -k "lookup"
```

## Adding a test case

Edit `cases.json` and add a new entry:

```json
{
  "id": "my-new-test",
  "description": "What this test checks",
  "method": "GET",
  "path": "/fhir/CodeSystem/$lookup",
  "params": {"system": "http://snomed.info/sct", "code": "44054006"},
  "expected_status": 200,
  "expected_resource_type": "Parameters",
  "expected_fields": [
    {"path": "parameter[?name=='display'].valueString", "contains": "diabetes"}
  ]
}
```

### Field check types

| Key | Description |
|---|---|
| `equals` | Exact match |
| `contains` | Case-insensitive substring |
| `present` | Field exists (true) or is absent (false) |
| `gte` | Numeric >= comparison |

### Custom checks

For complex assertions, use `custom_check`:

| Check | What it does |
|---|---|
| `all_operations_advertised` | Verifies all 7 ops in CapabilityStatement |
| `expansion_contains_codes` | Checks expansion has expected codes (+ `expected_codes` array) |
| `closure_subsumes` | Verifies closure subsumption (+ `closure_name`, `code_a`, `code_b`, `expected_outcome`) |

## HAPI FHIR interop (optional)

If a HAPI FHIR server is running (Docker, port 18080):

```bash
# Compare our responses with HAPI's
PYTHONPATH=src pytest tests/fhir_conformance/ -v --hapi-url http://localhost:18080/fhir
```

This sends the same requests to both servers and compares the response structure.
Differences are reported but don't fail the test (HAPI may return different display
strings or additional fields).
