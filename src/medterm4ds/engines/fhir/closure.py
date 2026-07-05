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
import threading
from typing import Any

import duckdb

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
        # Set to True if any ancestor/descendant walk failed since reset.
        # Callers reading the closure can check this to know whether
        # check() may be returning false negatives.
        self.incomplete_since: bool = False
        self._lock = threading.RLock()

    def add_concept(self, code: str, source: str, display: str, engine) -> set[str]:
        """Add a concept to the closure. Returns the set of newly-discovered
        equivalence relationships (codes in the closure that subsume or are
        subsumed by this code).

        Uses the engine to walk ancestors and descendants, then records
        relationships for any that are already in the closure.

        Failure handling: duckdb.Error (transient lock timeouts, brief
        connection issues) is logged at WARNING and the walk continues with
        what was collected — the closure is marked incomplete via the
        ``incomplete_since`` attribute so callers can detect degradation.
        Programming bugs (TypeError, AttributeError, KeyError) propagate so
        they surface instead of producing silently-wrong subsumption answers.
        """
        with self._lock:
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
            except duckdb.Error as exc:
                # Transient DuckDB issue — log at WARNING (not DEBUG) so an
                # operator running $subsumes against an incomplete closure
                # has a log line explaining why answers may be wrong.
                logger.warning(
                    "Ancestor walk failed for %s in closure %s: %s. "
                    "Closure is now incomplete — $subsumes may return false negatives.",
                    code, self.name, exc,
                )
                self.incomplete_since = True

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
            except duckdb.Error as exc:
                logger.warning(
                    "Descendant walk failed for %s in closure %s: %s. "
                    "Closure is now incomplete — $subsumes may return false negatives.",
                    code, self.name, exc,
                )
                self.incomplete_since = True

            self._version += 1
            return new_relations

    def add_concepts(
        self,
        concepts: list[tuple[str, str, str]],
        engine,
    ) -> None:
        """Batch-add multiple concepts to the closure.

        Equivalent to calling add_concept() in a loop, but ancestor and
        descendant walks are batched per source — 2 walks per source
        instead of 2 walks per concept. For a $closure POST with 2000
        SNOMED concepts, this collapses ~4000 hierarchy queries into 2.

        Args:
            concepts: list of (code, source, display) tuples.
            engine: terminology engine for hierarchy walks.

        Side effects: updates self.concepts, self._subsumes, self._version,
        and may set self.incomplete_since on duckdb.Error (same failure
        semantics as add_concept).
        """
        if not concepts:
            return

        with self._lock:
            # Register all concepts first so cross-closure relationships are
            # discoverable during the walks below.
            for code, source, display in concepts:
                self.concepts[code] = {"system": source, "display": display}
                self._subsumes[(code, code)] = True

            # Group by source so each walk hits one source's hierarchy.
            by_source: dict[str, list[str]] = {}
            for code, source, _ in concepts:
                by_source.setdefault(source, []).append(code)

            for source, codes in by_source.items():
                refs = [CodeRef(source, code) for code in codes]
                try:
                    ancestors = get_ancestors(refs, engine=engine, max_depth=20)
                except duckdb.Error as exc:
                    logger.warning(
                        "Batched ancestor walk failed for %d %s concepts in "
                        "closure %s: %s. Closure is incomplete — $subsumes "
                        "may return false negatives.",
                        len(codes), source, self.name, exc,
                    )
                    self.incomplete_since = True
                    ancestors = []
                for rel in ancestors:
                    # rel.source.code is the input code; rel.target.code is its ancestor.
                    input_code = rel.source.code
                    anc_code = rel.target.code
                    if anc_code in self.concepts:
                        self._subsumes[(anc_code, input_code)] = True
                        self._subsumes[(input_code, anc_code)] = False

                try:
                    descendants = get_descendants(refs, engine=engine, max_depth=20)
                except duckdb.Error as exc:
                    logger.warning(
                        "Batched descendant walk failed for %d %s concepts in "
                        "closure %s: %s. Closure is incomplete — $subsumes "
                        "may return false negatives.",
                        len(codes), source, self.name, exc,
                    )
                    self.incomplete_since = True
                    descendants = []
                for rel in descendants:
                    input_code = rel.source.code
                    desc_code = rel.target.code
                    if desc_code in self.concepts:
                        self._subsumes[(input_code, desc_code)] = True
                        self._subsumes[(desc_code, input_code)] = False

            self._version += 1

    def check(self, code_a: str, code_b: str) -> str:
        """Check subsumption via the closure table.

        Returns: "equivalent", "subsumes", "subsumed-by", "not-subsumed".
        """
        with self._lock:
            if code_a == code_b:
                return "equivalent"
            if self._subsumes.get((code_a, code_b)):
                return "subsumes"
            if self._subsumes.get((code_b, code_a)):
                return "subsumed-by"
            return "not-subsumed"

    def version_hash(self) -> str:
        """Return a hash representing the current state of the closure."""
        with self._lock:
            payload = f"{len(self.concepts)}:{self._version}:{sorted(self.concepts.keys())}"
            return hashlib.md5(payload.encode()).hexdigest()[:12]

    def to_parameter_list(self) -> list[dict[str, Any]]:
        """Return concept list as FHIR Parameters parameter entries."""
        from medterm4ds.engines.fhir import system_to_fhir_uri

        with self._lock:
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
    """Manages named closure tables for the FHIR server.

    Thread-safe: a lock guards all mutations so callers don't have to
    serialize via an external executor. Today the FHIR API happens to dispatch
    $closure through a single-worker executor, but relying on that invariant
    is fragile — any future caller (status endpoint, background task, non-FHIR
    client) would race on `_tables` and `ClosureTable._subsumes` without this
    lock.
    """

    def __init__(self):
        self._tables: dict[str, ClosureTable] = {}
        self._lock = threading.RLock()

    def get_or_create(self, name: str) -> ClosureTable:
        with self._lock:
            if name not in self._tables:
                self._tables[name] = ClosureTable(name)
                logger.info("Created closure table: %s", name)
            return self._tables[name]

    def reset(self, name: str) -> ClosureTable:
        """Reset (or create) a named closure table."""
        with self._lock:
            self._tables[name] = ClosureTable(name)
            logger.info("Reset closure table: %s", name)
            return self._tables[name]

    def get(self, name: str) -> ClosureTable | None:
        with self._lock:
            return self._tables.get(name)

    def list_names(self) -> list[str]:
        with self._lock:
            return list(self._tables.keys())


# Singleton guarded by a lock so concurrent first-callers don't race on init.
# Without this, two threads seeing `_manager is None` simultaneously would
# each construct a ClosureManager; one wins the assignment, the other's
# tables are orphaned, and subsequent $subsumes calls return wrong answers.
_manager: ClosureManager | None = None
_manager_lock = threading.Lock()


def get_closure_manager() -> ClosureManager:
    global _manager
    with _manager_lock:
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
