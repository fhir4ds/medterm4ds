"""Configuration records for terminology execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class LocalDuckDBConfig:
    """Local DuckDB execution settings.

    DuckDB's memory limit is not a strict process RSS cap. Use `threads=1` and
    smaller `query_chunk_size` values for constrained machines.
    """

    memory_limit: str | None = "4GB"
    temp_directory: str | Path | None = None
    threads: int | None = None
    preserve_insertion_order: bool = False
    query_chunk_size: int = 5000


MemoryProfile = Literal["fast", "balanced", "low"]


LOCAL_DUCKDB_MEMORY_PROFILES: dict[str, LocalDuckDBConfig] = {
    "fast": LocalDuckDBConfig(memory_limit="4GB", threads=None, query_chunk_size=5000),
    "balanced": LocalDuckDBConfig(memory_limit="1GB", threads=None, query_chunk_size=5000),
    "low": LocalDuckDBConfig(memory_limit="512MB", threads=1, query_chunk_size=1000),
}


def local_duckdb_config(
    profile: MemoryProfile = "balanced",
    *,
    memory_limit: str | None = None,
    temp_directory: str | Path | None = None,
    threads: int | None = None,
    preserve_insertion_order: bool | None = None,
    query_chunk_size: int | None = None,
) -> LocalDuckDBConfig:
    """Build a local DuckDB config from a named profile plus explicit overrides."""
    try:
        base = LOCAL_DUCKDB_MEMORY_PROFILES[profile]
    except KeyError as exc:
        choices = ", ".join(sorted(LOCAL_DUCKDB_MEMORY_PROFILES))
        raise ValueError(f"Unknown local DuckDB memory profile {profile!r}. Use one of: {choices}.") from exc

    return LocalDuckDBConfig(
        memory_limit=base.memory_limit if memory_limit is None else memory_limit,
        temp_directory=base.temp_directory if temp_directory is None else temp_directory,
        threads=base.threads if threads is None else threads,
        preserve_insertion_order=(
            base.preserve_insertion_order
            if preserve_insertion_order is None
            else preserve_insertion_order
        ),
        query_chunk_size=base.query_chunk_size if query_chunk_size is None else query_chunk_size,
    )


# Backward-compatible aliases for pre-0.0.1 naming.
LocalLiteConfig = LocalDuckDBConfig
LOCAL_LITE_MEMORY_PROFILES = LOCAL_DUCKDB_MEMORY_PROFILES
local_lite_config = local_duckdb_config
