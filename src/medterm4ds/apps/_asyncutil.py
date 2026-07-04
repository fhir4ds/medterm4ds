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
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, partial(func, *args, **kwargs))
