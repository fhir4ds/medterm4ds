"""Typed domain records for terminology workflows."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .normalize import normalize_source


@dataclass(frozen=True, order=True)
class CodeRef:
    """A code in a source terminology."""

    source: str
    code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", normalize_source(self.source))
        object.__setattr__(self, "code", str(self.code))

    @classmethod
    def from_pair(cls, pair: tuple[str, str]) -> CodeRef:
        code, source = pair
        return cls(source=source, code=code)

    def as_pair(self) -> tuple[str, str]:
        return (self.code, self.source)


@dataclass(frozen=True)
class CodeInfo:
    """Canonical atom information for one terminology code."""

    code: CodeRef
    name: str | None = None
    cui: str | None = None
    aui: str | None = None
    tty: str | None = None
    suppress: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.code.source,
            "code": self.code.code,
            "name": self.name,
            "cui": self.cui,
            "aui": self.aui,
            "tty": self.tty,
            "suppress": self.suppress,
        }


@dataclass(frozen=True)
class ProvenanceStep:
    """One auditable step in a terminology resolution path."""

    op: str
    source: str | None = None
    code: str | None = None
    target_source: str | None = None
    target_code: str | None = None
    cui: str | None = None
    aui: str | None = None
    tty: str | None = None
    depth: int | None = None
    mode: str | None = None
    name: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"op": self.op}
        for key in (
            "source",
            "code",
            "target_source",
            "target_code",
            "cui",
            "aui",
            "tty",
            "depth",
            "mode",
            "name",
        ):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        if self.metadata:
            data["metadata"] = dict(self.metadata)
        return data


@dataclass(frozen=True)
class Provenance:
    """Structured provenance for how a result was selected."""

    strategy: str
    steps: tuple[ProvenanceStep, ...] = ()

    @classmethod
    def from_steps(cls, strategy: str, steps: Iterable[ProvenanceStep]) -> Provenance:
        return cls(strategy=strategy, steps=tuple(steps))

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "steps": [step.to_dict() for step in self.steps],
        }


@dataclass(frozen=True)
class FriendlyNameResult:
    """Patient-friendly display result for one terminology code."""

    code: CodeRef
    name: str
    friendly_source: str
    match_type: str
    match_depth: int = 0
    technical_name: str | None = None
    matched_via: Provenance | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "code": self.code.code,
            "source": self.code.source,
            "name": self.name,
            "friendly_source": self.friendly_source,
            "match_type": self.match_type,
            "match_depth": self.match_depth,
            "technical_name": self.technical_name,
            "matched_via": self.matched_via.to_dict() if self.matched_via else None,
        }
        return data

    @classmethod
    def from_legacy_dict(cls, row: Mapping[str, Any]) -> FriendlyNameResult:
        code = CodeRef(source=str(row.get("source", "")), code=str(row.get("code", "")))
        friendly_source = str(row.get("friendly_source") or row.get("source") or code.source)
        raw_matched_via = row.get("matched_via")
        provenance = legacy_matched_via_to_provenance(code, row, raw_matched_via)
        return cls(
            code=code,
            name=str(row.get("name") or code.code),
            friendly_source=friendly_source,
            match_type=str(row.get("match_type") or "none"),
            match_depth=int(row.get("match_depth") or 0),
            technical_name=row.get("technical_name"),
            matched_via=provenance,
        )


@dataclass(frozen=True)
class ConceptMapRow:
    """One source-to-target row for terminology mapping exports."""

    source: CodeRef
    target: CodeRef
    target_display: str
    relationship: str
    source_display: str | None = None
    friendly_source: str | None = None
    match_type: str | None = None
    match_depth: int = 0
    matched_via: Provenance | None = None

    @classmethod
    def from_friendly_result(
        cls,
        result: FriendlyNameResult,
        *,
        target_source: str = "PATIENT_FRIENDLY",
    ) -> ConceptMapRow:
        target = CodeRef(
            source=target_source,
            code=f"{result.code.source}:{result.code.code}",
        )
        return cls(
            source=result.code,
            source_display=result.technical_name,
            target=target,
            target_display=result.name,
            relationship=conceptmap_relationship(result.match_type),
            friendly_source=result.friendly_source,
            match_type=result.match_type,
            match_depth=result.match_depth,
            matched_via=result.matched_via,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.source,
            "code": self.source.code,
            "source_display": self.source_display,
            "target_source": self.target.source,
            "target_code": self.target.code,
            "target_display": self.target_display,
            "relationship": self.relationship,
            "friendly_source": self.friendly_source,
            "match_type": self.match_type,
            "match_depth": self.match_depth,
            "matched_via": self.matched_via.to_dict() if self.matched_via else None,
        }


def conceptmap_relationship(match_type: str | None) -> str:
    """Map patient-friendly match types to a small stable relationship vocabulary."""
    if not match_type or match_type == "none":
        return "unmatched"
    if match_type.startswith("broader"):
        return "source-is-narrower-than-target"
    if match_type in {"component", "first_axis", "loinc_common"}:
        return "related-to"
    if match_type == "original":
        return "not-translated"
    return "equivalent"


def legacy_matched_via_to_provenance(
    code: CodeRef,
    row: Mapping[str, Any],
    raw_matched_via: Any,
) -> Provenance:
    """Convert medterm's loose matched_via dict into a structured path."""
    strategy = infer_strategy(code.source, str(row.get("match_type") or "none"), raw_matched_via)
    steps = [
        ProvenanceStep(op="input", source=code.source, code=code.code),
    ]

    if isinstance(raw_matched_via, Mapping):
        target_source = raw_matched_via.get("target_source") or raw_matched_via.get("source")
        target_code = raw_matched_via.get("target_code") or raw_matched_via.get("code")
        snomed_code = raw_matched_via.get("snomed_code")
        parent_code = raw_matched_via.get("parent_code")
        if target_source or target_code:
            steps.append(
                ProvenanceStep(
                    op="cross_reference",
                    source=code.source,
                    code=code.code,
                    target_source=str(target_source) if target_source else None,
                    target_code=str(target_code) if target_code else None,
                )
            )
        if snomed_code:
            steps.append(
                ProvenanceStep(
                    op="cross_reference",
                    source=code.source,
                    code=code.code,
                    target_source="SNOMEDCT_US",
                    target_code=str(snomed_code),
                    depth=_safe_int(raw_matched_via.get("source_match_depth")),
                    mode="broader",
                )
            )
        if parent_code:
            steps.append(
                ProvenanceStep(
                    op="ancestor",
                    source=code.source,
                    code=str(parent_code),
                    depth=_safe_int(row.get("match_depth")),
                )
            )

    if row.get("friendly_source"):
        steps.append(
            ProvenanceStep(
                op="friendly_name",
                source=str(row.get("friendly_source")),
                name=str(row.get("name") or ""),
                depth=_safe_int(row.get("match_depth")),
            )
        )

    return Provenance.from_steps(strategy, steps)


def infer_strategy(source: str, match_type: str, raw_matched_via: Any = None) -> str:
    """Infer a stable strategy label from result fields."""
    if source == "RXNORM":
        return "rxnorm_tty"
    if source == "LNC":
        return "loinc_component" if match_type in {"first_axis", "component"} else "loinc_fallback"
    if source == "CVX":
        return "cvx_group"
    if source == "CPT":
        return "cpt_fallback"
    if source == "SNOMEDCT_US":
        return "snomed_cross_reference" if raw_matched_via else "snomed_fallback"
    if source in {"ICD10CM", "ICD10PCS"} and raw_matched_via:
        return "icd10_snomed_fallback"
    return "default_friendly"


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
