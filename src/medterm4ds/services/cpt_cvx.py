"""CDC CPT↔CVX crosswalk (single-best vaccine mapping).

The CDC publishes a small table (``cpt.txt``) mapping each vaccine-related
CPT administration code to its best CVX code(s) — one target for almost
every CPT; 10 CPTs list two (the CDC file itself carries both, e.g. 90700
→ CVX 166 and 167):
``CPT | CPT description | (empty) | CVX short name | CVX | (empty) | last
updated | row id``. UMLS links CPT and CVX only sporadically via shared CUIs,
so this table is the authoritative crosswalk for the vaccine domain.

The file is vendored at ``medterm4ds/data/cpt_cvx.txt`` (163 rows, retrieved
2026-08-26 from CPT_CVX_SOURCE_URL) — package data, so the crosswalk works
on any existing database with no rebuild. Refresh by re-downloading the file
into the package and bumping CPT_CVX_RETRIEVED.

Rows carry ``match_type="cdc_cpt_cvx"`` so they stay distinguishable from
same-CUI engine rows; the mapping service prefers CDC rows on (source,
target) conflicts (single-best semantics) and keeps extra engine rows.
"""
from __future__ import annotations

import threading
from collections.abc import Sequence
from pathlib import Path

from medterm4ds.core.models import CodeMapping, CodeRef

__all__ = ["get_cpt_cvx_mappings", "CPT_CVX_SOURCE_URL"]

CPT_CVX_SOURCE_URL: str = (
    "https://www2.cdc.gov/vaccines/iis/iisstandards/downloads/cpt.txt"
)
CPT_CVX_RETRIEVED = "2026-08-26"

_DATA_FILE = Path(__file__).parent.parent / "data" / "cpt_cvx.txt"

# (cpt_code, cpt_desc, cvx_code, cvx_name) — parsed once, shared, immutable.
_lock = threading.Lock()
_pairs: list[tuple[str, str, str, str]] | None = None


def _load_pairs() -> list[tuple[str, str, str, str]]:
    global _pairs
    if _pairs is not None:
        return _pairs
    with _lock:
        if _pairs is None:
            pairs: list[tuple[str, str, str, str]] = []
            for line in _DATA_FILE.read_text(encoding="utf-8").splitlines():
                parts = line.split("|")
                if len(parts) < 5:
                    continue
                # CDC space-pads the code columns ("90281     ", "86        ").
                cpt = parts[0].strip()
                cvx = parts[4].strip()
                if not (cpt.isdigit() and cvx.isdigit()):
                    continue
                pairs.append((cpt, parts[1].strip(), cvx, parts[3].strip()))
            _pairs = pairs
    return _pairs


def get_cpt_cvx_mappings(
    codes: Sequence[CodeRef],
    *,
    target_sources: Sequence[str],
) -> list[CodeMapping]:
    """CDC CPT↔CVX mappings for the given codes.

    Forward (CPT→CVX) returns the CDC-listed target(s) per code — one for
    almost all CPTs, two for the 10 codes where the CDC table lists a pair.
    Reverse (CVX→CPT) is the one-to-many inverse: every CPT whose listed
    CVX is the input code. Only fires when the input source and a requested
    target source form the {CPT, CVX} pair; any other combination returns
    [] and the caller falls through to the engine path unchanged.
    """
    targets = {t.upper() for t in target_sources}
    results: list[CodeMapping] = []
    for ref in codes:
        source = ref.source.upper()
        if source == "CPT" and "CVX" in targets:
            rows = _forward(ref.code)
        elif source == "CVX" and "CPT" in targets:
            rows = _reverse(ref.code)
        else:
            continue
        for cpt, cpt_desc, cvx, cvx_name in rows:
            if source == "CPT":
                mapping = CodeMapping(
                    source=CodeRef(source="CPT", code=cpt),
                    target=CodeRef(source="CVX", code=cvx),
                    relationship="equivalent",
                    match_type="cdc_cpt_cvx",
                    source_display=cpt_desc or None,
                    target_display=cvx_name or None,
                )
            else:
                mapping = CodeMapping(
                    source=CodeRef(source="CVX", code=cvx),
                    target=CodeRef(source="CPT", code=cpt),
                    relationship="equivalent",
                    match_type="cdc_cpt_cvx",
                    source_display=cvx_name or None,
                    target_display=cpt_desc or None,
                )
            results.append(mapping)
    return results


def _forward(cpt_code: str) -> list[tuple[str, str, str, str]]:
    cpt_code = cpt_code.strip()
    return [
        (cpt, cpt_desc, cvx, cvx_name)
        for cpt, cpt_desc, cvx, cvx_name in _load_pairs()
        if cpt == cpt_code
    ]


def _reverse(cvx_code: str) -> list[tuple[str, str, str, str]]:
    cvx_code = cvx_code.strip()
    return [
        (cpt, cpt_desc, cvx, cvx_name)
        for cpt, cpt_desc, cvx, cvx_name in _load_pairs()
        if cvx == cvx_code
    ]
