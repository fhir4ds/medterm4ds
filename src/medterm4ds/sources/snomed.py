"""SNOMED CT isa/top-level guard rules and target routing."""

from __future__ import annotations

from .base import DefaultStrategy

# Sources that can fall back to SNOMED for patient-friendly resolution
SNOMED_FALLBACK_SOURCES: frozenset[str] = frozenset({
    "ICD10CM",
    "ICD10PCS",
    "LNC",
    "HCPCS",
    "CPT",
})

# When mapping SNOMED to other sources, this is the priority order
SNOMED_TARGET_PRIORITY: dict[str, int] = {
    "ICD10CM": 0,
    "ICD10PCS": 1,
    "LNC": 2,
    "CPT": 3,
    "HCPCS": 4,
}

# Depth threshold: SNOMED codes at this depth or shallower are "top-level"
SNOMED_TOP_LEVEL_GUARD_DEPTH: int = 3

# Match types exempt from the top-level guard
SNOMED_TOP_LEVEL_GUARD_EXEMPT_MATCH_TYPES: frozenset[str] = frozenset({"same_cui"})

# UMLS semantic types (TUI) → target source vocabularies that are semantically
# compatible. Used by the SNOMED crosswalk to filter candidates so a SNOMED
# concept routes to a clinically appropriate target (e.g., Pharmacologic
# Substance → RXNORM, not LNC).
#
# CVX is intentionally absent: vaccines share generic substance TUIs and are
# detected via crosswalk existence instead.
#
# Living here in sources/snomed.py (alongside the other SNOMED routing data)
# rather than at the engine layer. A future enhancement could expose this as
# a CSV/JSON data file for deployment-specific overrides; for now, editing
# this dict is the extension point.
SNOMED_TUI_TARGETS: dict[str, tuple[str, ...]] = {
    # Conditions → ICD10CM
    "T019": ("ICD10CM",),  # Congenital Abnormality
    "T020": ("ICD10CM",),  # Acquired Abnormality
    "T037": ("ICD10CM",),  # Injury or Poisoning
    "T046": ("ICD10CM",),  # Pathologic Function
    "T047": ("ICD10CM",),  # Disease or Syndrome
    "T048": ("ICD10CM",),  # Mental or Behavioral Dysfunction
    "T049": ("ICD10CM",),  # Cell or Molecular Dysfunction
    "T190": ("ICD10CM",),  # Anatomical Abnormality
    "T191": ("ICD10CM",),  # Neoplastic Process
    # Labs → LNC
    "T034": ("LNC",),      # Laboratory or Test Result
    "T059": ("LNC",),      # Laboratory Procedure
    # Substances / Drugs → RXNORM
    # Restrictive: only pharmacologic-substance or clinical-drug TUIs trigger
    # RXNORM routing. Endogenous proteins (T116 alone, e.g., the PMS2 gene
    # product) and pure organic chemicals without pharmacologic semantics are
    # excluded — they may share a CUI with a drug but aren't drugs themselves.
    "T121": ("RXNORM",),   # Pharmacologic Substance
    "T123": ("RXNORM",),   # Biologically Active Substance
    "T200": ("RXNORM",),   # Clinical Drug
    # Procedures → CPT (and ICD10PCS for surgical, priority picks ICD10PCS first)
    "T060": ("CPT", "ICD10PCS"),  # Diagnostic Procedure
    "T061": ("CPT", "ICD10PCS"),  # Therapeutic or Preventive Procedure
    "T062": ("CPT", "ICD10PCS"),  # Research Activity
    "T063": ("CPT", "ICD10PCS"),  # Molecular Biology Research Technique
}


class SnomedStrategy(DefaultStrategy):
    """SNOMED CT source strategy with isa hierarchy and top-level guard."""

    def __init__(self) -> None:
        super().__init__(source="SNOMEDCT_US")

    def hierarchy_edge_sql(self) -> str | None:
        """SNOMED uses RELA='isa' with PAR/CHD handling."""
        return "r.REL = 'PAR' AND COALESCE(r.RELA, 'isa') IN ('isa', 'inverse_isa')"

    def friendly_strategy_rows(self) -> list[dict[str, object]]:
        """SNOMED: route to target sources first, then guarded SNOMED fallback."""
        return [
            {
                "phase": "snomed_to_target_native_hierarchy",
                "walk_kind": "mapped_from",
                "target_source": "ICD10CM",
                "target_tty": None,
                "match_type": "same_cui",
                "priority": 0,
                "max_depth": 0,
                "stop_on_hit": False,
                "guard": None,
            },
            {
                "phase": "snomed_to_target_native_hierarchy",
                "walk_kind": "mapped_from",
                "target_source": "ICD10PCS",
                "target_tty": None,
                "match_type": "same_cui",
                "priority": 1,
                "max_depth": 0,
                "stop_on_hit": False,
                "guard": None,
            },
            {
                "phase": "snomed_to_target_native_hierarchy",
                "walk_kind": "mapped_from",
                "target_source": "LNC",
                "target_tty": None,
                "match_type": "same_cui",
                "priority": 2,
                "max_depth": 0,
                "stop_on_hit": False,
                "guard": None,
            },
            {
                "phase": "snomed_to_target_native_hierarchy",
                "walk_kind": "mapped_from",
                "target_source": "CPT",
                "target_tty": None,
                "match_type": "same_cui",
                "priority": 3,
                "max_depth": 0,
                "stop_on_hit": False,
                "guard": None,
            },
            {
                "phase": "snomed_to_target_native_hierarchy",
                "walk_kind": "mapped_from",
                "target_source": "HCPCS",
                "target_tty": None,
                "match_type": "same_cui",
                "priority": 4,
                "max_depth": 0,
                "stop_on_hit": False,
                "guard": None,
            },
            {
                "phase": "direct_snomed_guarded_walk",
                "walk_kind": "isa",
                "target_source": "MEDLINEPLUS",
                "target_tty": None,
                "match_type": "broader",
                "priority": 5,
                "max_depth": 5,
                "stop_on_hit": True,
                "guard": "snomed_top_level",
            },
            {
                "phase": "direct_snomed_guarded_walk",
                "walk_kind": "isa",
                "target_source": "CHV",
                "target_tty": None,
                "match_type": "broader",
                "priority": 6,
                "max_depth": 5,
                "stop_on_hit": True,
                "guard": "snomed_top_level",
            },
            {
                "phase": "original",
                "walk_kind": "none",
                "target_source": None,
                "target_tty": None,
                "match_type": "original",
                "priority": 99,
                "max_depth": 0,
                "stop_on_hit": True,
                "guard": None,
            },
        ]

    def atom_display_rank(self) -> str:
        """SNOMED uses PT/FN/SY ranking with shorter display preferred."""
        return (
            "CASE upper(TTY) "
            "WHEN 'PT' THEN 0 "
            "WHEN 'SCD' THEN 1 "
            "WHEN 'FN' THEN 2 "
            "WHEN 'SY' THEN 3 "
            "ELSE 4 END, "
            "LENGTH(STR), "
            "AUI"
        )
