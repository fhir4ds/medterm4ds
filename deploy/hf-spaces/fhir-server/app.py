"""HF Spaces startup wrapper for the medterm4ds FHIR terminology server.

Downloads all required data from HF dataset on first start (cached in
persistent storage), then starts the FHIR facade on port 7860.

Data layout after download:
  /data/
    lookup.duckdb                    # filtered UMLS (287 MB)
    patient_friendly_*.json          # 8 per-source JSONs (~500 MB)
    bm25/*_bm25.json                 # 6 BM25 indexes (192 MB)
    sapbert/                         # SapBERT model + FAISS indexes (2.5 GB)
      model.safetensors
      config.json
      tokenizer.json
      *_faiss.index
      *_metadata.json

Env vars set automatically:
  MEDTERM4DS_DB              → /data/lookup.duckdb
  MEDTERM4DS_FHIR4PX_BASELINE → /data
  MEDTERM4DS_SEARCH_INDEX_DIR → /data/bm25
  MEDTERM4DS_EMBEDDING_MODEL_DIR → /data/sapbert
"""

from __future__ import annotations

import os
from pathlib import Path

HF_DATASET = os.getenv("MEDTERM4DS_HF_DATASET", "joelmontavon/medterm4ds-data")


def download_data():
    """Download all FHIR server data from HF dataset if not cached."""
    data_dir = Path(os.getenv("MEDTERM4DS_DATA_DIR", "/data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    db_path = data_dir / "lookup.duckdb"
    bm25_dir = data_dir / "bm25"
    sapbert_dir = data_dir / "sapbert"

    # Check if data is already present
    if db_path.exists() and bm25_dir.is_dir():
        print(f"Data already cached at {data_dir}")
    else:
        print(f"Downloading from HF dataset: {HF_DATASET}...")
        try:
            from huggingface_hub import snapshot_download
            snapshot_download(
                repo_id=HF_DATASET,
                repo_type="dataset",
                local_dir=str(data_dir),
                token=os.getenv("HF_TOKEN"),
            )
            print(f"Downloaded to {data_dir}")
        except Exception as exc:
            print(f"Warning: download failed: {exc}")
            print("Falling back to local data if available.")

    # Set env vars for the FHIR facade
    os.environ["MEDTERM4DS_DB"] = str(db_path)
    os.environ["MEDTERM4DS_FHIR4PX_BASELINE"] = str(data_dir)  # patient_friendly_*.json are at root
    os.environ["MEDTERM4DS_SEARCH_INDEX_DIR"] = str(bm25_dir)
    os.environ["MEDTERM4DS_EMBEDDING_MODEL_DIR"] = str(sapbert_dir)
    os.environ.setdefault("MEDTERM4DS_MEMORY_PROFILE", "low")


def main():
    """Start the FHIR terminology server."""
    download_data()

    os.environ["MEDTERM4DS_FHIR_API_PORT"] = os.getenv("MEDTERM4DS_FHIR_API_PORT", "7860")
    os.environ["MEDTERM4DS_API_HOST"] = "0.0.0.0"  # HF Spaces requires public binding

    from medterm4ds.apps.fhir_api import main as fhir_main
    fhir_main()


if __name__ == "__main__":
    main()
