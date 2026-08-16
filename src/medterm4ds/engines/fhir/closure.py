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
from medterm4ds.services.hierarchy import get_ancestors_bfs, get_descendants_bfs

logger = logging.getLogger(__name__)

# Closure concepts are keyed by (source, code), never by the bare code
# string: two systems can share a code (e.g. an ICD-10-CM code that is also
# a valid SCTID digit string) and bare-code keying silently overwrites the
# first entry and conflates cross-system subsumption pairs (QC-266).
_ConceptKey = tuple[str, str]


class ClosureTable:
    """One named closure table storing pre-computed subsumption relationships."""

    def __init__(self, name: str):
        self.name = name
        # All concepts ever added: {(source, code): {system, display}}
        self.concepts: dict[_ConceptKey, dict[str, str]] = {}
        # Subsumption: (key_a, key_b) -> True means "a subsumes b"
        self._subsumes: dict[tuple[_ConceptKey, _ConceptKey], bool] = {}
        self._version = 0
        # Set to True if any ancestor/descendant walk failed since reset.
        # Callers reading the closure can check this to know whether
        # check() may be returning false negatives. Surfaced on the wire by
        # build_closure_response() as the `incomplete` Out parameter (QC-267).
        self.incomplete_since: bool = False
        self._lock = threading.RLock()

    def add_concept(self, code: str, source: str, display: str, engine) -> set[str]:
        """Add a concept to the closure. Returns the set of newly-discovered
        equivalence relationships (codes in the closure that subsume or are
        subsumed by this code).

        Uses the engine to walk ancestors and descendants, then records
        relationships for any that are already in the closure. Both walks
        are layer-by-layer BFS (visited-set, one batched query per layer) —
        the recursive-CTE get_ancestors/get_descendants walks previously used
        here enumerate every distinct path through multiply-inherited
        subtrees and exploded to 32 GB RSS / OOM-at-1 GiB for ordinary
        concepts (QC-261/275/281).

        Re-adding a concept already in the closure is a no-op: the earlier
        walk already recorded its relationships, and re-walking costs full
        hierarchy walks for zero state change (QC-278).

        Failure handling: duckdb.Error (transient lock timeouts, brief
        connection issues) is logged at WARNING and the walk continues with
        what was collected — the closure is marked incomplete via the
        ``incomplete_since`` attribute so callers can detect degradation.
        Programming bugs (TypeError, AttributeError, KeyError) propagate so
        they surface instead of producing silently-wrong subsumption answers.
        """
        new_relations: set[str] = set()
        with self._lock:
            key = (source, code)
            if key in self.concepts:
                return new_relations
            new_relations = self._record_walk(code, source, display, engine)
        return new_relations

    def _record_walk(self, code: str, source: str, display: str, engine) -> set[str]:
        """Register one concept and record its in-closure subsumption pairs.

        Returns the set of in-closure codes that subsume or are subsumed by
        this one. Caller must hold ``self._lock`` and have verified the
        concept is not already present. Shared by add_concept and
        add_concepts.
        """
        new_relations: set[str] = set()
        key = (source, code)
        self.concepts[key] = {"system": source, "display": display}
        # Self-subsumption
        self._subsumes[(key, key)] = True

        seed = CodeRef(source, code)

        # Walk ancestors: codes that subsume this one. BFS visits each
        # ancestor exactly once (visited set), so the multiply-inherited
        # path explosion of the recursive CTE cannot occur.
        try:
            ancestors, _cap = get_ancestors_bfs(seed, engine=engine, max_depth=20)
            for rel in ancestors:
                anc_key = (source, rel.target.code)
                if anc_key in self.concepts:
                    # ancestor subsumes this code
                    self._subsumes[(anc_key, key)] = True
                    self._subsumes[(key, anc_key)] = False
                    new_relations.add(rel.target.code)
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

        # Walk descendants: codes this one subsumes.
        try:
            descendants, _cap = get_descendants_bfs(seed, engine=engine, max_depth=20)
            for rel in descendants:
                desc_key = (source, rel.target.code)
                if desc_key in self.concepts:
                    # this code subsumes descendant
                    self._subsumes[(key, desc_key)] = True
                    self._subsumes[(desc_key, key)] = False
                    new_relations.add(rel.target.code)
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

        Each NEW concept gets one BFS ancestor walk + one BFS descendant
        walk (2 walks per new concept, each visiting every node once —
        linear in subtree size, no path enumeration). Concepts already in
        the closure are skipped without re-walking (QC-278).

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
            # Register concepts one at a time so each walk sees the concepts
            # registered before it (order within one POST does not matter for
            # the FINAL state: each concept's own ancestor+descendant walks
            # discover every in-closure relative regardless of order — the
            # pre-BFS CTE walks OOM'd nondeterministically instead, which is
            # what made insertion order change the answer, QC-281).
            for code, source, display in concepts:
                if (source, code) in self.concepts:
                    continue
                self._record_walk(code, source, display, engine)

    def check(self, code_a: str, code_b: str, system: str | None = None) -> str:
        """Check subsumption via the closure table.

        Subsumption is only defined within one code system (the R4
        $subsumes/$closure model has no cross-system relationship map), so
        relations are namespaced per system (QC-266).

        When ``system`` is supplied, both codes are resolved under that
        system. When omitted (backward-compatible 2-arg form), the pair is
        resolved within any single shared system: codes registered only
        under DIFFERENT systems are "not-subsumed" — cross-system pairs
        can never be conflated into a false relationship.

        Returns: "equivalent", "subsumes", "subsumed-by", "not-subsumed".
        """
        with self._lock:
            if code_a == code_b:
                return "equivalent"
            if system is not None:
                key_a = (system, code_a)
                key_b = (system, code_b)
                if self._subsumes.get((key_a, key_b)):
                    return "subsumes"
                if self._subsumes.get((key_b, key_a)):
                    return "subsumed-by"
                return "not-subsumed"
            systems_a = {s for (s, c) in self.concepts if c == code_a}
            systems_b = {s for (s, c) in self.concepts if c == code_b}
            for shared in sorted(systems_a & systems_b):
                if self._subsumes.get(((shared, code_a), (shared, code_b))):
                    return "subsumes"
                if self._subsumes.get(((shared, code_b), (shared, code_a))):
                    return "subsumed-by"
            return "not-subsumed"

    def version_hash(self) -> str:
        """Return a content hash of the full closure state.

        Hashes the concept set (source, code, display) AND the recorded
        subsumption relations, so any change in closure content — including
        a relation set degraded by a failed walk — changes the token
        (QC-283). The hash is deterministic for identical content: it
        excludes the internal call counter, so two closures built via
        different POST batching (or re-adding an already-present concept,
        which is a no-op) report the same version (QC-270/QC-278).
        """
        with self._lock:
            concept_items = sorted(
                (source, code, info.get("display", ""))
                for (source, code), info in self.concepts.items()
            )
            relation_items = sorted(
                key for key, value in self._subsumes.items() if value
            )
            payload = repr((concept_items, relation_items))
            return hashlib.md5(payload.encode()).hexdigest()[:12]

    def to_parameter_list(self) -> list[dict[str, Any]]:
        """Return concept list as FHIR Parameters parameter entries."""
        from medterm4ds.engines.fhir import system_to_fhir_uri

        with self._lock:
            entries: list[dict[str, Any]] = []
            for (source, code), info in sorted(self.concepts.items()):
                system_uri = system_to_fhir_uri(source) or source
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
    - incomplete: valueBoolean — True when any ancestor/descendant walk
      failed since the last reset, meaning check()/$subsumes answers read
      from this closure may be false negatives (QC-267; the
      ``incomplete_since`` degradation flag was previously server-internal
      only).
    - concept: list of all concepts currently in the closure
    """
    return {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "return", "valueString": closure.version_hash()},
            {"name": "incomplete", "valueBoolean": closure.incomplete_since},
            *closure.to_parameter_list(),
        ],
    }
