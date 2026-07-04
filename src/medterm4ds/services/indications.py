"""Indication (condition → medication) helpers.

Service-layer re-exports for indication relationship querying. The actual
implementations live in `medterm4ds.engines.duckdb.indications` because
they're tightly coupled to the engine's query machinery. This module exists
so domain code (`domains/terminology.drugs_for_indication`) doesn't reach
into engine internals, which would break the Protocol-based layering claim.

Callers wanting indication data should:
1. Call `services.indications.validate_indication_relationships(...)` to
   normalize user input (pure function, no engine needed).
2. Call `engine.get_drugs_for_indication(...)` if the engine supports it
   (check via the `IndicationsEngine` Protocol or `hasattr`).
3. Pass each result row through `services.indications.format_condition_medication_row`
   to shape it for output.
"""

from __future__ import annotations

from medterm4ds.engines.duckdb.indications import (
    _INDICATION_TARGET_TTYS,
    format_condition_medication_row,
    validate_indication_relationships,
)

__all__ = [
    "_INDICATION_TARGET_TTYS",
    "format_condition_medication_row",
    "validate_indication_relationships",
]
