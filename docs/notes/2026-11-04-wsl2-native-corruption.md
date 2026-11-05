# Native memory-corruption crashes in the search path (WSL2) — open finding

Date: 2026-11-04. Found while benchmarking AMUX-63 (fusion depth sweep).
Board is off for this repo, so this file is the task record. Not yet
root-caused; needs gdb/valgrind (not installed) or repro on another host.

## Symptom

Any process performing more than ~10 `SearchService.hybrid()` (or bare
`lexical()`) calls dies nondeterministically (~30–70% of processes) with
one of:

- absurd `TypeError`s from pure-Python frames, e.g.
  `'set_iterator' object is not callable` at
  `all(t in low for t in q_token_set)` (services/search.py lexical
  surface pass), `'str_ascii_iterator' object is not callable` at
  `"".join(...)` in `_strip_accents` (search.py:696),
  `descriptor 'join' for 'str' objects doesn't apply to a 'code' object`
- `Fatal Python error: _PyEval_EvalFrameDefault: Executing a cache.`
  with `<invalid frame>`
- silent SIGSEGV/SIGABRT; `dmesg` shows the fault inside the CPython
  binary in a refcount increment (`mov eax,[rcx]; inc eax`), and WSL
  `CaptureCrash` entries for the python processes.

These are Python-object corruption symptoms (builtin slots / method
caches holding iterator objects), i.e. native heap corruption, not
logic errors.

## What was ruled out

- **Not depth-dependent** — crashes at HYBRID_CANDIDATE_DEPTH 10/25/100.
- **Not a CPython version bug** — reproduces on uv cpython 3.12.13 and
  3.13.13.
- **Not faiss/torch threading** — persists with
  `OMP_NUM_THREADS=1`, `faiss.omp_set_num_threads(1)`,
  `torch.set_num_threads(1)`, `TOKENIZERS_PARALLELISM=false`.
- **Not faiss/numpy ABI** — numpy 2.2.6 pin did not help; also crashes
  with FAISS never called.
- **Not torch** — crashes with a `lexical()`-only loop, semantic engine
  never ensured (extension modules at crash: `duckdb`, `pydantic_core`
  only).
- **Not duckdb** — stubbing `sys.modules['duckdb']` before import (real
  code never runs) still crashes.
- **Not GC** — `gc.disable()` does not help.
- **Not bytecode cache** — clearing all `__pycache__` +
  `PYTHONDONTWRITEBYTECODE=1` does not help.
- **Not C-level buffer overrun caught by allocator** —
  `PYTHONMALLOC=debug` reports nothing before the crash.
- **Not the code itself under light load** — a synthetic pure-Python
  string/set/unicodedata stress with the same shape passes; loading the
  stack and doing nothing passes; short sequences often pass.

## Environment

WSL2 2.7.12.0, kernel 6.18.33.2-microsoft-standard-WSL2, 32 cores,
64 GB RAM, no EDAC/hardware-error log entries, `.wslconfig` only sets
`vmIdleTimeout=-1` (no autoMemoryReclaim). faiss-cpu 1.14.3,
torch 2.12.1+cu130 (CPU use), transformers 4.57.6, numpy 2.4.6.

## Working hypothesis

Environment-level memory corruption inside the WSL2 VM affecting
long-running python processes, or a native dependency corrupting the
CPython heap in a way that only manifests under the allocation pattern
of the real BM25 surface pass (large working set, many small string/set
allocations). Next steps: gdb/valgrind on a crashing minimal repro, or
reproduce on another host / native Linux box to split machine vs code.

## Practical consequence

Benchmark/sweep runs must be checkpointed and retried in subprocesses
(see `benchmarks/depth_sweep.py`). Long-lived servers may be affected
too — possibly relevant to flakiness seen by the fhir4ds lane.
