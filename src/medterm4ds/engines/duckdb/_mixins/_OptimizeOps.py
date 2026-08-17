"""Compact a valueset into include/exclude hierarchy rules."""


from __future__ import annotations

from medterm4ds.engines.duckdb._engine_base import *  # noqa: F401,F403
from collections.abc import Sequence
from medterm4ds.core.models import CodeRef, OptimizeResult

# QC-208/QC-214 (HIGH): production hierarchies reach depth 18 (SNOMED,
# per mt4ds.snomed_top_level_depth) and 15 (LNC). The previous cap of 12
# silently dropped ancestors and leaf descendants on those deep paths —
# include rules silently under-covered deep subtrees. The BFS loop breaks
# as soon as a level yields no new codes, so sources with shallower
# hierarchies (ICD10CM, max depth 7) pay no extra round-trips.
_OPTIMIZE_MAX_DEPTH = 18


class _OptimizeOps:
    """Compact a valueset into include/exclude hierarchy rules.

    Mixin for LocalDuckDBEngine — methods share state via ``self`` (``self.con``,
    ``self.cache_prepared``, ``self.query_chunk_size``, etc.). Not intended to be
    instantiated on its own.
    """

    def optimize_codes(
        self,
        codes: Sequence[CodeRef],
        *,
        relationship: str | None = None,
        output_format: str = "compact",
        include_codes: bool = False,
    ) -> OptimizeResult:
        """Optimize a source-specific valueset into hierarchy include/exclude rules."""
        if output_format not in {"compact", "flat"}:
            raise ValueError("output_format must be compact or flat")
        if not codes:
            return OptimizeResult(
                source="",
                relationship=relationship or "isa",
                rules=(),
                original_count=0,
                optimized_count=0,
                reduction=0.0,
            )
        refs = [CodeRef(source=code.source, code=code.code) for code in codes]
        sources = {ref.source for ref in refs}
        if len(sources) != 1:
            raise ValueError("optimize_codes requires all codes to use the same source")
        source = refs[0].source
        # QC-196 (MEDIUM): empty string is never a valid code or source
        # (promoted GLOBAL_RULES pattern — min_length=1 equivalent at the
        # service boundary). Pre-fix, CodeRef('ICD10CM','') produced a rule
        # whose include was ''.
        if not source.strip():
            raise ValueError("source must be a non-empty string")
        for ref in refs:
            if not ref.code.strip():
                raise ValueError(f"code must be a non-empty string, got {ref.code!r}")
        rel = relationship or _DEFAULT_OPTIMIZE_REL.get(source, "isa")
        if str(rel).lower() == "prefix":
            raise ValueError("prefix optimize is not supported; use UMLS hierarchy relationships")
        # QC-195 (MEDIUM): an unknown source used to silently succeed with
        # echo rules (include_source='NOSUCHSOURCE'). Reject at the boundary
        # when the source has no codes in this database.
        if not self._optimize_source_exists(source):
            raise ValueError(
                f"source {source!r} has no codes in this database "
                f"(check the SAB spelling, e.g. ICD10CM / SNOMEDCT_US)"
            )

        leaves = self._normalize_optimize_input(refs, rel)
        remaining = set(leaves)
        if not remaining:
            return OptimizeResult(
                source=source,
                relationship=rel,
                rules=(),
                original_count=len(leaves),
                optimized_count=0,
                reduction=0.0,
            )

        ancestor_cache = self._related_code_map(
            source,
            sorted(remaining),
            relationship=rel,
            upward=True,
            max_depth=_OPTIMIZE_MAX_DEPTH,
            warn_truncated=True,
        )
        candidate_set = set(remaining)
        for ancestors in ancestor_cache.values():
            candidate_set.update(ancestors)
        leaf_cache = self._leaf_descendants_for_candidates(source, sorted(candidate_set), rel)
        rules: list[OptimizeRule] = []

        while remaining:
            best_code: str | None = None
            best_covered: set[str] = set()
            best_excluded: set[str] = set()
            best_score = -1.0
            candidates = set(remaining)
            for code in remaining:
                candidates.update(ancestor_cache.get(code, set()))

            for candidate in sorted(candidates):
                descendant_leaves = leaf_cache.get(candidate, set())
                if not descendant_leaves:
                    descendant_leaves = {candidate}
                covered = descendant_leaves & remaining
                if not covered:
                    continue
                excluded = descendant_leaves - remaining
                # QC-192 (HIGH): score candidates on rule-count reduction —
                # rules emitted if this candidate is picked = 1 include +
                # len(excluded) excludes, versus len(covered) singleton rules
                # otherwise. The previous covered/(1+excluded) ratio was
                # always dominated by singleton candidates at 1.0, so the
                # exclude branch was dead code on every input.
                score = len(covered) - (1 + len(excluded))
                if (
                    score > best_score
                    or (
                        score == best_score
                        and (
                            len(excluded) < len(best_excluded)
                            or (
                                # QC-212 (HIGH): on full ties prefer a
                                # candidate that is itself an input-derived
                                # code over an ancestor of it. The previous
                                # lexical ``candidate > best_code`` tie-break
                                # picked ancestors (e.g. LOINC 'LP' grouping
                                # nodes) over the user's codes.
                                len(excluded) == len(best_excluded)
                                and candidate in remaining
                                and best_code not in remaining
                            )
                        )
                    )
                ):
                    best_code = candidate
                    best_covered = covered
                    best_excluded = excluded
                    best_score = score

            if best_code is None:
                best_code = min(remaining)
                best_covered = {best_code}
                best_excluded = set()

            if output_format == "flat":
                rules.append(
                    OptimizeRule(
                        include=CodeRef(source, best_code),
                        covered_codes=tuple(CodeRef(source, code) for code in sorted(best_covered)),
                    )
                )
                rules.extend(
                    OptimizeRule(include=CodeRef(source, code))
                    for code in sorted(best_excluded)
                )
            else:
                rules.append(
                    OptimizeRule(
                        include=CodeRef(source, best_code),
                        exclude=tuple(CodeRef(source, code) for code in sorted(best_excluded)),
                        covered_codes=tuple(CodeRef(source, code) for code in sorted(best_covered)),
                        excluded_codes=tuple(CodeRef(source, code) for code in sorted(best_excluded)),
                    )
                )
            remaining -= best_covered

        # QC-194/QC-215 (HIGH): compute original_count/reduction on the
        # normalized-leaf basis — the actual valueset content being compacted.
        # The previous len(refs) basis counted literal duplicates and made
        # ['E11'] vs ['E11','E11.9'] vs ['E11.9']*3 report 0%/50%/66.7%
        # reduction for semantically identical valuesets.
        reduction = 0.0
        if leaves:
            reduction = round((1 - (len(rules) / len(leaves))) * 100, 2)
        return OptimizeResult(
            source=source,
            relationship=rel,
            rules=tuple(rules),
            original_count=len(leaves),
            optimized_count=len(rules),
            reduction=reduction,
        )



    def _normalize_optimize_input(self, refs: Sequence[CodeRef], relationship: str) -> set[str]:
        source = refs[0].source
        input_codes = {ref.code for ref in refs}
        leaf_map = self._leaf_descendants_for_candidates(source, sorted(input_codes), relationship)
        leaves: set[str] = set()
        for code in sorted(input_codes):
            descendant_leaves = leaf_map.get(code, set())
            if descendant_leaves:
                leaves.update(descendant_leaves)
            else:
                leaves.add(code)
        return leaves



    def _optimize_source_exists(self, source: str) -> bool:
        """QC-195 (MEDIUM): reject sources with no codes in this database.

        Checks the prepared walk_edges table first (cheap columnar probe),
        then falls back to mrconso. Returns True when neither table exists
        (nothing to validate against — e.g. minimal test fixtures).
        """
        if self._table_exists("walk_edges"):
            row = self.con.execute(
                "SELECT 1 FROM mt4ds.walk_edges WHERE source = ? LIMIT 1",
                [source],
            ).fetchone()
            if row is not None:
                return True
        if self._table_exists("mrconso"):
            return (
                self.con.execute(
                    "SELECT 1 FROM mrconso WHERE SAB = ? LIMIT 1",
                    [source],
                ).fetchone()
                is not None
            )
        return True



    def _leaf_descendants_for_candidates(
        self,
        source: str,
        codes: Sequence[str],
        relationship: str,
    ) -> dict[str, set[str]]:
        descendant_map = self._related_code_map(
            source,
            codes,
            relationship=relationship,
            upward=False,
            max_depth=_OPTIMIZE_MAX_DEPTH,
            warn_truncated=True,
        )
        all_descendants = sorted({code for descendants in descendant_map.values() for code in descendants})
        if not all_descendants:
            return {code: set() for code in codes}
        child_map = self._related_code_map(
            source,
            all_descendants,
            relationship=relationship,
            upward=False,
            max_depth=1,
        )
        non_leaf = {code for code, children in child_map.items() if children}
        return {
            code: set(descendants) - non_leaf
            for code, descendants in descendant_map.items()
        }



    def _snomed_top_level_depths(self, codes: Sequence[str]) -> dict[str, int]:
        if not codes or not self._table_exists("snomed_top_level_depth"):
            return {}
        with self._temp_codes(codes) as temp:
            rows = self.con.execute(
                f"""
                SELECT code, min_top_depth
                FROM snomed_top_level_depth
                WHERE code IN (SELECT code FROM {temp})
                """
            ).fetchall()
        return {row[0]: int(row[1]) for row in rows if row[1] is not None}

