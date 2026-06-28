"""Shared fixtures and helpers for the regression test suite."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
DEFAULT_BUILD_TIMEOUT = 900  # 15 minutes; patient_friendly alone takes ~7.5


@dataclass(frozen=True)
class Deliverable:
    """One fhir4px output file and how to produce it."""

    name: str
    filename: str
    kind: str
    build_script: str
    build_args: tuple[str, ...] = ()


# All build scripts that consume only the DuckDB (no prior-step output).
# Tier 2 smoke tests invoke these in isolation. patient_friendly must run first
# because embedding_index reads its CSV output — that's encoded in the
# orchestration test, not in this registry.
PATIENT_FRIENDLY_DELIVERABLES = [
    Deliverable(
        name=f"patient_friendly_{src}",
        filename=f"patient_friendly_{src}.json",
        kind="patient_json",
        build_script="build_fhir4px_patient_friendly.py",
        build_args=("--output-dir",),  # +out_dir
    )
    for src in (
        "icd10cm",
        "icd10pcs",
        "snomedct_us",
        "rxnorm",
        "lnc",
        "cpt",
        "hcpcs",
        "cvx",
    )
]

EMBEDDING_DELIVERABLES = [
    Deliverable(
        name=f"embedding_index_{cat}",
        filename=f"embedding_index_{cat}.jsonl",
        kind="embedding",
        build_script="build_fhir4px_embedding_index.py",
        build_args=("--output-dir",),  # +out_dir; also needs --input (CSV)
    )
    for cat in (
        "condition",
        "lab",
        "medication",
        "procedure",
        "vaccine",
        "body_structure",
    )
]

ASSOCIATIONS_DELIVERABLE = Deliverable(
    name="condition_associations",
    filename="condition_associations.json",
    kind="associations",
    build_script="build_fhir4px_associations.py",
    build_args=("--output",),  # +output_path
)

RXNORM_INGREDIENTS_DELIVERABLE = Deliverable(
    name="rxnorm_ingredients",
    filename="rxnorm-ingredients.json",
    kind="rxnorm_ingredients",
    build_script="build_fhir4px_rxnorm_ingredients.py",
    build_args=("--output",),  # +output_path
)

PATIENT_FRIENDLY_CSV_DELIVERABLE = Deliverable(
    name="patient_friendly_names_csv",
    filename="patient_friendly_names.csv",
    kind="patient_csv",
    build_script="build_fhir4px_patient_friendly.py",
    build_args=("--output-dir",),
)

ALL_DELIVERABLES = (
    PATIENT_FRIENDLY_DELIVERABLES
    + EMBEDDING_DELIVERABLES
    + [ASSOCIATIONS_DELIVERABLE, RXNORM_INGREDIENTS_DELIVERABLE, PATIENT_FRIENDLY_CSV_DELIVERABLE]
)


def run_build_script(
    script_name: str,
    out_dir: Path,
    umls_db_path: Path,
    *,
    extra_args: tuple[str, ...] = (),
    timeout: int = DEFAULT_BUILD_TIMEOUT,
    input_csv: Path | None = None,
) -> subprocess.CompletedProcess:
    """Invoke a build_fhir4px_*.py script via subprocess.

    out_dir is created if missing. All build scripts accept --db; the caller
    passes script-specific path args via extra_args. Returns the CompletedProcess.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    script_path = SCRIPTS_DIR / script_name
    cmd: list[str] = [
        sys.executable,
        str(script_path),
        "--db",
        str(umls_db_path),
    ]
    if "build_fhir4px_embedding_index.py" in script_name and input_csv is not None:
        cmd.extend(["--input", str(input_csv)])
    if "build_fhir4px_associations.py" in script_name or "build_fhir4px_rxnorm_ingredients.py" in script_name:
        # These use --output (a file path), passed via extra_args
        pass
    cmd.extend(extra_args)

    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )


@pytest.fixture(scope="session")
def fhir4px_outputs_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Per-session tmp directory for build outputs."""
    return tmp_path_factory.mktemp("fhir4px_regression")


@dataclass(frozen=True)
class Fhir4pxBuildResult:
    """Bundle of paths produced by one full pipeline run."""

    out_dir: Path
    patient_friendly_csv: Path
    patient_friendly_jsons: dict[str, Path]
    embedding_jsonls: dict[str, Path]
    associations: Path
    rxnorm_ingredients: Path


def _run_pipeline(out_dir: Path, umls_db_path: Path) -> Fhir4pxBuildResult:
    """Run all four build_fhir4px_*.py scripts in order, returning output paths."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: patient_friendly (CSV + 8 JSONs in --output-dir)
    pf_dir = out_dir / "patient_friendly"
    pf_dir.mkdir(exist_ok=True)
    result = run_build_script(
        "build_fhir4px_patient_friendly.py",
        pf_dir,
        umls_db_path,
        extra_args=("--output-dir", str(pf_dir)),
    )
    assert result.returncode == 0, f"patient_friendly build failed:\n{result.stderr}"

    csv_path = pf_dir / "patient_friendly_names.csv"
    json_paths = {
        src: pf_dir / f"patient_friendly_{src}.json"
        for src in ("icd10cm", "icd10pcs", "snomedct_us", "rxnorm", "lnc", "cpt", "hcpcs", "cvx")
    }

    # Step 2: embedding_index (reads CSV from step 1)
    emb_dir = out_dir / "embedding_index"
    emb_dir.mkdir(exist_ok=True)
    result = run_build_script(
        "build_fhir4px_embedding_index.py",
        emb_dir,
        umls_db_path,
        extra_args=("--output-dir", str(emb_dir)),
        input_csv=csv_path,
    )
    assert result.returncode == 0, f"embedding_index build failed:\n{result.stderr}"

    emb_paths = {
        cat: emb_dir / f"embedding_index_{cat}.jsonl"
        for cat in ("condition", "lab", "medication", "procedure", "vaccine", "body_structure")
    }

    # Step 3: associations (with Synthea labs if available)
    assoc_path = out_dir / "condition_associations.json"
    assoc_extra_args: list[str] = ["--output", str(assoc_path)]
    synthea_path = Path("/mnt/d/fhir4px/public/terminology/synthea_condition_lab_codes.json")
    if synthea_path.exists():
        assoc_extra_args += ["--synthea-labs", str(synthea_path)]
    result = run_build_script(
        "build_fhir4px_associations.py",
        out_dir,
        umls_db_path,
        extra_args=tuple(assoc_extra_args),
    )
    assert result.returncode == 0, f"associations build failed:\n{result.stderr}"

    # Step 4: rxnorm ingredients
    rxnorm_path = out_dir / "rxnorm-ingredients.json"
    result = run_build_script(
        "build_fhir4px_rxnorm_ingredients.py",
        out_dir,
        umls_db_path,
        extra_args=("--output", str(rxnorm_path)),
    )
    assert result.returncode == 0, f"rxnorm_ingredients build failed:\n{result.stderr}"

    return Fhir4pxBuildResult(
        out_dir=out_dir,
        patient_friendly_csv=csv_path,
        patient_friendly_jsons=json_paths,
        embedding_jsonls=emb_paths,
        associations=assoc_path,
        rxnorm_ingredients=rxnorm_path,
    )


@pytest.fixture(scope="session")
def fhir4px_built(fhir4px_outputs_dir: Path, umls_db_path: Path) -> Fhir4pxBuildResult:
    """Run the full fhir4px pipeline once per session. ~10 min on first run."""
    return _run_pipeline(fhir4px_outputs_dir, umls_db_path)
