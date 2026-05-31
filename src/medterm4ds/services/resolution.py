"""Historical, obsolete, and NDC code resolution services."""

from __future__ import annotations

from collections.abc import Sequence

from medterm4ds.core.models import CodeRef, CodeResolution, Provenance, ProvenanceStep
from medterm4ds.engines.base import LookupEngine, ResolutionEngine

ResolveMode = str


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
    normalized = [
        item if isinstance(item, CodeRef) else CodeRef.from_pair(item)
        for item in codes
    ]
    if resolve_mode == "active_only" and all(ref.source != "NDC" for ref in normalized):
        return normalized, None
    resolutions = resolve_codes(normalized, engine=engine)
    if resolve_mode == "historical":
        return normalized, resolutions
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
