"""CDC vaccine group metadata (VG.txt), vendored.

VG.txt maps every product CVX code to its vaccine GROUP:
``ShortDesc | CVX | Status | GroupName | GroupCVX`` — e.g.
``DTP|01|Inactive|DTAP|107`` (product 01 groups under the canonical
"unspecified DTaP" code 107) and ``M/R|04|Inactive|MMR|03`` (groups under
MMR itself). The 5th column — the group's own CVX code — was historically
discarded by both loaders (data_setup's cvx_metadata build and
``_engine_base._load_default_cvx_groups``), leaving only group NAMES.

The file is vendored at ``medterm4ds/data/vg.txt`` (258 rows, retrieved
2026-08-26 from CVX_GROUP_SOURCE_URL — the canonical URL constant lives at
``sources.cvx.CVX_GROUP_URL``). Refresh by re-downloading into the package.

Same lazy-load pattern as ``services.cpt_cvx``: parse once, shared,
immutable, no network at runtime or prepare time.
"""
from __future__ import annotations

import threading
from pathlib import Path

__all__ = ["load_cvx_group_rows"]

CVX_GROUP_RETRIEVED = "2026-08-26"

_DATA_FILE = Path(__file__).parent.parent / "data" / "vg.txt"

# (cvx_code, short_desc, status, group_name, group_cvx)
_lock = threading.Lock()
_rows: list[tuple[str, str, str, str, str]] | None = None


def load_cvx_group_rows() -> list[tuple[str, str, str, str, str]]:
    """All VG.txt rows, 5 columns each, codes stripped of CDC padding."""
    global _rows
    if _rows is not None:
        return _rows
    with _lock:
        if _rows is None:
            rows: list[tuple[str, str, str, str, str]] = []
            for line in _DATA_FILE.read_text(encoding="utf-8").splitlines():
                parts = line.split("|")
                if len(parts) < 5:
                    continue
                code = parts[1].strip()
                group_cvx = parts[4].strip()
                if not (code.isdigit() and group_cvx.isdigit()):
                    continue
                rows.append((
                    code,
                    parts[0].strip(),
                    parts[2].strip(),
                    parts[3].strip(),
                    group_cvx,
                ))
            _rows = rows
    return _rows
