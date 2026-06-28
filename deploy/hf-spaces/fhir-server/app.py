"""HF Spaces startup wrapper for the medterm4ds FHIR terminology server.

Downloads pre-computed data from HF datasets on first start (cached in
persistent storage), then starts the FHIR facade on port 7860.

Data sources:
  - lookup.duckdb (~1 GB): filtered mrconso/mrrel/mrsat from HF dataset
  - patient_friendly_*.json (~500 MB): pre-computed patient-friendly names
  - condition_associations.json (~220 MB): condition→medication mappings
  - rxnorm-ingredients.json (~5 MB): RxNorm product decomposition
  - *_bm25.json (~192 MB): BM25 search indexes for $search

Set environment variables:
  MEDTERM4DS_DB: path to lookup.duckdb (downloaded if absent)
  MEDTERM4DS_FHIR4PX_BASELINE: path to patient_friendly JSONs directory
  MEDTERM4DS_SEARCH_INDEX_DIR: path to BM25 indexes
  MEDTERM4DS_HF_DATASET: HF dataset repo for downloads (default: joelmontavon/fhir4px-lookup)
  MEDTERM4DS_MEMORY_PROFILE: duckdb memory profile (default: low)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def download_data():
    """Download data from HF datasets if not already cached."""
    data_dir = Path(os.getenv("MEDTERM4DS_DATA_DIR", "/data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    db_path = data_dir / "lookup.duckdb"
    baseline_dir = data_dir / "fhir4px"
    bm25_dir = data_dir / "bm25"
    hf_dataset = os.getenv("MEDTERM4DS_HF_DATASET", "joelmontavon/fhir4px-lookup")

    if db_path.exists() and baseline_dir.is_dir():
        print(f"Data already cached at {data_dir}")
        return db_path, baseline_dir, bm25_dir

    print(f"Downloading data from HF dataset: {hf_dataset}...")
    try:
        from huggingface_hub import snapshot_download
        snapshot_download(
            repo_id=hf_dataset,
            repo_type="dataset",
            local_dir=str(data_dir),
        )
        print(f"Downloaded to {data_dir}")
    except ImportError:
        print("huggingface_hub not installed. Using local data if available.")
    except Exception as exc:
        print(f"Warning: could not download from HF: {exc}")
        print("Falling back to local data if available.")

    # Set env vars for the FHIR facade
    os.environ.setdefault("MEDTERM4DS_DB", str(db_path))
    os.environ.setdefault("MEDTERM4DS_FHIR4PX_BASELINE", str(baseline_dir))
    os.environ.setdefault("MEDTERM4DS_SEARCH_INDEX_DIR", str(bm25_dir))
    os.environ.setdefault("MEDTERM4DS_MEMORY_PROFILE", "low")

    return db_path, baseline_dir, bm25_dir


def main():
    """Start the FHIR terminology server on port 7860 (HF Spaces default)."""
    download_data()

    # Override port for HF Spaces (default 7860)
    os.environ.setdefault("MEDTERM4DS_FHIR_API_PORT", "7860")
    os.environ.setdefault("MEDTERM4DS_API_HOST", "0.0.0.0")  # HF Spaces requires public binding

    from medterm4ds.apps.fhir_api import main as fhir_main
    fhir_main()


if __name__ == "__main__":
    main()
