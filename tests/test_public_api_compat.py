from __future__ import annotations

import medterm4ds as mt


def test_local_duckdb_compatibility_aliases():
    from medterm4ds import (
        LOCAL_DUCKDB_MEMORY_PROFILES,
        LOCAL_LITE_MEMORY_PROFILES,
        LocalDuckDBConfig,
        LocalDuckDBEngine,
        LocalLiteConfig,
        LocalLiteEngine,
        local_duckdb_config,
        local_lite_config,
    )

    assert mt.__version__ == "0.0.2"
    assert LocalLiteEngine is LocalDuckDBEngine
    assert LocalLiteConfig is LocalDuckDBConfig
    assert LOCAL_LITE_MEMORY_PROFILES is LOCAL_DUCKDB_MEMORY_PROFILES
    assert local_lite_config is local_duckdb_config
