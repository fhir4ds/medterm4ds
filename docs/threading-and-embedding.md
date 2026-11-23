# Threading and process embedding guide

How to embed medterm4ds in a host process (in-process adapter) or a
server, and which concurrency contracts the library enforces versus
expects the host to enforce. Written after FHIR4-22/FHIR4-23 (2026-11-20):
a fhir4ds-side experiment hit transient `TypeError: 'int' object is not
callable` inside pure-Python search code and process segfaults, both
traced to concurrent native inference — not to a torch x DuckDB interop
problem.

## What the library enforces (host needs to do nothing)

**Native inference is serialized internally** (`_infer_lock` in
`SemanticSearchEngine`, since FHIR4-22 fix): the HF fast tokenizer and
the SapBERT torch forward are not safe to invoke concurrently on one
engine instance — concurrent calls corrupt native heap state, which
surfaces as transient TypeErrors in unrelated pure-Python frames or
outright segfaults. `search`/`semantic`/`hybrid` and `embed_batch`
acquire the lock automatically; concurrent callers queue.

**BM25 lazy init is serialized** (`_ensure_lock` in `SearchService`):
first-use index loading cannot double-load under threads.

## What the host must enforce

**One thread per DuckDB connection.** The DuckDB Python connection is
not thread-safe under concurrent use. The FHIR API serializes all DB
work on a single-worker executor for this reason; an in-process adapter
must do the same (one DB thread, or an external lock around engine calls
that share a connection). Violating this produces row-shape errors
(`ValueError: not enough values to unpack`), not corruption — but it is
unsupported.

## torch x DuckDB coexistence

There is **no load-ordering or co-residency constraint**. Verified
experimentally (torch 2.7.1+cu126, DuckDB 1.5.6, CPython 3.10, WSL2):

- Single-threaded co-load with alternating heavy use: stable in both
  import orders, including CUDA initialization (6/6 runs).
- Concurrent DuckDB lookups (serialized per connection) + 6-thread
  hybrid search on the fixed code: stable, zero failures, ~800 lookups
  and 720 searches per run (2/2 runs).

Earlier same-process segfaults attributed to torch+DuckDB coexistence
were concurrent native inference on one engine (fixed by the
`_infer_lock`); phase-isolated subprocess runners worked only because
they incidentally serialized inference. The in-process adapter is the
supported embedding pattern — the HTTP sidecar remains useful for
process isolation and independent scaling, not for correctness.
