"""Checkpointed output writers for long-running exports."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping

from medterm4ds.core.models import CodeRef
from medterm4ds.outputs.records import ResultLike, to_csv_record, to_record

OutputFormat = Literal["jsonl", "csv"]


@dataclass(frozen=True)
class OutputPosition:
    """Last completed row in an output file."""

    rows: int = 0
    last_code: CodeRef | None = None

    @property
    def has_rows(self) -> bool:
        return self.rows > 0 and self.last_code is not None


def default_checkpoint_path(output_path: str | Path) -> Path:
    """Return the default sidecar checkpoint path for an output file."""
    return Path(f"{Path(output_path)}.checkpoint.json")


def read_output_position(path: str | Path, output_format: OutputFormat) -> OutputPosition:
    """Scan an existing output and return the last completed source/code."""
    output_path = Path(path)
    if not output_path.exists() or output_path.stat().st_size == 0:
        return OutputPosition()
    if output_format == "jsonl":
        return _read_jsonl_position(output_path)
    if output_format == "csv":
        return _read_csv_position(output_path)
    raise ValueError(f"Unsupported output format: {output_format}")


def write_checkpointed_rows(
    rows: Iterable[ResultLike],
    path: str | Path,
    *,
    output_format: OutputFormat,
    checkpoint_path: str | Path,
    append: bool = False,
    checkpoint_every: int = 1000,
    initial_position: OutputPosition | None = None,
    metadata: Mapping[str, Any] | None = None,
    on_row: Callable[[dict[str, Any], int], None] | None = None,
) -> OutputPosition:
    """Write rows and maintain a sidecar checkpoint.

    When resuming, callers should derive `initial_position` from the existing
    output file, not only the checkpoint, so a crash after a row write but before
    checkpoint update does not duplicate that row.
    """
    output_path = Path(path)
    checkpoint = Path(checkpoint_path)
    if checkpoint_every < 1:
        raise ValueError("checkpoint_every must be at least 1")

    start_position = initial_position or OutputPosition()
    if output_format == "jsonl":
        final_position = _write_jsonl_checkpointed(
            rows,
            output_path,
            checkpoint,
            append=append,
            checkpoint_every=checkpoint_every,
            initial_position=start_position,
            metadata=metadata or {},
            on_row=on_row,
        )
    elif output_format == "csv":
        final_position = _write_csv_checkpointed(
            rows,
            output_path,
            checkpoint,
            append=append,
            checkpoint_every=checkpoint_every,
            initial_position=start_position,
            metadata=metadata or {},
            on_row=on_row,
        )
    else:
        raise ValueError(f"Unsupported output format: {output_format}")

    _write_checkpoint(
        checkpoint,
        output_path=output_path,
        output_format=output_format,
        position=final_position,
        complete=True,
        metadata=metadata or {},
    )
    return final_position


def _write_jsonl_checkpointed(
    rows: Iterable[ResultLike],
    output_path: Path,
    checkpoint: Path,
    *,
    append: bool,
    checkpoint_every: int,
    initial_position: OutputPosition,
    metadata: Mapping[str, Any],
    on_row: Callable[[dict[str, Any], int], None] | None,
) -> OutputPosition:
    mode = "a" if append else "w"
    current = initial_position
    written = 0
    with output_path.open(mode, encoding="utf-8") as file:
        for row in rows:
            record = to_record(row)
            file.write(json.dumps(record, sort_keys=True))
            file.write("\n")
            written += 1
            current = OutputPosition(rows=initial_position.rows + written, last_code=_code_from_record(record))
            if on_row:
                on_row(record, current.rows)
            if written % checkpoint_every == 0:
                file.flush()
                _write_checkpoint(
                    checkpoint,
                    output_path=output_path,
                    output_format="jsonl",
                    position=current,
                    complete=False,
                    metadata=metadata,
                )
    return current


def _write_csv_checkpointed(
    rows: Iterable[ResultLike],
    output_path: Path,
    checkpoint: Path,
    *,
    append: bool,
    checkpoint_every: int,
    initial_position: OutputPosition,
    metadata: Mapping[str, Any],
    on_row: Callable[[dict[str, Any], int], None] | None,
) -> OutputPosition:
    existing_header = _csv_fieldnames(output_path) if append and output_path.exists() else None
    file_has_rows = bool(existing_header)
    mode = "a" if append else "w"
    current = initial_position
    written = 0
    writer: csv.DictWriter | None = None

    with output_path.open(mode, encoding="utf-8", newline="") as file:
        for row in rows:
            record = to_record(row)
            csv_record = to_csv_record(record)
            if writer is None:
                fieldnames = existing_header or list(csv_record.keys())
                writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
                if not file_has_rows:
                    writer.writeheader()
            writer.writerow(csv_record)
            written += 1
            current = OutputPosition(rows=initial_position.rows + written, last_code=_code_from_record(record))
            if on_row:
                on_row(record, current.rows)
            if written % checkpoint_every == 0:
                file.flush()
                _write_checkpoint(
                    checkpoint,
                    output_path=output_path,
                    output_format="csv",
                    position=current,
                    complete=False,
                    metadata=metadata,
                )

    if written == 0 and not append:
        output_path.write_text("", encoding="utf-8")
    return current


def _read_jsonl_position(path: Path) -> OutputPosition:
    rows = 0
    last_record: dict[str, Any] | None = None
    with path.open(encoding="utf-8") as file:
        for line in file:
            stripped = line.strip()
            if not stripped:
                continue
            last_record = json.loads(stripped)
            rows += 1
    return OutputPosition(rows=rows, last_code=_code_from_record(last_record) if last_record else None)


def _read_csv_position(path: Path) -> OutputPosition:
    rows = 0
    last_record: dict[str, Any] | None = None
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            last_record = dict(row)
            rows += 1
    return OutputPosition(rows=rows, last_code=_code_from_record(last_record) if last_record else None)


def _csv_fieldnames(path: Path) -> list[str] | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.reader(file)
        try:
            return next(reader)
        except StopIteration:
            return None


def _write_checkpoint(
    path: Path,
    *,
    output_path: Path,
    output_format: OutputFormat,
    position: OutputPosition,
    complete: bool,
    metadata: Mapping[str, Any],
) -> None:
    data = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "output": str(output_path),
        "format": output_format,
        "rows": position.rows,
        "last_source": position.last_code.source if position.last_code else None,
        "last_code": position.last_code.code if position.last_code else None,
        "complete": complete,
        "metadata": dict(metadata),
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _code_from_record(record: Mapping[str, Any] | None) -> CodeRef | None:
    if not record:
        return None
    source = record.get("source")
    code = record.get("code")
    if source is None or code is None:
        return None
    return CodeRef(source=str(source), code=str(code))
