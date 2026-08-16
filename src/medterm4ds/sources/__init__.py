"""Source strategy registry for medterm4ds.

Each UMLS source has a strategy that defines its hierarchy edges,
patient-friendly resolution rules, and atom display ranking.
"""

from __future__ import annotations

from .base import (
    BROAD_CHV_NAMES,
    BROAD_MEDLINEPLUS_NAMES,
    DefaultStrategy,
    LOINC_CLASS_RELA,
    RELA_HIERARCHY_CHILD_SIDE,
    RELA_HIERARCHY_PARENT_SIDE,
    SourceStrategy,
)
from .cpt_hcpcs import CptStrategy, HcpcsStrategy
from .cvx import CVX_GROUP_URL, CvxStrategy
from .generic import GenericStrategy
from .icd import PREFIX_HIERARCHY_SOURCES, IcdStrategy
from .loinc import BLACKLIST_LOINC, LoincStrategy
from .rxnorm import (
    RXNORM_BASE_TTY_PRIORITY,
    RXNORM_GROUP_TTYS,
    RXNORM_KNOWN_TTYS,
    RXNORM_TTY_TOPOLOGY,
    RxNormStrategy,
    compute_tty_paths,
    find_tty_path,
)
from .snomed import (
    SNOMED_FALLBACK_SOURCES,
    SNOMED_TARGET_PRIORITY,
    SNOMED_TOP_LEVEL_GUARD_DEPTH,
    SNOMED_TOP_LEVEL_GUARD_EXEMPT_MATCH_TYPES,
    SnomedStrategy,
)

# ---------------------------------------------------------------------------
# Strategy registry -- maps UMLS SAB names to their strategy instances
# ---------------------------------------------------------------------------

SOURCE_STRATEGIES: dict[str, SourceStrategy] = {
    "RXNORM": RxNormStrategy(),
    "SNOMEDCT_US": SnomedStrategy(),
    "ICD10CM": IcdStrategy("ICD10CM"),
    "ICD10PCS": IcdStrategy("ICD10PCS"),
    "HCPCS": HcpcsStrategy(),
    "CPT": CptStrategy(),
    "LNC": LoincStrategy(),
    "CVX": CvxStrategy(),
    "ATC": GenericStrategy("ATC"),
    "MSH": GenericStrategy("MSH"),
}


def get_strategy(source: str) -> SourceStrategy:
    """Return strategy for *source*, defaulting to generic."""
    return SOURCE_STRATEGIES.get(source, GenericStrategy(source))


__all__ = [
    # Registry
    "SOURCE_STRATEGIES",
    "get_strategy",
    # Protocol and base
    "SourceStrategy",
    "DefaultStrategy",
    # Broad name sets
    "BROAD_CHV_NAMES",
    "BROAD_MEDLINEPLUS_NAMES",
    # Hierarchy edge RELA vocabulary (canonical — see sources/base.py)
    "LOINC_CLASS_RELA",
    "RELA_HIERARCHY_CHILD_SIDE",
    "RELA_HIERARCHY_PARENT_SIDE",
    # RxNorm
    "RXNORM_TTY_TOPOLOGY",
    "RXNORM_KNOWN_TTYS",
    "RXNORM_GROUP_TTYS",
    "RXNORM_BASE_TTY_PRIORITY",
    "RxNormStrategy",
    "compute_tty_paths",
    "find_tty_path",
    # SNOMED
    "SNOMED_FALLBACK_SOURCES",
    "SNOMED_TARGET_PRIORITY",
    "SNOMED_TOP_LEVEL_GUARD_DEPTH",
    "SNOMED_TOP_LEVEL_GUARD_EXEMPT_MATCH_TYPES",
    "SnomedStrategy",
    # ICD
    "PREFIX_HIERARCHY_SOURCES",
    "IcdStrategy",
    # LOINC
    "BLACKLIST_LOINC",
    "LoincStrategy",
    # CPT/HCPCS
    "CptStrategy",
    "HcpcsStrategy",
    # CVX
    "CVX_GROUP_URL",
    "CvxStrategy",
    # Generic
    "GenericStrategy",
]
