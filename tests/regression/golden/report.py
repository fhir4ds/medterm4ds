"""Human-readable diff formatting for golden-file comparison failures."""

from __future__ import annotations

from typing import Any

from .compare import DiffReport, FieldDelta, RecordDelta

MAX_RECORDS_PER_CATEGORY = 25
MAX_FIELD_VALUE_LEN = 120


def _truncate(value: Any) -> str:
    text = repr(value)
    if len(text) > MAX_FIELD_VALUE_LEN:
        return text[: MAX_FIELD_VALUE_LEN - 3] + "..."
    return text


def _format_field_delta(delta: FieldDelta) -> str:
    return f"    {delta.path or '<root>'}:\n      expected: {_truncate(delta.expected)}\n      actual:   {_truncate(delta.actual)}"


def _format_record_delta(delta: RecordDelta) -> str:
    key = delta.key
    header = f"  {key}"
    if len(delta.fields) == 1:
        # Single-field change: collapse to one line for readability
        f = delta.fields[0]
        path = f" .{f.path}" if f.path else ""
        return (
            f"{header}{path}:\n"
            f"    expected: {_truncate(f.expected)}\n"
            f"    actual:   {_truncate(f.actual)}"
        )
    lines = [header]
    for f in delta.fields:
        lines.append(_format_field_delta(f))
    return "\n".join(lines)


def format_diff(name: str, diff: DiffReport, max_records: int = MAX_RECORDS_PER_CATEGORY) -> str:
    """Format a DiffReport as a readable string for pytest assertion messages."""
    sections: list[str] = [
        f"Golden parity drift for {name}: "
        f"{len(diff.added)} added, {len(diff.removed)} removed, {len(diff.changed)} changed"
    ]

    if diff.added:
        sections.append(f"\nADDED ({len(diff.added)} records, showing first {max_records}):")
        for key in diff.added[:max_records]:
            sections.append(f"  + {key}")

    if diff.removed:
        sections.append(f"\nREMOVED ({len(diff.removed)} records, showing first {max_records}):")
        for key in diff.removed[:max_records]:
            sections.append(f"  - {key}")

    if diff.changed:
        sections.append(f"\nCHANGED ({len(diff.changed)} records, showing first {max_records}):")
        for record_delta in diff.changed[:max_records]:
            sections.append(_format_record_delta(record_delta))

    hidden = max(0, len(diff.changed) - max_records)
    if hidden:
        sections.append(f"\n(... {hidden} more changed records not shown)")

    sections.append(
        "\nIf these changes are intentional: rebuild reports/fhir4px/ via "
        "`PYTHONPATH=src python3 scripts/build_fhir4px_all.py` and update "
        "tests/regression/fixtures/pinned_meta.json with the new SHA256 hashes."
    )
    return "\n".join(sections)
