"""Configuration records for terminology execution."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


def _detect_system_memory_gb() -> int:
    """Best-effort detection of total system memory in GB. Falls back to 16."""
    try:
        # Linux: /proc/meminfo
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return max(kb // (1024 * 1024), 1)
    except (OSError, ValueError, IndexError):
        pass
    return 16


def _default_fast_memory_limit() -> str:
    """Use ~50% of system memory for the 'fast' profile.

    DuckDB's memory_limit is a soft cap — parallel threads + Python overhead
    can push actual usage higher. 50% leaves headroom for the OS, Python
    interpreter, and DuckDB's own thread-local allocations. On a 64GB machine
    this yields ~32GB; on 16GB it yields 8GB.
    """
    sys_gb = _detect_system_memory_gb()
    fast_gb = max(int(sys_gb * 0.50), 4)
    return f"{fast_gb}GB"


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


# QC-380 (MEDIUM): DuckDB size-string format for memory_limit. Canonical
# pattern lives here (single source of truth per GLOBAL_RULES) so the CLI
# argparse validator, the Python ``connect()`` argument, and the
# MEDTERM4DS_MEMORY_LIMIT env path all enforce the same shape. Accept what
# DuckDB's ByteSize parser accepts: integer or decimal amount plus an
# optional decimal/binary unit suffix (case-insensitive).
_MEMORY_LIMIT_PATTERN = re.compile(
    r"^\s*\d+(?:\.\d+)?\s*(?:b|kb|mb|gb|tb|pb|eb|kib|mib|gib|tib|pib)?\s*$",
    re.IGNORECASE,
)


def validate_memory_limit(value: str) -> str:
    """Validate a DuckDB size string; raise ValueError naming the problem.

    Also rejects the empty string (QC-465: ``memory_limit=''`` previously
    fell through a falsy check in the engine and silently reverted the
    profile limit to DuckDB's ~80%-of-RAM default).
    """
    if not isinstance(value, str) or not _MEMORY_LIMIT_PATTERN.match(value):
        raise ValueError(
            f"memory_limit must be a DuckDB size string like 4GB or 512MB, "
            f"got {value!r}"
        )
    return value


LOCAL_DUCKDB_MEMORY_PROFILES: dict[str, LocalDuckDBConfig] = {
    # 'fast' uses ~50% of system memory by default and auto-detects threads.
    # This is the recommended profile for development machines with adequate RAM.
    "fast": LocalDuckDBConfig(
        memory_limit=_default_fast_memory_limit(),
        threads=None,  # auto-detect (uses all cores)
        query_chunk_size=5000,
    ),
    "balanced": LocalDuckDBConfig(memory_limit="1GB", threads=None, query_chunk_size=5000),
    "low": LocalDuckDBConfig(memory_limit="512MB", threads=1, query_chunk_size=1000),
}


def local_duckdb_config(
    profile: MemoryProfile = "fast",
    *,
    memory_limit: str | None = None,
    temp_directory: str | Path | None = None,
    threads: int | None = None,
    preserve_insertion_order: bool | None = None,
    query_chunk_size: int | None = None,
) -> LocalDuckDBConfig:
    """Build a local DuckDB config from a named profile plus explicit overrides.

    Explicit overrides are validated here (the single funnel used by the
    CLI, Python ``connect()``, and all three servers). Pre-fix, falsy
    values were silently dropped downstream: ``memory_limit=''`` reverted
    the profile limit, ``threads=0`` and ``temp_directory=''`` were
    ignored entirely (QC-465), and ``threads=-1`` reached DuckDB as a raw
    ``SyntaxException`` while ``query_chunk_size=-5`` was silently clamped
    to 1 (QC-466). Invalid overrides now raise ``ValueError`` naming the
    knob.
    """
    try:
        base = LOCAL_DUCKDB_MEMORY_PROFILES[profile]
    except KeyError as exc:
        choices = ", ".join(sorted(LOCAL_DUCKDB_MEMORY_PROFILES))
        raise ValueError(f"Unknown local DuckDB memory profile {profile!r}. Use one of: {choices}.") from exc

    # QC-465/QC-466: explicit (non-None) overrides must be well-formed —
    # never silently dropped and never forwarded to DuckDB raw.
    if memory_limit is not None:
        memory_limit = validate_memory_limit(memory_limit)
    if temp_directory is not None and not str(temp_directory).strip():
        raise ValueError(
            f"temp_directory must be a non-empty path, got {temp_directory!r}"
        )
    if threads is not None:
        threads = int(threads)
        if threads < 1:
            raise ValueError(f"threads must be a positive integer, got {threads!r}")
    if query_chunk_size is not None:
        query_chunk_size = int(query_chunk_size)
        if query_chunk_size < 1:
            raise ValueError(
                f"query_chunk_size must be a positive integer, got {query_chunk_size!r}"
            )

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
