from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "compare_patient_friendly_benchmark.py"


def _module():
    spec = importlib.util.spec_from_file_location("compare_patient_friendly_benchmark", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Result:
    def to_dict(self):
        return {
            "name": "Diabetes Mellitus",
            "friendly_source": "CHV",
            "match_type": "broader",
            "match_depth": 1,
            "technical_name": "diabetes mellitus",
        }


def test_report_row_can_ignore_patient_friendly_name_case():
    mod = _module()
    row = {
        "source": "ICD10CM",
        "code": "E11.9",
        "original_name": "Type 2 diabetes mellitus",
        "friendly_name": "diabetes mellitus",
        "friendly_source": "CHV",
        "match_type": "broader",
    }

    strict = mod._report_row(
        row,
        _Result(),
        "Type 2 diabetes mellitus",
        classifications={},
        ignore_name_case=False,
    )
    relaxed = mod._report_row(
        row,
        _Result(),
        "Type 2 diabetes mellitus",
        classifications={},
        ignore_name_case=True,
    )

    assert strict["status"] == "mismatch"
    assert strict["mismatch_fields"] == "name"
    assert strict["name_case_only_difference"] == "false"
    assert relaxed["status"] == "match"
    assert relaxed["mismatch_fields"] == ""
    assert relaxed["name_case_only_difference"] == "true"

    summary = mod._EmptySummary()
    summary.add(relaxed)
    assert summary.matches == 1
    assert summary.name_case_only_differences == 1
