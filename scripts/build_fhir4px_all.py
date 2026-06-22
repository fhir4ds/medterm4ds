#!/usr/bin/env python3
"""Step 5: Master pipeline orchestrator for fhir4px data deliverables.

Runs all four build steps in sequence and reports timing. Each step is
also independently runnable.

Usage:
  PYTHONPATH=src python3 scripts/build_fhir4px_all.py
  PYTHONPATH=src python3 scripts/build_fhir4px_all.py --skip step1  # skip patient-friendly
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time

STEPS = [
    ("step1", "Patient-friendly names",
     [sys.executable, "scripts/build_fhir4px_patient_friendly.py"]),
    ("step2", "Embedding index (5 categories)",
     [sys.executable, "scripts/build_fhir4px_embedding_index.py"]),
    ("step3", "Condition associations",
     [sys.executable, "scripts/build_fhir4px_associations.py"]),
    ("step4", "RxNorm ingredients",
     [sys.executable, "scripts/build_fhir4px_rxnorm_ingredients.py"]),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip", nargs="*", default=[], help="Step IDs to skip")
    args = parser.parse_args()

    env_prefix = ["env", "PYTHONPATH=src"]
    total_start = time.perf_counter()

    for step_id, label, cmd in STEPS:
        if step_id in args.skip:
            print(f"[{step_id}] SKIPPED — {label}")
            continue
        print(f"\n{'='*60}")
        print(f"[{step_id}] {label}")
        print(f"{'='*60}")
        start = time.perf_counter()
        full_cmd = env_prefix + cmd
        result = subprocess.run(full_cmd)
        elapsed = time.perf_counter() - start
        if result.returncode != 0:
            print(f"\n[{step_id}] FAILED (exit {result.returncode}) after {elapsed:.1f}s")
            return result.returncode
        print(f"\n[{step_id}] Done in {elapsed:.1f}s")

    total = time.perf_counter() - total_start
    print(f"\n{'='*60}")
    print(f"All steps complete in {total:.1f}s")
    print(f"{'='*60}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
