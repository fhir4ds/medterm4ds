"""Record and file output helpers."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal, Protocol


class SupportsToDict(Protocol):
    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable record."""
        ...


ResultLike = SupportsToDict | Mapping[str, Any]
DataFrameBackend = Literal["pandas", "polars"]


def to_records(rows: Iterable[ResultLike]) -> list[dict[str, Any]]:
    """Convert service results to plain dictionaries."""
    return [to_record(row) for row in rows]


def to_dataframe(rows: Iterable[ResultLike], *, backend: DataFrameBackend = "pandas"):
    """Convert service results to a pandas or Polars DataFrame."""
    if backend == "pandas":
        return to_pandas(rows)
    if backend == "polars":
        return to_polars(rows)
    raise ValueError("backend must be 'pandas' or 'polars'")


def to_pandas(rows: Iterable[ResultLike]):
    """Convert service results to a pandas DataFrame when pandas is installed."""
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError("Install pandas to use to_pandas() or to_dataframe().") from exc
    return pd.DataFrame(to_records(rows))


def to_polars(rows: Iterable[ResultLike]):
    """Convert service results to a Polars DataFrame when polars is installed."""
    try:
        import polars as pl
    except ImportError as exc:
        raise ImportError("Install polars to use to_polars() or to_dataframe(backend='polars').") from exc
    return pl.DataFrame(to_records(rows))


def write_jsonl(rows: Iterable[ResultLike], path: str | Path) -> Path:
    """Write service results as newline-delimited JSON."""
    output_path = Path(path)
    with output_path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(to_record(row), sort_keys=True))
            file.write("\n")
    return output_path


def write_csv(rows: Iterable[ResultLike], path: str | Path) -> Path:
    """Write service results as CSV.

    Nested values such as provenance are serialized as compact JSON strings.
    """
    output_path = Path(path)
    iterator = iter(rows)
    try:
        first = to_csv_record(to_record(next(iterator)))
    except StopIteration:
        output_path.write_text("", encoding="utf-8")
        return output_path

    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(first.keys()), extrasaction="ignore")
        writer.writeheader()
        writer.writerow(first)
        for row in iterator:
            writer.writerow(to_csv_record(to_record(row)))
    return output_path


def to_record(row: ResultLike) -> dict[str, Any]:
    """Convert one service result to a plain dictionary."""
    if isinstance(row, Mapping):
        return dict(row)
    return row.to_dict()


def to_csv_record(row: Mapping[str, Any]) -> dict[str, Any]:
    """Convert nested values to CSV-safe scalar values."""
    return {
        key: _csv_value(value)
        for key, value in row.items()
    }


def _csv_value(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return value
