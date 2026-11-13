"""HF Spaces startup wrapper for the medterm4ds FHIR terminology server.

Data provisioning:

  lookup.duckdb (raw UMLS — license-restricted):
    Must be built from UMLS RRF files using the user's own NLM license.
    Set UMLS_API_KEY; the container downloads RRF from NLM and builds
    a 217 MB filtered DuckDB (~8 min first start, cached after).
    Cannot be downloaded from HF (UMLS license prohibits redistribution).

  BM25 + SapBERT + canonical artifacts (derived data — freely distributable):
    Downloaded from the fhir4ds/terminology HF repo as a self-contained
    release (the 2026-10-31 repoint). These are pre-computed indexes, not
    raw UMLS data.

  patient_friendly_*.json (release reports):
    Downloaded from joelmontavon/medterm4ds-data (reports are not part of
    the terminology release). $lookup enrichment degrades gracefully if
    unavailable.

Data layout after provisioning:
  /data/
    lookup.duckdb                    # filtered UMLS (217 MB, built from RRF)
    terminology/<release>/           # release layout: canonical/lexical/semantic/model
    patient_friendly_*.json          # 8 per-source JSONs (~225 MB, from HF)
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

# EV03-QA-06 (2026-11-12): set MEDTERM4DS_LAYOUT at MODULE TOP, BEFORE any
# medterm4ds import can execute (Step 1's data_setup import inside main()
# pulls in medterm4ds/__init__ → services.search, which freezes its
# _LAYOUT_ENV sentinel at import time). Setting it only inside
# _provision_search_artifacts worked on RESTARTS (cached lookup.duckdb
# returns before any medterm4ds import) but left first starts on "auto".
os.environ.setdefault("MEDTERM4DS_LAYOUT", "terminology")

# Patient-friendly baseline reports are NOT part of the fhir4ds/terminology
# release (they are release reports, not terminology artifacts); the legacy
# dataset remains their distribution channel for this demo Space.
HF_DATASET = os.getenv("MEDTERM4DS_HF_DATASET", "joelmontavon/medterm4ds-data")
UMLS_RELEASE = os.getenv("UMLS_RELEASE", "2026AA")


def _build_lookup_from_umls(data_dir: Path) -> Path:
    """Build lookup.duckdb from UMLS RRF (Path 1: license-compliant)."""
    db_path = data_dir / "lookup.duckdb"

    if db_path.exists() and db_path.stat().st_size > 1_000_000:
        print(f"lookup.duckdb already cached ({db_path.stat().st_size / 1e6:.0f} MB)")
        return db_path

    api_key = os.getenv("UMLS_API_KEY") or os.getenv("UTS_API_KEY")
    if not api_key:
        raise RuntimeError(
            "lookup.duckdb not found. Set UMLS_API_KEY to build from UMLS RRF, "
            "or mount a pre-built DB at /data/lookup.duckdb."
        )

    print(f"Building lookup.duckdb from UMLS {UMLS_RELEASE}...")
    print("  (User's own NLM UMLS license applies)")

    with tempfile.TemporaryDirectory(prefix="umls-") as tmp:
        tmp_path = Path(tmp)

        # Download + extract UMLS release
        from medterm4ds.services.data_setup import download_release
        zip_path = download_release(
            output_dir=str(tmp_path),
            api_key=api_key,
            release_version=UMLS_RELEASE,
            extract=True,
        )
        print(f"  Downloaded: {zip_path.name}")

        # Find META directory
        extract_dir = tmp_path / zip_path.stem
        meta_files = list(extract_dir.rglob("MRCONSO.RRF"))
        if not meta_files:
            raise RuntimeError(f"Could not find MRCONSO.RRF under {extract_dir}")
        rrf_dir = meta_files[0].parent
        print(f"  RRF: {rrf_dir}")

        # Build filtered lookup.duckdb directly (no 56 GB intermediate)
        from medterm4ds.services.lookup_builder import build_lookup_from_rrf
        build_lookup_from_rrf(rrf_dir, db_path)

        shutil.rmtree(extract_dir, ignore_errors=True)

    return db_path


def _provision_search_artifacts(data_dir: Path) -> None:
    """Provision search/extract artifacts via the fhir4ds/terminology
    release layout (the 2026-10-31 repoint; see README 'Artifact cache
    layout'). Downloads atomically into /data/terminology/<release>/ and
    pins the layout so the server never falls back to legacy/split."""
    # Redundant with the module-top setdefault (EV03-QA-06) — kept as a
    # harmless belt-and-braces for anyone importing this function alone.
    os.environ.setdefault("MEDTERM4DS_LAYOUT", "terminology")
    from medterm4ds.core import terminology as _term

    resolved = _term.download(data_dir)
    # Pin the freshly provisioned release dir: the default terminology root
    # (~/.cache) is NOT on the Space's persistent volume.
    os.environ["MEDTERM4DS_TERMINOLOGY_DIR"] = str(resolved.base)
    print(
        f"terminology release {resolved.release} ready "
        f"(embedding space {resolved.space_id}) at {resolved.base}"
    )


def _download_patient_friendly(data_dir: Path) -> Path:
    """Download patient_friendly_*.json baseline reports from HF.

    These predate the terminology release layout and ship separately
    (release reports, not terminology artifacts). $lookup enrichment
    degrades gracefully if the download fails."""
    if any(data_dir.glob("patient_friendly_*.json")):
        return data_dir

    print(f"Downloading patient-friendly reports from HF: {HF_DATASET}...")
    try:
        from huggingface_hub import snapshot_download
        snapshot_download(
            repo_id=HF_DATASET,
            repo_type="dataset",
            local_dir=str(data_dir),
            token=os.getenv("HF_TOKEN"),
            allow_patterns=["patient_friendly_*.json"],
        )
    except Exception as exc:
        print(f"Warning: download failed: {exc}")
        print("$lookup patient-friendly properties may be unavailable.")

    return data_dir


def main():
    """Start the FHIR terminology server."""
    data_dir = Path(os.getenv("MEDTERM4DS_DATA_DIR", "/data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Build lookup.duckdb from UMLS RRF (requires UMLS_API_KEY)
    db_path = _build_lookup_from_umls(data_dir)

    # Step 2: search/extract artifacts via the terminology release layout
    _provision_search_artifacts(data_dir)

    # Step 3: patient-friendly baseline reports (separate distribution)
    baseline_dir = _download_patient_friendly(data_dir)

    # Set env vars
    os.environ["MEDTERM4DS_DB"] = str(db_path)
    os.environ["MEDTERM4DS_FHIR4PX_BASELINE"] = str(baseline_dir)
    os.environ.setdefault("MEDTERM4DS_MEMORY_PROFILE", "low")
    os.environ["MEDTERM4DS_FHIR_API_PORT"] = os.getenv("MEDTERM4DS_FHIR_API_PORT", "7860")
    os.environ["MEDTERM4DS_API_HOST"] = "0.0.0.0"

    from medterm4ds.apps.fhir_api import main as fhir_main
    fhir_main()


if __name__ == "__main__":
    main()
