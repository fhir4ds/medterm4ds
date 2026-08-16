"""Typed domain records for terminology workflows."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .display import format_patient_friendly_name
from .normalize import normalize_source


@dataclass(frozen=True, order=True)
class CodeRef:
    """A code in a source terminology."""

    source: str
    code: str

    def __post_init__(self) -> None:
        # Per GLOBAL_RULES "Silent Fallbacks": programming bugs MUST propagate.
        # ``str(None) == 'None'`` would silently turn a None code into the
        # literal string 'None', producing a misleading 'not found' instead of
        # a type error. Found by QC-003 (EDGE_CASE LOW). Same shape applies to
        # ``source`` (str(None) is 'None', not a real SAB).
        if self.source is None:
            raise TypeError("CodeRef.source must be a string, got None")
        if self.code is None:
            raise TypeError("CodeRef.code must be a string, got None")
        object.__setattr__(self, "source", normalize_source(self.source))
        object.__setattr__(self, "code", str(self.code))

    @classmethod
    def from_pair(cls, pair: tuple[str, str]) -> CodeRef:
        """Construct from a ``(source, code)`` tuple.

        Tuple order is ``(source, code)`` — same as the dataclass field order,
        same as the Terminology facade, same as FHIR Coding ``{system, code}``.
        Historically this method accepted ``(code, source)`` (the legacy
        medterm convention); that ambiguity was removed in v0.0.1 because it
        caused silent source/code swaps when refactoring between tuple and
        CodeRef forms.
        """
        source, code = pair
        return cls(source=source, code=code)

    def as_pair(self) -> tuple[str, str]:
        """Return ``(source, code)`` tuple (matches from_pair order)."""
        return (self.source, self.code)


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
class CodeResolution:
    """Resolution from an input code to the code that should be used for work."""

    input: CodeRef
    resolved: CodeRef | None
    status: str
    match_type: str
    input_display: str | None = None
    resolved_display: str | None = None
    input_cui: str | None = None
    resolved_cui: str | None = None
    input_aui: str | None = None
    resolved_aui: str | None = None
    input_suppress: str | None = None
    resolved_suppress: str | None = None
    replacement_relationship: str | None = None
    normalized_code: str | None = None
    candidates: tuple[CodeRef, ...] = ()
    matched_via: Provenance | None = None

    @property
    def is_resolved(self) -> bool:
        return self.resolved is not None and self.status not in {"not_found", "ambiguous"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.input.source,
            "code": self.input.code,
            "resolved_source": self.resolved.source if self.resolved else None,
            "resolved_code": self.resolved.code if self.resolved else None,
            "status": self.status,
            "match_type": self.match_type,
            "input_display": self.input_display,
            "resolved_display": self.resolved_display,
            "input_cui": self.input_cui,
            "resolved_cui": self.resolved_cui,
            "input_aui": self.input_aui,
            "resolved_aui": self.resolved_aui,
            "input_suppress": self.input_suppress,
            "resolved_suppress": self.resolved_suppress,
            "replacement_relationship": self.replacement_relationship,
            "normalized_code": self.normalized_code,
            "candidates": [
                {"source": candidate.source, "code": candidate.code}
                for candidate in self.candidates
            ],
            "matched_via": self.matched_via.to_dict() if self.matched_via else None,
        }


@dataclass(frozen=True)
class SourceStats:
    """Inventory statistics for one terminology source."""

    source: str
    code_count: int
    atom_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", normalize_source(self.source))

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "code_count": self.code_count,
            "atom_count": self.atom_count,
        }


@dataclass(frozen=True)
class NameSearchResult:
    """One terminology name search result."""

    code: CodeRef
    name: str
    cui: str | None = None
    aui: str | None = None
    tty: str | None = None
    match_type: str = "contains"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.code.source,
            "code": self.code.code,
            "name": self.name,
            "cui": self.cui,
            "aui": self.aui,
            "tty": self.tty,
            "match_type": self.match_type,
        }


@dataclass(frozen=True)
class CodeMapping:
    """One source-to-target terminology mapping."""

    source: CodeRef
    target: CodeRef
    relationship: str
    match_type: str
    match_depth: int = 0
    source_display: str | None = None
    target_display: str | None = None
    source_cui: str | None = None
    target_cui: str | None = None
    source_aui: str | None = None
    target_aui: str | None = None
    target_tty: str | None = None
    matched_via: Provenance | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.source,
            "code": self.source.code,
            "source_display": self.source_display,
            "target_source": self.target.source,
            "target_code": self.target.code,
            "target_display": self.target_display,
            "relationship": self.relationship,
            "match_type": self.match_type,
            "match_depth": self.match_depth,
            "source_cui": self.source_cui,
            "target_cui": self.target_cui,
            "source_aui": self.source_aui,
            "target_aui": self.target_aui,
            "target_tty": self.target_tty,
            "matched_via": self.matched_via.to_dict() if self.matched_via else None,
        }


@dataclass(frozen=True)
class CodeRelation:
    """One hierarchical relationship between terminology codes."""

    source: CodeRef
    target: CodeRef
    relationship: str
    depth: int = 1
    source_display: str | None = None
    target_display: str | None = None
    rel: str | None = None
    rela: str | None = None
    source_cui: str | None = None
    target_cui: str | None = None
    source_aui: str | None = None
    target_aui: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.source,
            "code": self.source.code,
            "source_display": self.source_display,
            "target_source": self.target.source,
            "target_code": self.target.code,
            "target_display": self.target_display,
            "relationship": self.relationship,
            "depth": self.depth,
            "rel": self.rel,
            "rela": self.rela,
            "source_cui": self.source_cui,
            "target_cui": self.target_cui,
            "source_aui": self.source_aui,
            "target_aui": self.target_aui,
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

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", format_patient_friendly_name(str(self.name)))

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
            relationship=conceptmap_relationship(
                result.match_type, match_depth=result.match_depth
            ),
            friendly_source=result.friendly_source,
            match_type=result.match_type,
            match_depth=result.match_depth,
            matched_via=result.matched_via,
        )

    @classmethod
    def from_mapping(cls, mapping: CodeMapping) -> ConceptMapRow:
        """Build a ConceptMap row from a source-to-target mapping."""
        return cls(
            source=mapping.source,
            source_display=mapping.source_display,
            target=mapping.target,
            target_display=mapping.target_display or mapping.target.code,
            relationship=mapping.relationship,
            friendly_source=None,
            match_type=mapping.match_type,
            match_depth=mapping.match_depth,
            matched_via=mapping.matched_via,
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


@dataclass(frozen=True)
class OptimizeRule:
    """One include/exclude rule for a compact valueset."""

    include: CodeRef
    exclude: tuple[CodeRef, ...] = ()
    covered_codes: tuple[CodeRef, ...] = ()
    excluded_codes: tuple[CodeRef, ...] = ()

    def to_dict(self, *, include_codes: bool = False) -> dict[str, Any]:
        row: dict[str, Any] = {
            "include_source": self.include.source,
            "include": self.include.code,
            "exclude": [code.code for code in self.exclude],
        }
        if include_codes:
            row["covered_codes"] = [
                {"source": code.source, "code": code.code}
                for code in self.covered_codes
            ]
            row["excluded_codes"] = [
                {"source": code.source, "code": code.code}
                for code in self.excluded_codes
            ]
        return row


@dataclass(frozen=True)
class OptimizeResult:
    """Valueset optimization output."""

    source: str
    relationship: str
    rules: tuple[OptimizeRule, ...]
    original_count: int
    optimized_count: int
    reduction: float
    strategy: str = "greedy_hierarchy"

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", normalize_source(self.source))

    def to_dict(self, *, include_codes: bool = False) -> dict[str, Any]:
        return {
            "source": self.source,
            "relationship": self.relationship,
            "strategy": self.strategy,
            "original_count": self.original_count,
            "optimized_count": self.optimized_count,
            "reduction": self.reduction,
            "rules": [
                rule.to_dict(include_codes=include_codes)
                for rule in self.rules
            ],
        }


# Match types that represent a depth-0 self-hit / exact / same-CUI match
# (semantically equivalent to the source concept). All other depth>0
# hierarchical / TTY-traversal / fallback match types are narrower-than-target.
# Found by QC-074/QC-081/QC-094/QC-095 (CRITICAL x3 + HIGH): pre-fix,
# conceptmap_relationship only checked match_type.startswith('broader'),
# mislabeling snomed_fallback / snomed_to_target_* / group depth>0 /
# ingredient depth>0 / cvx_group as 'equivalent' (100K+ production rows).
_EQUIVALENT_MATCH_TYPES: frozenset[str] = frozenset({
    "exact",
    "same_cui",
})


def conceptmap_relationship(match_type: str | None, *, match_depth: int = 0) -> str:
    """Map patient-friendly match types to a small stable relationship vocabulary.

    Dispatches on the (match_type, match_depth) tuple:

      * None / 'none' -> 'unmatched'
      * 'original' -> 'not-translated' (no translation in target system)
      * 'component' / 'first_axis' / 'loinc_common' -> 'related-to'
        (related but not equivalent)
      * depth==0 self-hits ('exact', 'same_cui', 'group', 'ingredient')
        -> 'equivalent'
      * ALL depth>0 hierarchical / TTY-traversal / cross-source fallback
        match types ('broader*', 'snomed_fallback', 'snomed_to_target_*',
        'group' at depth>0, 'ingredient' at depth>0, 'cvx_group') ->
        'source-is-narrower-than-target' (the source is a specific concept
        that maps to a broader / generic / family-level patient-friendly
        name)
    """
    if not match_type or match_type == "none":
        return "unmatched"
    if match_type.startswith("broader"):
        return "source-is-narrower-than-target"
    if match_type in {"component", "first_axis", "loinc_common"}:
        return "related-to"
    if match_type == "original":
        return "not-translated"
    # depth>0 always means the friendly name is broader (ancestor, generic
    # group, or disease family). snomed_fallback / snomed_to_target_* /
    # cvx_group always carry depth>0 semantics by construction.
    if match_depth > 0:
        return "source-is-narrower-than-target"
    # depth==0 with a depth-self-hit match type (exact, same_cui, group,
    # ingredient) is a true equivalence.
    if match_type in _EQUIVALENT_MATCH_TYPES:
        return "equivalent"
    # 'group'/'ingredient'/'cvx_group' at depth==0 are self-hits (TTY
    # traversal that landed on the same concept) -> equivalent.
    if match_type in {"group", "ingredient", "cvx_group"}:
        return "equivalent"
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
