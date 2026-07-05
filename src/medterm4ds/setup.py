"""Interactive setup wizard for first-time medterm4ds users.

Usage:
    python -m medterm4ds.setup

Prompts for UMLS API key, builds lookup.duckdb, downloads derived
artifacts. Stores the key in ~/.medterm4ds/config.toml so subsequent
mt.connect() calls don't need UMLS_API_KEY in the environment.

For non-interactive use, just call mt.connect(umls_api_key=...) or
set the UMLS_API_KEY env var and call mt.connect().
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

UMLS_SIGNUP_URL = "https://www.nlm.nih.gov/account/"


def main() -> int:
    """Run the interactive setup wizard."""
    from medterm4ds.core.provision import (
        DEFAULT_UMLS_RELEASE,
        resolve_cache_home,
    )

    print()
    print("Welcome to medterm4ds setup.")
    print()
    print("medterm4ds uses UMLS Metathesaurus data, which requires an NLM API key.")
    print(f"Get one (free, requires accepting UMLS license): {UMLS_SIGNUP_URL}")
    print()

    # Step 1: Get the API key
    api_key = os.getenv("UMLS_API_KEY") or os.getenv("UTS_API_KEY")
    if api_key:
        print(f"Using UMLS_API_KEY from environment.")
    else:
        # Check config.toml
        config_path = resolve_cache_home() / "config.toml"
        if config_path.exists():
            import tomllib
            with open(config_path, "rb") as f:
                config = tomllib.load(f)
            api_key = config.get("umls", {}).get("api_key")

        if not api_key:
            api_key = getpass.getpass("Enter your UMLS API key: ").strip()
            if not api_key:
                print("No API key provided. Set UMLS_API_KEY env var and re-run.")
                return 1

    # Step 2: Pick UMLS release
    release = os.getenv("UMLS_RELEASE", DEFAULT_UMLS_RELEASE)
    print(f"\nUsing UMLS release: {release}")

    # Step 3: Save key to config.toml (so future connect() calls find it)
    home = resolve_cache_home()
    config_path = home / "config.toml"
    config_path.touch(mode=0o600, exist_ok=True)
    config_path.write_text(
        f'[umls]\napi_key = "{api_key}"\n'
    )
    os.chmod(config_path, 0o600)
    print(f"Saved API key to {config_path} (chmod 600)")

    # Step 4: Provision
    print(f"\nBuilding lookup.duckdb from UMLS {release} (one-time, ~8 min)...")
    print("Downloading derived artifacts from Hugging Face (~2 min)...")
    print()

    from medterm4ds.client import connect
    try:
        terms = connect(
            umls_api_key=api_key,
            version=release,
        )
    except Exception as exc:
        print(f"\nSetup failed: {exc}", file=sys.stderr)
        print(f"\nIf the API key is wrong, get a valid one at {UMLS_SIGNUP_URL}",
              file=sys.stderr)
        return 1

    # Step 5: Verify
    info = terms.lookup("SNOMEDCT_US", "44054006")
    if info and info.name:
        print(f"\n✓ Setup complete! Verified: SNOMED 44054006 → {info.name}")
    else:
        print("\n✓ Setup complete (lookup verification skipped — no SNOMED data)")

    print(f"\nYou can now use medterm4ds:")
    print(f"  import medterm4ds as mt")
    print(f"  terms = mt.connect()  # finds cached data automatically")
    print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
