"""Native thread-pool pinning for the ML serving stack.

FHIR4-51 (2026-11-25): fhir4ds' hybrid-search benchmark hit SIGSEGV
(call through a null function pointer) ~2-5 min into long runs on WSL2,
plus intermittent *impossible* pure-Python errors on the search path
(``unicodedata.combining() argument must be a unicode character, not
str_iterator``, ``string index out of range``, ``'int' object is not
callable``) — the latter being the interpreter-visible face of the same
native corruption, not logic bugs. Stable only with
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 and DuckDB
threads=1: the torch/faiss OpenMP pools racing DuckDB workers corrupt
the process. ``MEDTERM4DS_DEVICE=cpu`` alone does not help.

medterm4ds therefore pins its OWN native pools (torch + faiss) to 1 by
default, via the explicit post-import APIs, so hosts only have to
control their own DuckDB connections. The knob:

- ``MEDTERM4DS_NATIVE_THREADS`` (default 1): thread count pinned into
  ``torch.set_num_threads`` / ``faiss.omp_set_num_threads`` at engine
  load. ``0`` disables pinning entirely (host manages everything).
- Distinct from ``MEDTERM4DS_THREADS``, which remains the DuckDB knob
  for the bundled apps — conflating them would silently re-pin torch
  for hosts that deliberately run DuckDB multi-threaded.
- When ``MEDTERM4DS_NATIVE_THREADS`` is set EXPLICITLY, the value is
  also exported to OMP/MKL/OPENBLAS_NUM_THREADS before torch/faiss
  import so late-loading native libs honor it; the default pin uses
  only the explicit APIs to keep effects contained to our stack.
"""

from __future__ import annotations

import logging
import os

from medterm4ds.core.env import env_int

logger = logging.getLogger(__name__)

# Env vars that must be set before a native lib loads to be honored by
# its OpenMP runtime; forwarded only on explicit operator request.
_FORWARDED_VARS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")

_applied: int | None = None


def native_thread_count() -> int | None:
    """Resolve the pinned thread count WITHOUT importing torch/faiss.

    Pure env parsing, so DuckDB-only paths (``mt.connect()``) can size
    their connections without dragging the ML stack into the process.
    Returns None when pinning is disabled
    (``MEDTERM4DS_NATIVE_THREADS=0``).
    """
    explicit = env_int("MEDTERM4DS_NATIVE_THREADS", minimum=0)
    if explicit == 0:
        return None
    return explicit if explicit is not None else 1


def duckdb_thread_config() -> dict[str, int] | None:
    """DuckDB connect ``config=`` for connections the LIBRARY opens.

    FHIR4-51 round 2: the ancestor-fallback path on the hybrid search
    opened a fresh DuckDB connection with no config, inheriting the
    ambient default (32 threads on the reporting host). Those C++
    worker threads are invisible to faulthandler, so a crash stack
    showing only the Python thread does NOT mean single-threaded — this
    pool churning connect/teardown inside the hybrid loop is a prime
    corruption suspect. Connections the HOST opens remain the host's
    responsibility.
    """
    threads = native_thread_count()
    if threads is None:
        return None
    return {"threads": threads}


def apply_thread_pinning() -> int | None:
    """Pin torch/faiss thread pools; safe to call repeatedly.

    Returns the applied thread count, or ``None`` when pinning is
    disabled (``MEDTERM4DS_NATIVE_THREADS=0``). Invalid values raise a
    named ``ValueError`` from ``env_int`` at load time — a typo'd knob
    must not silently run unpinned.
    """
    global _applied
    if _applied is not None:
        return _applied

    threads = native_thread_count()
    if threads is None:
        logger.info(
            "Native thread-pool pinning disabled (MEDTERM4DS_NATIVE_THREADS=0) "
            "— host is responsible for OMP/MKL/OPENBLAS pool sizing"
        )
        _applied = 0
        return None

    if env_int("MEDTERM4DS_NATIVE_THREADS", minimum=0) is not None:
        for var in _FORWARDED_VARS:
            os.environ[var] = str(threads)

    try:
        import torch

        torch.set_num_threads(threads)
        # Interop pool (default = all cores, observed unpinned at 16 on
        # the reporting host). Must run before interop work starts;
        # "already initialized" is benign — the pin came too late but
        # nothing is corrupted by skipping it.
        try:
            torch.set_num_interop_threads(threads)
        except RuntimeError:
            logger.info(
                "torch interop pool already initialized — "
                "set_num_interop_threads skipped"
            )
    except ImportError:
        pass
    try:
        import faiss

        faiss.omp_set_num_threads(threads)
    except ImportError:
        pass

    logger.info("Native thread pools pinned to %d thread(s)", threads)
    _applied = threads
    return threads
