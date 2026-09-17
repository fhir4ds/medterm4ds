"""Exact terminology code lookup services."""

from __future__ import annotations

from collections.abc import Sequence

from medterm4ds.core.models import CodeInfo, CodeRef
from medterm4ds.core.normalize import validate_code_nonempty, validate_source_sab
from medterm4ds.engines.base import LookupEngine
from medterm4ds.services.prepared_primitives import (
    group_codes_by_source,
    preferred_atom_lookup,
)
from medterm4ds.services.resolution import effective_code_refs


def get_code_infos(
    codes: Sequence[CodeRef | tuple[str, str]],
    engine: LookupEngine,
    *,
    resolve_mode: str = "active_only",
) -> list[CodeInfo | None]:
    """Look up canonical atom info for one or many codes.

    Tuple inputs use the medterm convention `(source, code)` — same as
    ``CodeRef.from_pair``. (The `(source, code)` order matches the rest of the
    public API: ``mt.lookup("SNOMEDCT_US", "44054006")``.)

    ``resolve_mode='historical'`` returns the historical atom's info (so the
    caller sees what they queried, even when the code is obsolete); ``'resolve_current'``
    returns the active replacement's info. Found by QC-017 (DATA_INTEGRITY
    HIGH): pre-fix, ``effective_code_refs`` returned ``(normalized, resolutions)``
    for historical mode where ``normalized`` was the UNCHANGED original ref,
    and the ``resolutions`` (containing the resolved display) was discarded —
    making historical and resolve_current functionally identical to active_only.
    """
    normalized = [
        item if isinstance(item, CodeRef) else CodeRef.from_pair(item)
        for item in codes
    ]
    # QC-422 (MEDIUM): the CLI and MCP boundaries reject empty/URI-form
    # source and empty code, but the Python facade funneled the same inputs
    # here and returned success-shaped null data. Guard at the service
    # boundary so every surface inherits identical diagnostics (helpers in
    # core.normalize are the single source of truth for the messages).
    # QC01-001: in BATCH inputs, isolate the bad element instead of
    # rejecting the whole call — the FHIR batch surface gives every entry
    # its own outcome (§3.7), and a 1000-row batch must not die on row 501.
    # Invalid elements become empty-code refs (not-found rows) with a
    # WARNING naming the first offender; single-code calls keep the hard
    # ValueError (programming-error signal).
    if len(normalized) == 1:
        validate_source_sab(normalized[0].source)
        validate_code_nonempty(str(normalized[0].code))
    else:
        def _is_invalid(ref: CodeRef) -> bool:
            try:
                validate_source_sab(ref.source)
                validate_code_nonempty(str(ref.code))
            except ValueError:
                return True
            return False

        invalid_count = 0
        first_invalid: CodeRef | None = None
        kept: list[CodeRef] = []
        for ref in normalized:
            if _is_invalid(ref):
                invalid_count += 1
                if first_invalid is None:
                    first_invalid = ref
                kept.append(CodeRef(source=ref.source, code=""))
            else:
                kept.append(ref)
        if invalid_count:
            import logging

            logging.getLogger(__name__).warning(
                "get_code_infos: %d/%d batch elements invalid and returned "
                "as not-found rows (first: source=%r code=%r)",
                invalid_count, len(normalized),
                first_invalid.source, first_invalid.code,
            )
            normalized = kept
    effective, resolutions = effective_code_refs(
        normalized,
        engine=engine,
        resolve_mode=resolve_mode,
    )
    if resolutions is None:
        # active_only fast-path: no resolution computed.
        return engine.get_code_infos(effective)

    # historical / resolve_current / NDC: build CodeInfo from resolution data.
    # For each input ref, prefer the resolution's display fields over the
    # (possibly-None) active-atom lookup. historical uses the input atom
    # (what the caller queried); resolve_current uses the resolved replacement.
    infos = engine.get_code_infos(effective)
    out: list[CodeInfo | None] = []
    tty_fixups: list[tuple[int, CodeRef]] = []
    for ref, info, resolution in zip(normalized, infos, resolutions, strict=True):
        if resolution is None or resolution.status == "not_found":
            out.append(info)
            continue
        use_resolved = resolution.is_resolved and (
            resolve_mode == "resolve_current"
            # QC-401 (HIGH): a CROSS-SOURCE resolution (input.source !=
            # resolved.source — NDC→RXNORM is the production case) must use
            # the resolved atom's display regardless of mode. The resolved
            # system has no atom for the input code, so the historical
            # fallthrough's input_display is the raw NDC string — the record
            # then echoes the 11-digit code as its own name while
            # cui/aui/tty/suppress come from the resolved RXNORM atom
            # (internally inconsistent; display-is-never-a-raw-code rule).
            # Same cross-source semantics effective_code_refs applies for
            # historical mode (QC-119).
            or (
                resolution.resolved is not None
                and resolution.input.source != resolution.resolved.source
            )
        )
        if use_resolved:
            target_ref = resolution.resolved or ref
            display = resolution.resolved_display
            cui = resolution.resolved_cui
            aui = resolution.resolved_aui
            suppress = resolution.resolved_suppress
            # CR-056: the resolved atom's TTY is fetched in ONE batch after
            # the loop — the per-code engine.get_code_infos([target_ref])
            # round-trip made resolve paths N+1.
            tty = info.tty if (info is not None and target_ref == ref) else None
        else:
            # historical (or ambiguous/duplicate fallthrough): show the
            # input atom's display from the resolution record.
            target_ref = ref
            display = resolution.input_display
            cui = resolution.input_cui
            aui = resolution.input_aui
            suppress = resolution.input_suppress
            tty = None  # resolution does not carry input TTY
        if display is None and cui is None and info is None:
            out.append(None)
            continue
        out.append(
            CodeInfo(
                code=target_ref,
                name=display if display is not None else (info.name if info else None),
                cui=cui if cui is not None else (info.cui if info else None),
                aui=aui if aui is not None else (info.aui if info else None),
                tty=tty if tty is not None else (info.tty if info else None),
                suppress=suppress if suppress is not None else (info.suppress if info else None),
            )
        )
        if use_resolved and target_ref != ref:
            tty_fixups.append((len(out) - 1, target_ref))
    if tty_fixups:
        from dataclasses import replace

        fetched = engine.get_code_infos([ref for _, ref in tty_fixups])
        for (idx, _ref), f_info in zip(tty_fixups, fetched, strict=True):
            # Best-effort, matching the old inline fetch: no info or no TTY
            # on the resolved atom leaves the input atom's fallback in place.
            if f_info is not None and f_info.tty is not None and out[idx] is not None:
                out[idx] = replace(out[idx], tty=f_info.tty)
    return out


def get_code_info(
    code: CodeRef | tuple[str, str],
    engine: LookupEngine,
    *,
    resolve_mode: str = "active_only",
) -> CodeInfo | None:
    """Look up one code through the batch contract."""
    return get_code_infos([code], engine=engine, resolve_mode=resolve_mode)[0]


def get_code_infos_prepared(
    codes: Sequence[CodeRef | tuple[str, str]],
    con,
) -> list[CodeInfo | None]:
    """Look up preferred atom info directly from prepared ``mt4ds.best_atoms``."""
    normalized = [
        item if isinstance(item, CodeRef) else CodeRef.from_pair(item)
        for item in codes
    ]
    lookups: dict[tuple[str, str], CodeInfo] = {}
    for source, source_codes in group_codes_by_source(normalized).items():
        for code, info in preferred_atom_lookup(con, source, source_codes).items():
            lookups[(source, code)] = info
    return [lookups.get((code.source, code.code)) for code in normalized]


def get_code_info_prepared(
    code: CodeRef | tuple[str, str],
    con,
) -> CodeInfo | None:
    """Look up one preferred atom from prepared ``mt4ds.best_atoms``."""
    return get_code_infos_prepared([code], con)[0]
