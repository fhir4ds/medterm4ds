"""Parametrized FHIR R4 conformance test runner.

Loads declarative test cases from cases.json and runs each against the FHIR
facade via TestClient. Validates status codes, resource types, field values,
and custom checks (subsumption, closure, expansion contents).

Usage:
  pytest tests/fhir_conformance/ -v
  make fhir-conformance
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from .conftest import load_cases

ALL_CASES = load_cases()


def _extract_value(data: dict, path: str) -> Any:
    """Extract a value from nested dict/list using a simple path.

    Supports:
      - Dict keys: "resourceType"
      - List filter: "parameter[?name=='display'].valueString"
      - Numeric: "expansion.total"
    """
    parts = path.split(".")
    current: Any = data
    for part in parts:
        if current is None:
            return None
        # Handle list filter: key[?name=='value']
        if "[?" in part:
            key = part.split("[?")[0]
            filter_expr = part[len(key) + 2 : part.index("]")]
            # Parse: name=='display' or name=="display"
            filter_key, _, filter_val = filter_expr.partition("==")
            filter_key = filter_key.strip()
            filter_val = filter_val.strip().strip("'\"")
            if isinstance(current, dict):
                current = current.get(key)
            if not isinstance(current, list):
                return None
            matches = [item for item in current if isinstance(item, dict) and str(item.get(filter_key)) == filter_val]
            current = matches[0] if matches else None
        elif "[" in part and part.endswith("]"):
            key = part.split("[")[0]
            idx = int(part[len(key) + 1 : -1])
            if isinstance(current, dict):
                current = current.get(key)
            if isinstance(current, list) and 0 <= idx < len(current):
                current = current[idx]
            else:
                return None
        else:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
    return current


def _check_field(actual: Any, spec: dict) -> bool:
    """Check one field expectation. Returns (passed, message)."""
    path = spec["path"]
    actual_val = _extract_value(actual, path)
    if actual_val is None and "present" in spec:
        return spec["present"] is False, f"{path}: expected present={spec['present']}, got None"
    if "equals" in spec:
        return actual_val == spec["equals"], f"{path}: expected {spec['equals']!r}, got {actual_val!r}"
    if "contains" in spec:
        return str(spec["contains"]).lower() in str(actual_val or "").lower(), f"{path}: expected to contain {spec['contains']!r}, got {actual_val!r}"
    if "present" in spec:
        return actual_val is not None, f"{path}: expected present={spec['present']}, got {actual_val}"
    if "gte" in spec:
        return (actual_val or 0) >= spec["gte"], f"{path}: expected >= {spec['gte']}, got {actual_val}"
    return True, ""


def _custom_check(case: dict, response_body: dict, client) -> list[str]:
    """Run case-specific custom checks. Returns list of error messages (empty = pass)."""
    errors: list[str] = []
    check = case.get("custom_check", "")

    if check == "all_operations_advertised":
        rest = response_body.get("rest", [{}])[0]
        all_ops = set()
        for res in rest.get("resource", []):
            for op in res.get("operation", []):
                all_ops.add(op.get("name", ""))
        expected = {"lookup", "validate-code", "subsumes", "closure", "search", "expand", "translate"}
        missing = expected - all_ops
        if missing:
            errors.append(f"Missing operations in CapabilityStatement: {missing}")

    elif check == "expansion_contains_codes":
        contains = response_body.get("expansion", {}).get("contains", [])
        actual_codes = {c.get("code") for c in contains}
        for expected_code in case.get("expected_codes", []):
            if expected_code not in actual_codes:
                errors.append(f"Expected code {expected_code} not in expansion. Got: {actual_codes}")
        for unexpected_code in case.get("excluded_codes", []):
            if unexpected_code in actual_codes:
                errors.append(f"Code {unexpected_code} should be excluded but is present")

    elif check == "closure_subsumes":
        from medterm4ds.engines.fhir.closure import get_closure_manager
        closure = get_closure_manager().get(case["closure_name"])
        if closure is None:
            errors.append(f"Closure table '{case['closure_name']}' not found")
        else:
            outcome = closure.check(case["code_a"], case["code_b"])
            if outcome != case["expected_outcome"]:
                errors.append(
                    f"Closure check({case['code_a']}, {case['code_b']}): "
                    f"expected {case['expected_outcome']}, got {outcome}"
                )

    return errors


@pytest.mark.parametrize("case", ALL_CASES, ids=[c["id"] for c in ALL_CASES])
def test_case(case: dict, fhir_client):
    """Run one FHIR conformance test case."""
    from pathlib import Path
    model_dir = Path("/mnt/d/fhir4px-model/data/sapbert_finetuned")
    model_available = model_dir.exists()

    # Skip if model-dependent test and model is available (can't test the 503 path)
    if case.get("skip_if_model_available") and model_available:
        # Model is present — but if the case explicitly wants the 503 path,
        # skip it (it'd never trigger). Otherwise fall through to test the
        # success path with the case's own expected_status.
        if case.get("expected_status") == 503:
            pytest.skip("Model is available — can't test 503 path for this case")

    # Skip if test requires the model and it's not available
    if case.get("skip_unless_model_available") and not model_available:
        pytest.skip("Model is not available — can't test 200 path for this case")
    # Normal path
    # Send request
    method = case["method"]
    path = case["path"]
    if method == "GET":
        resp = fhir_client.get(path, params=case.get("params", {}))
    elif method == "POST":
        body = case.get("body", {})
        resp = fhir_client.post(path, json=body)
    else:
        pytest.fail(f"Unsupported method: {method}")

    # Check status code
    assert resp.status_code == case["expected_status"], (
        f"{case['id']}: expected status {case['expected_status']}, got {resp.status_code}. "
        f"Body: {resp.text[:300]}"
    )

    body = resp.json()

    # Check resourceType
    expected_rt = case.get("expected_resource_type")
    if expected_rt:
        assert body.get("resourceType") == expected_rt, (
            f"{case['id']}: expected resourceType {expected_rt}, got {body.get('resourceType')}"
        )

    # Check expected fields
    for field_spec in case.get("expected_fields", []):
        passed, message = _check_field(body, field_spec)
        assert passed, f"{case['id']}: {message}"

    # Run custom checks
    if "custom_check" in case:
        errors = _custom_check(case, body, fhir_client)
        for err in errors:
            pytest.fail(f"{case['id']}: {err}")
