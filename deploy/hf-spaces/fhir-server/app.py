"""HF Spaces startup wrapper for the medterm4ds FHIR terminology server.

Supports two data provisioning paths:

  Path 1 — "Bring your own UMLS" (license-compliant):
    Set UMLS_API_KEY. Container downloads UMLS RRF from NLM and builds
    lookup.duckdb directly (~10 min first start, ~2 GB disk needed).
    No UMLS data is redistributed; the user's own NLM license applies.

  Path 2 — "Pre-built shortcut" (convenient):
    If lookup.duckdb already exists (cached or volume-mounted), use it.
    Otherwise download from joelmontavon/medterm4ds-data HF dataset.
    Note: the HF dataset contains filtered UMLS data — ensure you have
    appropriate NLM licensing for your use case.

  Path 2 is used when UMLS_API_KEY is not set.
  Path 1 is used when UMLS_API_KEY is set and lookup.duckdb doesn't exist.

Data layout after provisioning:
  /data/
    lookup.duckdb                    # filtered UMLS (287 MB)
    patient_friendly_*.json          # 8 per-source JSONs (~225 MB)
    bm25/*_bm25.json                 # 6 BM25 indexes (167 MB)
    sapbert/                         # SapBERT model + FAISS indexes (2.5 GB)
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

HF_DATASET = os.getenv("MEDTERM4DS_HF_DATASET", "joelmontavon/medterm4ds-data")
UMLS_RELEASE = os.getenv("UMLS_RELEASE", "2026AA")


def _provision_lookup_db(data_dir: Path) -> Path:
    """Provision lookup.duckdb — either build from UMLS RRF or download."""
    db_path = data_dir / "lookup.duckdb"

    # Already exists (cached from previous run or volume-mounted)
    if db_path.exists() and db_path.stat().st_size > 1_000_000:
        print(f"lookup.duckdb already exists ({db_path.stat().st_size / 1e6:.0f} MB)")
        return db_path

    api_key = os.getenv("UMLS_API_KEY") or os.getenv("UTS_API_KEY")

    if api_key:
        # Path 1: Build from UMLS RRF (license-compliant, ~10 min)
        print(f"Building lookup.duckdb from UMLS {UMLS_RELEASE} RRF files...")
        print(f"  (Using NLM API key — user's own UMLS license applies)")

        with tempfile.TemporaryDirectory(prefix="umls-") as tmp:
            tmp_path = Path(tmp)

            # Download UMLS release
            from medterm4ds.services.data_setup import download_release
            zip_path = download_release(
                output_dir=str(tmp_path),
                api_key=api_key,
                release_version=UMLS_RELEASE,
                extract=True,
            )
            print(f"  Downloaded + extracted: {zip_path.name}")

            # Find the META directory
            extract_dir = tmp_path / zip_path.stem
            meta_dirs = list(extract_dir.rglob("MRCONSO.RRF"))
            if not meta_dirs:
                raise RuntimeError(f"Could not find MRCONSO.RRF under {extract_dir}")
            rrf_dir = meta_dirs[0].parent
            print(f"  RRF directory: {rrf_dir}")

            # Build lookup.duckdb directly from RRF (no full 56 GB DB needed)
            from medterm4ds.services.lookup_builder import build_lookup_from_rrf
            build_lookup_from_rrf(rrf_dir, db_path)

            # Clean up RRF files (save disk space)
            shutil.rmtree(extract_dir, ignore_errors=True)

    else:
        # Path 2: Download pre-built from HF
        print(f"Downloading from HF dataset: {HF_DATASET}...")
        try:
            from huggingface_hub import snapshot_download
            snapshot_download(
                repo_id=HF_DATASET,
                repo_type="dataset",
                local_dir=str(data_dir),
                token=os.getenv("HF_TOKEN"),
                allow_patterns=["lookup.duckdb", "patient_friendly_*.json", "bm25/*", "sapbert/*"],
            )
        except Exception as exc:
            print(f"Download failed: {exc}")
            if not db_path.exists():
                raise RuntimeError(
                    "Cannot provision lookup.duckdb. Set UMLS_API_KEY to build from "
                    "UMLS RRF, or ensure MEDTERM4DS_DB points to an existing DB."
                )

    return db_path


def _provision_search_data(data_dir: Path) -> tuple[Path, Path, Path]:
    """Provision BM25 indexes, SapBERT model, and patient_friendly JSONs."""
    bm25_dir = data_dir / "bm25"
    sapbert_dir = data_dir / "sapbert"
    has_bm25 = bm25_dir.is_dir() and any(bm25_dir.glob("*_bm25.json"))
    has_sapbert = sapbert_dir.is_dir() and (sapbert_dir / "model.safetensors").exists()
    has_jsons = any(data_dir.glob("patient_friendly_*.json"))

    if has_bm25 and has_sapbert and has_jsons:
        print(f"Search data already cached")
        return data_dir, bm25_dir, sapbert_dir

    # Download remaining data from HF (BM25/SapBERT/JSONs are derived, not licensed)
    print(f"Downloading search data from HF: {HF_DATASET}...")
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
        print(f"Warning: could not download search data: {exc}")
        print("$search may not be available (other operations still work).")

    return data_dir, bm25_dir, sapbert_dir


def main():
    """Start the FHIR terminology server."""
    data_dir = Path(os.getenv("MEDTERM4DS_DATA_DIR", "/data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    db_path = _provision_lookup_db(data_dir)
    baseline_dir, bm25_dir, sapbert_dir = _provision_search_data(data_dir)

    # Set env vars for the FHIR facade
    os.environ["MEDTERM4DS_DB"] = str(db_path)
    os.environ["MEDTERM4DS_FHIR4PX_BASELINE"] = str(baseline_dir)
    os.environ["MEDTERM4DS_SEARCH_INDEX_DIR"] = str(bm25_dir)
    os.environ["MEDTERM4DS_EMBEDDING_MODEL_DIR"] = str(sapbert_dir)
    os.environ.setdefault("MEDTERM4DS_MEMORY_PROFILE", "low")

    # HF Spaces port + binding
    os.environ["MEDTERM4DS_FHIR_API_PORT"] = os.getenv("MEDTERM4DS_FHIR_API_PORT", "7860")
    os.environ["MEDTERM4DS_API_HOST"] = "0.0.0.0"

    from medterm4ds.apps.fhir_api import main as fhir_main
    fhir_main()


if __name__ == "__main__":
    main()
