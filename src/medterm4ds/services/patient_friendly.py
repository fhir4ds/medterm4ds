"""Batch-first patient-friendly service."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from medterm4ds.core.models import CodeRef, FriendlyNameResult
from medterm4ds.core.normalize import validate_code_nonempty, validate_source_sab
from medterm4ds.engines.base import PatientFriendlyEngine
from medterm4ds.services.resolution import effective_code_refs

_logger = logging.getLogger(__name__)


def get_patient_friendly_names(
    codes: Sequence[CodeRef | tuple[str, str]],
    engine: PatientFriendlyEngine,
    max_depth: int = 5,
    resolve_mode: str = "active_only",
) -> list[FriendlyNameResult]:
    """Resolve patient-friendly names for one or many codes.

    Tuple inputs use the medterm convention `(source, code)` — same as
    ``CodeRef.from_pair``.
    """
    # QC-076/QC-077/QC-082 (MEDIUM): validate max_depth type BEFORE the
    # engine coerces it via int() — string values silently coerced, None
    # leaked a raw TypeError from int(None). Sibling of EC-03 FIX-007
    # (hierarchy) and EC-02 FIX-005 (mapping).
    if not isinstance(max_depth, int) or isinstance(max_depth, bool):
        raise TypeError(
            f"max_depth must be int, got {type(max_depth).__name__}"
        )
    # QC-075/QC-082 (MEDIUM): max_depth < 0 is a programming bug.
    if max_depth < 0:
        raise ValueError("max_depth must be non-negative")
    # QC-075/QC-082 (MEDIUM): max_depth == 0 silently skips the broader
    # walk, returning only exact/same_cui matches (or 'original' for
    # codes with no direct hit). This is a valid use case (caller wants
    # only direct hits), but the silent degradation was the bug — surface
    # a WARNING so the caller knows the broader walk was skipped.
    if max_depth == 0:
        _logger.warning(
            "patient_friendly called with max_depth=0: broader walk is "
            "skipped, only exact/same_cui matches will be returned"
        )
    normalized = [
        item if isinstance(item, CodeRef) else CodeRef.from_pair(item)
        for item in codes
    ]
    # QC-422 (MEDIUM): URI-form source previously produced a success-shaped
    # row whose name was the raw code (CLI/MCP reject the same input). Guard
    # at the service boundary, same as lookup/mapping.
    for ref in normalized:
        validate_source_sab(ref.source)
        validate_code_nonempty(str(ref.code))
    # RS2/PD1 fix (TS-25/TS-39): unknown-code detection UPSTREAM of the
    # strategy dispatch. The two engine paths encoded unknown codes
    # differently — the raw path emitted match_type='none' (the registered
    # RS2 shape) while the prepared path emitted match_type='original' with
    # the identifier as its own display (a success-implying shape that lets
    # void concepts survive keep-originals filters). Detecting unknowns
    # BEFORE dispatch and synthesizing one uniform miss row makes the
    # miss shape path-INDEPENDENT and closes RS2 on both paths at once.
    # Capability-guarded: the PatientFriendlyEngine PROTOCOL guarantees
    # only get_patient_friendly_names — engines without get_code_infos
    # (test doubles, remote adapters) keep their own miss encoding.
    from medterm4ds.services.lookup import get_code_infos

    # Code-review R1: the unknown check must run on the RESOLVED refs,
    # not the inputs — resolve_current maps retired codes to their
    # replacements (TS-33's contract), and testing the raw inputs first
    # would starve the resolution of codes that only exist via
    # replacement.
    effective, _resolutions = effective_code_refs(
        normalized,
        engine=engine,
        resolve_mode=resolve_mode,
    )
    get_infos = getattr(engine, "get_code_infos", None)
    if get_infos is not None:
        try:
            known_mask = [
                info is not None
                for info in get_code_infos(effective, engine=engine)
            ]
        except NotImplementedError:
            known_mask = None
    else:
        known_mask = None
    if known_mask is None:
        return list(
            engine.get_patient_friendly_names(
                effective, max_depth=max_depth
            )
        )
    known_refs = [
        ref
        for ref, known in zip(effective, known_mask, strict=True)
        if known
    ]
    known_by_key = {
        (r.code.source, r.code.code): r
        for r in (
            engine.get_patient_friendly_names(
                known_refs, max_depth=max_depth
            )
            if known_refs
            else []
        )
    }
    # Rebuild in INPUT order (mixed batches interleave hits and misses;
    # C1's per-code isolation contract includes order preservation).
    # Match engine results BY KEY, not position: engines may dedup or
    # reorder internally (the remote adapter collapses duplicate codes).
    results: list[FriendlyNameResult] = []
    for ref, known in zip(effective, known_mask, strict=True):
        if known:
            hit = known_by_key.get((ref.source, ref.code))
            if hit is None:
                # Engine dropped a known code (should not happen; the
                # pre-check established existence). Surface its own miss
                # encoding rather than crash.
                hit = FriendlyNameResult(
                    code=ref,
                    name="",
                    friendly_source=ref.source,
                    match_type="none",
                    match_depth=0,
                    technical_name=None,
                    matched_via=None,
                )
            results.append(hit)
        else:
            results.append(
                FriendlyNameResult(
                    code=ref,
                    # RS2 fix: the identifier must not pose as a display —
                    # the miss row carries an EMPTY name (rendered as null
                    # on the wire); the code/source fields identify it.
                    name="",
                    friendly_source=ref.source,
                    match_type="none",
                    match_depth=0,
                    technical_name=None,
                    matched_via=None,
                )
            )
    return results


def get_patient_friendly_name(
    code: CodeRef | tuple[str, str],
    engine: PatientFriendlyEngine,
    max_depth: int = 5,
    resolve_mode: str = "active_only",
) -> FriendlyNameResult:
    """Resolve one patient-friendly name through the batch contract."""
    results = get_patient_friendly_names(
        [code],
        engine=engine,
        max_depth=max_depth,
        resolve_mode=resolve_mode,
    )
    return results[0]
