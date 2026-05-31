"""Compact ASCII renderers for CLI and MCP output."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def render_table(
    rows: Sequence[Mapping[str, Any]],
    *,
    columns: Sequence[str] | None = None,
    max_width: int = 36,
    max_rows: int | None = None,
) -> str:
    """Render dictionaries as a compact ASCII table."""
    visible_rows = list(rows[:max_rows] if max_rows is not None else rows)
    if not visible_rows:
        return "(no rows)"
    selected_columns = list(columns or _infer_columns(visible_rows))
    widths = {
        column: min(
            max(
                len(column),
                *[
                    len(_cell(row.get(column), max_width=max_width))
                    for row in visible_rows
                ],
            ),
            max_width,
        )
        for column in selected_columns
    }
    header = " | ".join(column.ljust(widths[column]) for column in selected_columns)
    divider = "-+-".join("-" * widths[column] for column in selected_columns)
    body = [
        " | ".join(
            _cell(row.get(column), max_width=widths[column]).ljust(widths[column])
            for column in selected_columns
        )
        for row in visible_rows
    ]
    suffix = []
    if max_rows is not None and len(rows) > max_rows:
        suffix.append(f"... {len(rows) - max_rows} more rows")
    return "\n".join([header, divider, *body, *suffix])


def render_tree(
    data: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    title: str | None = None,
    max_depth: int = 4,
    max_items: int = 20,
) -> str:
    """Render nested dictionaries/lists as a compact ASCII tree."""
    lines: list[str] = []
    if title:
        lines.append(str(title))
    _render_node(data, lines, prefix="", depth=0, max_depth=max_depth, max_items=max_items)
    return "\n".join(lines) if lines else "(empty)"


def render_output(
    payload: Any,
    *,
    output_format: str = "dict",
    table_columns: Sequence[str] | None = None,
    title: str | None = None,
) -> Any:
    """Return payload unchanged or rendered as table/tree text."""
    normalized = output_format.lower().strip()
    if normalized in {"dict", "json", "raw"}:
        return payload
    rows = payload.get("results") if isinstance(payload, Mapping) else payload
    if normalized == "table":
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            rows = [payload]
        return render_table(
            [row for row in rows if isinstance(row, Mapping)],
            columns=table_columns,
            max_rows=50,
        )
    if normalized == "tree":
        return render_tree(payload, title=title)
    raise ValueError("output_format must be dict, table, or tree")


def _infer_columns(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    preferred = [
        "source",
        "code",
        "name",
        "target_source",
        "target_code",
        "target_display",
        "relationship",
        "match_type",
        "status",
        "resolved_source",
        "resolved_code",
    ]
    keys = list(dict.fromkeys(key for row in rows for key in row))
    ordered = [key for key in preferred if key in keys]
    ordered.extend(key for key in keys if key not in ordered)
    return ordered[:8]


def _render_node(
    value: Any,
    lines: list[str],
    *,
    prefix: str,
    depth: int,
    max_depth: int,
    max_items: int,
) -> None:
    if depth >= max_depth:
        lines.append(f"{prefix}...")
        return
    if isinstance(value, Mapping):
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                lines.append(f"{prefix}... {len(value) - max_items} more")
                break
            if isinstance(item, Mapping | list | tuple):
                lines.append(f"{prefix}{key}:")
                _render_node(
                    item,
                    lines,
                    prefix=f"{prefix}  ",
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_items=max_items,
                )
            else:
                lines.append(f"{prefix}{key}: {_cell(item)}")
        return
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            if index >= max_items:
                lines.append(f"{prefix}... {len(value) - max_items} more")
                break
            if isinstance(item, Mapping | list | tuple):
                lines.append(f"{prefix}-")
                _render_node(
                    item,
                    lines,
                    prefix=f"{prefix}  ",
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_items=max_items,
                )
            else:
                lines.append(f"{prefix}- {_cell(item)}")
        return
    lines.append(f"{prefix}{_cell(value)}")


def _cell(value: Any, *, max_width: int = 36) -> str:
    if value is None:
        text = ""
    elif isinstance(value, list | tuple):
        text = ", ".join(str(item) for item in value[:3])
        if len(value) > 3:
            text += f", ... ({len(value)})"
    elif isinstance(value, Mapping):
        text = "{...}"
    else:
        text = str(value)
    text = " ".join(text.split())
    if len(text) > max_width:
        return text[: max_width - 1] + "..."
    return text
