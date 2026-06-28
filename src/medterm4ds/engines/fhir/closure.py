"""FHIR $closure table for fast subsumption checks.

Maintains named closure tables that pre-compute subsumption relationships
between concepts. When a concept is added, the server walks its ancestors
and descendants (via existing hierarchy services) and records which concepts
subsume which. Subsequent subsumption checks are O(1) dict lookups instead
of O(depth) hierarchy walks.

The closure tables are in-memory (lost on server restart). Clients should
re-initialize on each session. This is appropriate for the localhost-only
deployment model.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from medterm4ds.core.models import CodeRef
from medterm4ds.services.hierarchy import get_ancestors, get_descendants

logger = logging.getLogger(__name__)


class ClosureTable:
    """One named closure table storing pre-computed subsumption relationships."""

    def __init__(self, name: str):
        self.name = name
        # All concepts ever added: {code: {system, display}}
        self.concepts: dict[str, dict[str, str]] = {}
        # Subsumption: (code_a, code_b) -> True means "a subsumes b"
        self._subsumes: dict[tuple[str, str], bool] = {}
        self._version = 0

    def add_concept(self, code: str, source: str, display: str, engine) -> set[str]:
        """Add a concept to the closure. Returns the set of newly-discovered
        equivalence relationships (codes in the closure that subsume or are
        subsumed by this code).

        Uses the engine to walk ancestors and descendants, then records
        relationships for any that are already in the closure.
        """
        self.concepts[code] = {"system": source, "display": display}
        # Self-subsumption
        self._subsumes[(code, code)] = True

        new_relations: set[str] = set()

        # Walk ancestors: codes that subsume this one
        try:
            ancestors = get_ancestors([CodeRef(source, code)], engine=engine, max_depth=20)
            for rel in ancestors:
                anc_code = rel.target.code
                if anc_code in self.concepts:
                    # ancestor subsumes this code
                    self._subsumes[(anc_code, code)] = True
                    self._subsumes[(code, anc_code)] = False
                    new_relations.add(anc_code)
        except Exception:
            logger.debug("Ancestor walk failed for %s in closure %s", code, self.name)

        # Walk descendants: codes this one subsumes
        try:
            descendants = get_descendants([CodeRef(source, code)], engine=engine, max_depth=20)
            for rel in descendants:
                desc_code = rel.target.code
                if desc_code in self.concepts:
                    # this code subsumes descendant
                    self._subsumes[(code, desc_code)] = True
                    self._subsumes[(desc_code, code)] = False
                    new_relations.add(desc_code)
        except Exception:
            logger.debug("Descendant walk failed for %s in closure %s", code, self.name)

        self._version += 1
        return new_relations

    def check(self, code_a: str, code_b: str) -> str:
        """Check subsumption via the closure table.

        Returns: "equivalent", "subsumes", "subsumed-by", "not-subsumed".
        """
        if code_a == code_b:
            return "equivalent"
        if self._subsumes.get((code_a, code_b)):
            return "subsumes"
        if self._subsumes.get((code_b, code_a)):
            return "subsumed-by"
        return "not-subsumed"

    def version_hash(self) -> str:
        """Return a hash representing the current state of the closure."""
        payload = f"{len(self.concepts)}:{self._version}:{sorted(self.concepts.keys())}"
        return hashlib.md5(payload.encode()).hexdigest()[:12]

    def to_parameter_list(self) -> list[dict[str, Any]]:
        """Return concept list as FHIR Parameters parameter entries."""
        from medterm4ds.engines.fhir import system_to_fhir_uri

        entries: list[dict[str, Any]] = []
        for code, info in sorted(self.concepts.items()):
            system_uri = system_to_fhir_uri(info["system"]) or info["system"]
            entries.append({
                "name": "concept",
                "valueCoding": {
                    "system": system_uri,
                    "code": code,
                    "display": info.get("display", code),
                },
            })
        return entries


class ClosureManager:
    """Manages named closure tables for the FHIR server."""

    def __init__(self):
        self._tables: dict[str, ClosureTable] = {}

    def get_or_create(self, name: str) -> ClosureTable:
        if name not in self._tables:
            self._tables[name] = ClosureTable(name)
            logger.info("Created closure table: %s", name)
        return self._tables[name]

    def reset(self, name: str) -> ClosureTable:
        """Reset (or create) a named closure table."""
        self._tables[name] = ClosureTable(name)
        logger.info("Reset closure table: %s", name)
        return self._tables[name]

    def get(self, name: str) -> ClosureTable | None:
        return self._tables.get(name)

    def list_names(self) -> list[str]:
        return list(self._tables.keys())


# Singleton
_manager: ClosureManager | None = None


def get_closure_manager() -> ClosureManager:
    global _manager
    if _manager is None:
        _manager = ClosureManager()
    return _manager


def build_closure_response(closure: ClosureTable) -> dict[str, Any]:
    """Build a FHIR Parameters response for $closure.

    Includes:
    - return: version hash (so client knows if state changed)
    - concept: list of all concepts currently in the closure
    """
    return {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "return", "valueString": closure.version_hash()},
            *closure.to_parameter_list(),
        ],
    }
