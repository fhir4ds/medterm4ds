"""Runtime data provisioning for the terminology-demo HF Space.

Volume mounts (the preferred configuration, attached in the Space settings
UI) win: if the files are already at their mount points this module does
nothing. Otherwise it fetches them at container start into the Space's
ephemeral /data — the licence-restricted lookup.duckdb from the PRIVATE
dataset (fhir4ds/medterm4ds-lookup, token-gated; never baked into the
public image or repo) and the public fhir4ds/terminology release via
medterm4ds's own atomic downloader.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("terminology-demo.startup-data")

DB_PATH = os.getenv("MEDTERM4DS_DB", "/data/lookup.duckdb")
CACHE_DIR = Path(os.getenv("MEDTERM4DS_CACHE_DIR", "/data/cache"))
LOOKUP_DATASET = os.getenv("DEMO_LOOKUP_DATASET", "fhir4ds/medterm4ds-lookup")


def ensure_lookup_db() -> None:
    if Path(DB_PATH).exists():
        logger.info("lookup DB present at %s (volume mount)", DB_PATH)
        return
    token = os.getenv("DEMO_HF_TOKEN") or os.getenv("HF_TOKEN")
    if not token:
        raise RuntimeError(
            f"{DB_PATH} is missing and no DEMO_HF_TOKEN/HF_TOKEN is set. "
            "Either attach the private dataset fhir4ds/medterm4ds-lookup as "
            "a read-only volume at /data/lookup.duckdb, or set a token "
            "secret with read access to that dataset."
        )
    from huggingface_hub import hf_hub_download

    target_dir = str(Path(DB_PATH).parent)
    logger.info("fetching lookup.duckdb from private dataset %s", LOOKUP_DATASET)
    hf_hub_download(
        repo_id=LOOKUP_DATASET,
        filename="lookup.duckdb",
        repo_type="dataset",
        token=token,
        local_dir=target_dir,
    )
    if not Path(DB_PATH).exists():
        raise RuntimeError(f"download finished but {DB_PATH} is still missing")
    logger.info("lookup DB fetched to %s", DB_PATH)


def ensure_terminology_release() -> None:
    from medterm4ds.core.terminology import base_dir, download, is_present

    base = base_dir(CACHE_DIR)
    if is_present(base):
        logger.info("terminology release present at %s (volume mount)", base)
        return
    logger.info("fetching terminology release into %s (public repo)", base)
    download(CACHE_DIR)
    if not is_present(base):
        raise RuntimeError(f"terminology download finished but {base} incomplete")


def ensure_data() -> None:
    ensure_lookup_db()
    ensure_terminology_release()
