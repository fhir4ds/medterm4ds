"""Shared async helpers for the medterm4ds app layer.

Both `mcp.py` and `fhir_api.py` need to offload synchronous DuckDB-touching
work to a single-worker ThreadPoolExecutor (DuckDB Python connections are
not thread-safe under concurrent use). This module centralizes that helper
so the two apps don't drift apart.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial


async def run_db(executor: ThreadPoolExecutor, func, *args, **kwargs):
    """Offload a sync call to the given single-worker executor.

    `executor` is per-app (lives on McpRuntime.db_executor or
    app.state.db_executor) so multiple servers in one process get
    independent workers and clean teardown.
    """
    if executor is None:
        # QC-420 (LOW): run_in_executor(None, ...) silently falls back to the
        # loop's DEFAULT executor — post-close straggler tools then ran on a
        # second thread pool against a torn-down runtime (some succeeding,
        # some raising 'engine not ready'). Keep the documented contract:
        # handlers run between open() and close(); a missing executor is a
        # shutdown race, so fail fast.
        raise RuntimeError(
            "db executor is not available (server not opened or already closed)"
        )
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, partial(func, *args, **kwargs))
