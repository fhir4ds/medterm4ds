# medterm4ds Architecture

`medterm4ds` is organized around one terminology core with multiple thin
interfaces. Bulk processing, API serving, MCP tools, and CLI commands should all
call the same service layer.

## Layers

```text
core/       Typed records, normalization, and configuration.
engines/    Execution backends implementing service protocols.
services/   Batch-first terminology workflows.
outputs/    Serialization and export helpers.
ds.py       Notebook/DataFrame-friendly wrappers over services.
domains/    Diagnosis, lab, procedure, drug, vaccine, and compatibility helpers.
apps/       CLI, API, and MCP adapters.
scripts/    Benchmarks, parity checks, and acceptance checks.
```

## Core Contracts

The core domain records are:

- `CodeRef`: normalized source/code input.
- `CodeInfo`: active UMLS atom metadata for exact code lookup.
- `CodeMapping`: source-to-target same-CUI mapping row.
- `CodeRelation`: one same-source hierarchy relationship row.
- `SourceStats`: code and atom counts for a terminology source.
- `NameSearchResult`: one active atom name search hit.
- `FriendlyNameResult`: one patient-friendly display result plus provenance.
- `ConceptMapRow`: source-to-target mapping row used by exports.
- `Provenance` and `ProvenanceStep`: structured path showing how a result was
  selected.

Services depend on protocols, not concrete engines. `LocalLiteEngine`, the
remote `RemoteApiEngine`, or a test double should be able to implement the same
service contract.

Each public result model has a versioned schema exposed through
`get_output_schema(...)`. The schema field order is expected to match the
model's `to_dict()` output and is treated as a stable downstream contract within
the schema version.

## Execution Model

`LocalLiteEngine` is the default local engine. It keeps large terminology data
inside DuckDB, uses temp input/cache tables, resolves codes in batches, and
chunks high-risk recursive query paths internally.

`RemoteApiEngine` implements the same protocols by POSTing to the FastAPI
surface. Python code can therefore switch from local DuckDB execution to a
remote terminology process without changing service calls.

Source-to-source mapping is exact same-CUI by default. Broader/narrower mapping
is opt-in through bounded hierarchy traversal so high-volume exports do not
silently expand into noisy mappings.

Memory behavior is controlled through `LocalLiteConfig` and named profiles:

- `fast`: more memory, higher throughput.
- `balanced`: default for typical local runs.
- `low`: one DuckDB thread and smaller query chunks for constrained machines.

DuckDB's memory limit is not a strict cap on total process RSS. Python overhead
and some DuckDB allocations can exceed the configured limit.

## Services

Current services:

- `get_code_info(...)`
- `get_code_infos(...)`
- `get_source_stats(...)`
- `sample_source_codes(...)`
- `get_code_ttys(...)`
- `search_names(...)`
- `get_code_mappings(...)`
- `get_code_relations(...)`
- `get_parents(...)`
- `get_children(...)`
- `get_ancestors(...)`
- `get_descendants(...)`
- `get_patient_friendly_names(...)`
- `get_concept_map(...)`
- `iter_concept_map(...)`
- source inventory helpers for DuckDB-backed code lists

These functions are batch-first. Single-code workflows should call the same
batch contract with one code.

`medterm4ds.ds` wraps these services for pandas or Polars use. DataFrame helpers
must not add terminology rules; they convert service outputs to tabular records
and preserve the same output schemas.

`domains/` provides thin workflow names for common clinical use cases and MCP
compatibility. UMLS-backed tools must delegate to lookup, search, mapping, or
hierarchy services. External evidence tools should remain explicit adapter
boundaries and return structured unavailable responses until their data sources
are configured.

## Bulk Is Not A Separate Terminology Layer

Bulk is treated as an execution/output mode, not as a separate transform tree.
The CLI streams source inventory through the same services and writes JSONL,
CSV, or FHIR output. Resume/checkpoint behavior lives in `outputs/` and the CLI,
not in terminology rules.

`services.bulk` provides shared batch iterators for lookup, mapping, hierarchy,
and patient-friendly workflows. CLI bulk commands should compose these iterators
with inventory streaming and checkpointed writers rather than creating separate
transform implementations.

## Interfaces

`apps/cli.py`, `apps/api.py`, and `apps/mcp.py` are intentionally thin:

- CLI handles arguments, inventory, output format, and resume.
- API owns one configured DuckDB engine per process and exposes service
  endpoints.
- MCP owns one configured DuckDB engine per process and registers structured
  tools.

None of these adapters should implement terminology rules.

## Compatibility

`engines/medterm_baseline` is a comparison adapter for the dirty
`/mnt/d/medterm` working tree. It is not a production engine. The parity script
uses it to compare semantic fields and record known old-medterm failures.

## Adding New Capabilities

When adding lookup, crosswalk, hierarchy/discover, drug, lab, or procedure
features:

1. Add or extend core result models only if the service needs a stable record.
2. Add service functions that operate on batches.
3. Implement the engine method in `LocalLiteEngine` or a new engine.
4. Add output helpers only if there is a new export shape.
5. Expose the behavior through CLI/API/MCP as adapters.

Avoid duplicating terminology logic in `apps/`, `outputs/`, or scripts.
