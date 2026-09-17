from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# scripts/ is gitignored operator-local tooling (v0.0.3 QA-001): skip in
# clean checkouts / sdists rather than fail.
pytestmark = pytest.mark.skipif(
    not (ROOT / "scripts").is_dir(),
    reason="scripts/ not present (clean checkout / sdist); see v0.0.3 QA-001",
)


def test_example_notebooks_execute_against_synthetic_fixture(tmp_path):
    report_path = tmp_path / "notebook_smoke.json"
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{ROOT / 'src'}:{ROOT / 'scripts'}:{env.get('PYTHONPATH', '')}"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_notebook_smoke.py"),
            "--output-json",
            str(report_path),
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert {row["status"] for row in report["results"]} == {"pass"}
    assert {row["notebook"] for row in report["results"]} == {
        "ndc_rxnorm_resolution.ipynb",
        "patient_friendly_mapping_review.ipynb",
        "terminology_lookup.ipynb",
        "valueset_optimization.ipynb",
    }
