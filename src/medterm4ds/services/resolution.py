"""Historical, obsolete, and NDC code resolution services."""

from __future__ import annotations

from collections.abc import Sequence

from medterm4ds.core.models import CodeRef, CodeResolution, Provenance, ProvenanceStep
from medterm4ds.engines.base import LookupEngine, ResolutionEngine

ResolveMode = str

#: Allowed ``resolve_mode`` values — single source of truth shared by the
#: CLI argparse ``choices=`` and the Python API validation. Found by QC-002
#: (EDGE_CASE MEDIUM): pre-fix, the Python API silently accepted typos and
#: fell through to resolve_current behavior; the CLI validated via argparse
#: but the engine layer didn't, so direct API callers got silent-wrong-answer.
ALLOWED_RESOLVE_MODES: frozenset[str] = frozenset(
    {"active_only", "resolve_current", "historical"}
)


def resolve_codes(
    codes: Sequence[CodeRef | tuple[str, str]],
    engine: ResolutionEngine | LookupEngine,
) -> list[CodeResolution]:
    """Resolve input codes while preserving input order."""
    normalized = [
        item if isinstance(item, CodeRef) else CodeRef.from_pair(item)
        for item in codes
    ]
    resolver = getattr(engine, "resolve_codes", None)
    if resolver is not None:
        return resolver(normalized)
    infos = engine.get_code_infos(normalized)
    return [
        _active_resolution(ref, info)
        if info is not None
        else CodeResolution(
            input=ref,
            resolved=None,
            status="not_found",
            match_type="not_found",
            matched_via=Provenance.from_steps(
                "not_found",
                [ProvenanceStep(op="input", source=ref.source, code=ref.code)],
            ),
        )
        for ref, info in zip(normalized, infos, strict=True)
    ]


def effective_code_refs(
    codes: Sequence[CodeRef | tuple[str, str]],
    *,
    engine: ResolutionEngine | LookupEngine,
    resolve_mode: ResolveMode = "active_only",
) -> tuple[list[CodeRef], list[CodeResolution] | None]:
    """Return code refs to use for downstream work plus optional resolution rows."""
    if resolve_mode not in ALLOWED_RESOLVE_MODES:
        # Fail loud on programming errors (typos, empty string, None) per
        # GLOBAL_RULES "Silent Fallbacks" — silent fallthrough to
        # resolve_current masked the historical/active_only distinction.
        # Found by QC-002 (EDGE_CASE MEDIUM).
        raise ValueError(
            f"resolve_mode must be one of {sorted(ALLOWED_RESOLVE_MODES)!r}, "
            f"got {resolve_mode!r}"
        )
    normalized = [
        item if isinstance(item, CodeRef) else CodeRef.from_pair(item)
        for item in codes
    ]
    if resolve_mode == "active_only" and all(ref.source != "NDC" for ref in normalized):
        return normalized, None
    resolutions = resolve_codes(normalized, engine=engine)
    if resolve_mode == "historical":
        # Historical mode returns the input CodeRef as effective for
        # same-system obsolete codes (e.g. SNOMED->SNOMED where the input
        # IS the historical atom). But for cross-system resolutions like
        # NDC->RXNORM, the input is not an atom of the resolved system at
        # all — using the input NDC as effective while resolved_display
        # carries the RXNORM drug name produces a silent display/code
        # mismatch. Found by QC-119 (DATA_INTEGRITY HIGH): for resolutions
        # where input.source != resolved.source, use the resolved CodeRef
        # as effective so effective and resolved_display stay consistent.
        effective = [
            resolution.input
            if (resolution.resolved is None or resolution.input.source == resolution.resolved.source)
            else resolution.resolved
            for resolution in resolutions
        ]
        return effective, resolutions
    effective = [
        resolution.resolved if resolution.is_resolved and resolution.resolved else resolution.input
        for resolution in resolutions
    ]
    return effective, resolutions


def _active_resolution(ref: CodeRef, info) -> CodeResolution:
    return CodeResolution(
        input=ref,
        resolved=ref,
        status="active",
        match_type="active_exact",
        input_display=info.name,
        resolved_display=info.name,
        input_cui=info.cui,
        resolved_cui=info.cui,
        input_aui=info.aui,
        resolved_aui=info.aui,
        input_suppress=info.suppress,
        resolved_suppress=info.suppress,
        matched_via=Provenance.from_steps(
            "active_exact",
            [
                ProvenanceStep(op="input", source=ref.source, code=ref.code),
                ProvenanceStep(
                    op="active_atom",
                    source=ref.source,
                    code=ref.code,
                    cui=info.cui,
                    aui=info.aui,
                    tty=info.tty,
                    name=info.name,
                ),
            ],
        ),
    )
