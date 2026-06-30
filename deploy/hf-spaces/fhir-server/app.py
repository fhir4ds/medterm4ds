"""HF Spaces startup wrapper for the medterm4ds FHIR terminology server.

Data provisioning:

  lookup.duckdb (raw UMLS — license-restricted):
    Must be built from UMLS RRF files using the user's own NLM license.
    Set UMLS_API_KEY; the container downloads RRF from NLM and builds
    a 217 MB filtered DuckDB (~8 min first start, cached after).
    Cannot be downloaded from HF (UMLS license prohibits redistribution).

  BM25 + SapBERT + patient_friendly JSONs (derived data — freely distributable):
    Downloaded from joelmontavon/medterm4ds-data HF dataset (~3 GB).
    These are pre-computed indexes, not raw UMLS data.

Data layout after provisioning:
  /data/
    lookup.duckdb                    # filtered UMLS (217 MB, built from RRF)
    patient_friendly_*.json          # 8 per-source JSONs (~225 MB, from HF)
    bm25/*_bm25.json                 # 6 BM25 indexes (167 MB, from HF)
    sapbert/                         # SapBERT + FAISS (2.5 GB, from HF)
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

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


def _download_derived_data(data_dir: Path) -> tuple[Path, Path, Path]:
    """Download BM25 + SapBERT + patient_friendly JSONs from HF."""
    bm25_dir = data_dir / "bm25"
    sapbert_dir = data_dir / "sapbert"
    has_bm25 = bm25_dir.is_dir() and any(bm25_dir.glob("*_bm25.json"))
    has_sapbert = (sapbert_dir / "model.safetensors").exists()
    has_jsons = any(data_dir.glob("patient_friendly_*.json"))

    if has_bm25 and has_sapbert and has_jsons:
        print("Derived data already cached")
        return data_dir, bm25_dir, sapbert_dir

    print(f"Downloading derived data from HF: {HF_DATASET}...")
    try:
        from huggingface_hub import snapshot_download
        snapshot_download(
            repo_id=HF_DATASET,
            repo_type="dataset",
            local_dir=str(data_dir),
            token=os.getenv("HF_TOKEN"),
            allow_patterns=["patient_friendly_*.json", "bm25/*", "sapbert/*"],
        )
    except Exception as exc:
        print(f"Warning: download failed: {exc}")
        print("$search may not be available (other operations still work).")

    return data_dir, bm25_dir, sapbert_dir


def main():
    """Start the FHIR terminology server."""
    data_dir = Path(os.getenv("MEDTERM4DS_DATA_DIR", "/data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: Build lookup.duckdb from UMLS RRF (requires UMLS_API_KEY)
    db_path = _build_lookup_from_umls(data_dir)

    # Step 2: Download derived search data from HF (BM25, SapBERT, JSONs)
    baseline_dir, bm25_dir, sapbert_dir = _download_derived_data(data_dir)

    # Set env vars
    os.environ["MEDTERM4DS_DB"] = str(db_path)
    os.environ["MEDTERM4DS_FHIR4PX_BASELINE"] = str(baseline_dir)
    os.environ["MEDTERM4DS_SEARCH_INDEX_DIR"] = str(bm25_dir)
    os.environ["MEDTERM4DS_EMBEDDING_MODEL_DIR"] = str(sapbert_dir)
    os.environ.setdefault("MEDTERM4DS_MEMORY_PROFILE", "low")
    os.environ["MEDTERM4DS_FHIR_API_PORT"] = os.getenv("MEDTERM4DS_FHIR_API_PORT", "7860")
    os.environ["MEDTERM4DS_API_HOST"] = "0.0.0.0"

    from medterm4ds.apps.fhir_api import main as fhir_main
    fhir_main()


if __name__ == "__main__":
    main()
