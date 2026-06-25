"""Set-based record comparison with structured field-level diffs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FieldDelta:
    """One field that differs between expected and actual."""

    path: str
    expected: Any
    actual: Any


@dataclass
class RecordDelta:
    """Per-record diff: list of field paths that differ."""

    key: Any
    fields: list[FieldDelta] = field(default_factory=list)


@dataclass
class DiffReport:
    """Structured diff between two canonical record sets."""

    added: list[Any] = field(default_factory=list)
    removed: list[Any] = field(default_factory=list)
    changed: list[RecordDelta] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    @property
    def total(self) -> int:
        return len(self.added) + len(self.removed) + len(self.changed)


def _diff_values(expected: Any, actual: Any, prefix: str, deltas: list[FieldDelta]) -> None:
    """Walk two values recursively, collecting field deltas."""
    if expected == actual:
        return
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual), key=lambda k: str(k)):
            if key not in expected:
                deltas.append(FieldDelta(f"{prefix}.{key}", "<missing>", actual[key]))
            elif key not in actual:
                deltas.append(FieldDelta(f"{prefix}.{key}", expected[key], "<missing>"))
            else:
                _diff_values(expected[key], actual[key], f"{prefix}.{key}", deltas)
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            deltas.append(FieldDelta(prefix, expected, actual))
        else:
            for i, (e, a) in enumerate(zip(expected, actual)):
                _diff_values(e, a, f"{prefix}[{i}]", deltas)
    else:
        deltas.append(FieldDelta(prefix, expected, actual))


def compare(expected: dict[Any, Any], actual: dict[Any, Any]) -> DiffReport:
    """Compare two canonical record dicts keyed by the same primary key."""
    expected_keys = set(expected)
    actual_keys = set(actual)
    report = DiffReport(
        added=sorted(actual_keys - expected_keys, key=lambda k: str(k)),
        removed=sorted(expected_keys - actual_keys, key=lambda k: str(k)),
    )
    for key in sorted(expected_keys & actual_keys, key=lambda k: str(k)):
        deltas: list[FieldDelta] = []
        _diff_values(expected[key], actual[key], "", deltas)
        if deltas:
            report.changed.append(RecordDelta(key=key, fields=deltas))
    return report
