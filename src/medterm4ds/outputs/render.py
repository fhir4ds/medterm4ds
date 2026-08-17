"""Compact ASCII renderers for CLI and MCP output."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

# Cap on inferred (non-explicit) table columns; dropping columns beyond this
# is surfaced via a "... N more columns" suffix (QC-370).
_MAX_INFERRED_COLUMNS = 8


def render_table(
    rows: Sequence[Mapping[str, Any]],
    *,
    columns: Sequence[str] | None = None,
    max_width: int = 36,
    max_rows: int | None = None,
) -> str:
    """Render dictionaries as a compact ASCII table.

    When ``columns`` is not supplied, at most ``_MAX_INFERRED_COLUMNS``
    columns render (preferred keys first); a ``... N more columns`` suffix
    mirrors the row-truncation marker so dropped columns are visible
    (QC-370).
    """
    visible_rows = list(rows[:max_rows] if max_rows is not None else rows)
    if not visible_rows:
        return "(no rows)"
    selected_columns = list(columns) if columns else _infer_columns(visible_rows)
    hidden_columns = 0
    if not columns and len(selected_columns) > _MAX_INFERRED_COLUMNS:
        hidden_columns = len(selected_columns) - _MAX_INFERRED_COLUMNS
        selected_columns = selected_columns[:_MAX_INFERRED_COLUMNS]
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
    if hidden_columns:
        suffix.append(f"... {hidden_columns} more columns")
    return "\n".join([header, divider, *body, *suffix])


def render_tree(
    data: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    title: str | None = None,
    max_depth: int | None = 4,
    max_items: int | None = 20,
) -> str:
    """Render nested dictionaries/lists as a compact ASCII tree.

    ``max_depth``/``max_items`` of ``None`` render the full structure
    (file exports pass None so record data is never clipped — QC-358/369);
    the defaults keep interactive stdout compact.
    """
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
    max_rows: int | None = 50,
) -> Any:
    """Return payload unchanged or rendered as table/tree text.

    ``max_rows`` caps the table render (None renders every row). Callers
    whose rows are all load-bearing — e.g. optimize rules, where the CLI
    table is untruncated — pass ``max_rows=None`` for cross-surface parity
    (QC-203/QC-210).
    """
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
            max_rows=max_rows,
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
    return ordered


def _render_node(
    value: Any,
    lines: list[str],
    *,
    prefix: str,
    depth: int,
    max_depth: int | None,
    max_items: int | None,
) -> None:
    if max_depth is not None and depth >= max_depth:
        lines.append(f"{prefix}...")
        return
    if isinstance(value, Mapping):
        for index, (key, item) in enumerate(value.items()):
            if max_items is not None and index >= max_items:
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
            if max_items is not None and index >= max_items:
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
