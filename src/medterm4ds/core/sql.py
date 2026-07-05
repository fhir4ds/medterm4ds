"""SQL literal escaping helpers.

Single source of truth for converting Python values to DuckDB SQL literals.
Previously duplicated (with drift) across engines/duckdb/_engine_base.py,
services/rxnorm_tty_walk.py, and services/patient_friendly_prepared.py —
the engine copy handled ints correctly (unquoted), the service copies
always quoted, which would silently produce '42' instead of 42 if a
non-string ever slipped through.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import TypeVar

T = TypeVar("T")


def sql_literal(value: object) -> str:
    """Render a Python value as a DuckDB SQL literal.

    Ints are emitted unquoted so DuckDB compares them as integers, not
    strings. Strings have single quotes escaped (``'`` -> ``''``).
    """
    if isinstance(value, int):
        return str(value)
    text = str(value).replace("'", "''")
    return f"'{text}'"


def sql_values(rows: Sequence[Sequence[object]]) -> str:
    """Render a sequence of rows as a VALUES clause body.

    E.g. ``[("a", 1), ("b", 2)]`` -> ``('a', 1),\\n('b', 2)``.
    """
    if not rows:
        raise ValueError("rows must not be empty")
    return ",\n                           ".join(
        "(" + ", ".join(sql_literal(value) for value in row) + ")"
        for row in rows
    )


def chunks(values: Sequence[T], size: int) -> Iterator[list[T]]:
    """Yield successive chunks of ``size`` items from ``values``."""
    for start in range(0, len(values), size):
        yield list(values[start:start + size])
