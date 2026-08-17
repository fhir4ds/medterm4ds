"""Patient-friendly name resolution with prepared-table fast path."""


from __future__ import annotations

import duckdb

from medterm4ds.engines.duckdb._engine_base import *  # noqa: F401,F403
from collections.abc import Sequence
from medterm4ds.core.models import CodeRef, FriendlyNameResult


class _PatientFriendlyOps:
    """Patient-friendly name resolution with prepared-table fast path.

    Mixin for LocalDuckDBEngine — methods share state via ``self`` (``self.con``,
    ``self.cache_prepared``, ``self.query_chunk_size``, etc.). Not intended to be
    instantiated on its own.
    """

    def get_patient_friendly_names(
        self,
        codes: Sequence[CodeRef],
        max_depth: int = 5,
    ) -> list[FriendlyNameResult]:
        if not codes:
            return []

        # Patient-friendly hierarchy policy:
        # - ICD/CPT/HCPCS-like sources walk their own hierarchy first and
        #   stop at the first depth with a non-heading MEDLINEPLUS/CHV atom,
        #   preferring MEDLINEPLUS only within that depth frontier.
        # - LOINC keeps its source-native component/axis/common-name tiers, then
        #   participates in SNOMED fallback if those tiers miss.
        # - If source-native hierarchy misses, fall back through SNOMED and use
        #   the same first-frontier rule. SNOMED fallback accepts nodes at
        #   top-level depth >= 4 and does not expand into levels 1-3.
        # - RxNorm and CVX use separate source-native strategies.
        ordered = [CodeRef(source=c.source, code=c.code) for c in codes]
        sources = {ref.source for ref in ordered}
        if self._has_patient_friendly_prepared_tables(sources):
            try:
                return self._get_patient_friendly_names_prepared(ordered, max_depth=max_depth)
            except duckdb.Error as exc:
                # Narrow to duckdb.Error so programming bugs (KeyError, AttributeError,
                # schema mismatches in the prepared SQL) propagate instead of silently
                # falling back to the legacy raw-mrrel path. The legacy path no longer
                # supports LNC (see _resolve_source), so a silent fallback there would
                # either raise NotImplementedError or — worse — answer with the wrong
                # resolver. WARNING because fallback means degraded results.
                logger.warning(
                    "Prepared patient-friendly path failed (%s); falling back to legacy "
                    "raw-mrrel path. Legacy does not support LNC.",
                    exc,
                )

        grouped: dict[str, list[str]] = defaultdict(list)
        for ref in ordered:
            grouped[ref.source].append(ref.code)
        grouped = {source: _dedupe(values) for source, values in grouped.items()}

        snomed_codes = grouped.pop("SNOMEDCT_US", [])
        if snomed_codes:
            self._progress(f"mapping SNOMEDCT_US ({len(snomed_codes)} codes)")
        snomed_map = self._map_snomed_codes(snomed_codes) if snomed_codes else {}
        for _sn_code, (target_source, target_code, _is_broader) in snomed_map.items():
            grouped.setdefault(target_source, []).append(target_code)
        grouped = {source: _dedupe(values) for source, values in grouped.items()}

        non_snomed: dict[tuple[str, str], _Row] = {}
        # QC-436 (MEDIUM): per-source isolation. One source the legacy path
        # cannot resolve (LNC without the prepared schema) previously raised
        # for the ENTIRE batch — 9 clinically valid codes got no answer
        # because a 10th was LOINC. Catch NotImplementedError per source,
        # keep a WARNING (the message carries the actionable remediation),
        # and let the output loop below emit per-code original-name rows at
        # the exact positions, mirroring lookup_codes' null-field-record
        # isolation for bogus codes. Not a silent fallback: the whole batch
        # failing (every source unsupported) still re-raises so single-source
        # callers keep the loud, actionable error.
        unsupported_source_error: NotImplementedError | None = None
        any_source_resolved = False
        for source, source_codes in grouped.items():
            source_chunks = list(_chunks(source_codes, self.query_chunk_size))
            for chunk_index, source_chunk in enumerate(source_chunks, 1):
                self._progress(
                    f"resolving {source} chunk {chunk_index}/{len(source_chunks)} "
                    f"({len(source_chunk)} codes)"
                )
                try:
                    rows = self._resolve_source(source, source_chunk, max_depth)
                except NotImplementedError as exc:
                    logger.warning(
                        "Patient-friendly resolution for source %r is unsupported "
                        "on this connection (%s); emitting original-name rows for "
                        "%d codes at their exact positions.",
                        source,
                        exc,
                        len(source_chunk),
                    )
                    unsupported_source_error = exc
                    continue
                any_source_resolved = True
                self._apply_snomed_fallback(source, rows, max_depth)
                for row in rows:
                    non_snomed[(row.source, row.code)] = row
        if unsupported_source_error is not None and not any_source_resolved and not snomed_codes:
            # Nothing was resolvable — surface the loud actionable error
            # instead of an all-original batch that looks like success.
            raise unsupported_source_error

        if snomed_codes:
            self._progress(f"resolving SNOMEDCT_US ({len(snomed_codes)} codes)")
        snomed_rows = self._resolve_snomed(snomed_codes, snomed_map, non_snomed, max_depth)
        snomed_by_code = {row.code: row for row in snomed_rows}

        output: list[FriendlyNameResult] = []
        for ref in ordered:
            if ref.source == "SNOMEDCT_US":
                row = snomed_by_code.get(ref.code) or self._make_original(ref.code, ref.source)
            else:
                row = non_snomed.get((ref.source, ref.code)) or self._make_original(ref.code, ref.source)
            output.append(row.result())
        return output



    def _get_patient_friendly_names_prepared(
        self,
        codes: Sequence[CodeRef],
        *,
        max_depth: int,
    ) -> list[FriendlyNameResult]:
        from medterm4ds.services.patient_friendly_prepared import (
            get_non_rxnorm_patient_friendly,
        )
        from medterm4ds.services.rxnorm_tty_walk import get_rxnorm_patient_friendly

        rxnorm_items: list[tuple[int, CodeRef]] = []
        other_items: list[tuple[int, CodeRef]] = []
        for index, code in enumerate(codes):
            if code.source == "RXNORM":
                rxnorm_items.append((index, code))
            else:
                other_items.append((index, code))

        by_index: dict[int, FriendlyNameResult] = {}
        if rxnorm_items:
            rxnorm_rows = get_rxnorm_patient_friendly(
                [code for _index, code in rxnorm_items],
                self.con,
            )
            for (index, _code), row in zip(rxnorm_items, rxnorm_rows, strict=True):
                by_index[index] = row
        if other_items:
            other_rows = get_non_rxnorm_patient_friendly(
                [code for _index, code in other_items],
                self.con,
                max_depth=max_depth,
            )
            for (index, _code), row in zip(other_items, other_rows, strict=True):
                by_index[index] = row

        return [by_index[index] for index in range(len(codes))]



    def _has_patient_friendly_prepared_tables(self, sources: set[str]) -> bool:
        if not self._prepared_schema_version_is_current():
            # QC-435: the version-mismatch WARNING is logged by the gate
            # itself (once per engine instance).
            return False
        required = {
            "best_atoms",
            "patient_friendly_strategy",
        }
        if sources - {"RXNORM"}:
            required.update({
                "walk_edges",
                "friendly_atoms",
                "snomed_top_level_depth",
                "cvx_metadata",
            })
        if "RXNORM" in sources:
            required.update({
                "rxnorm_tty_paths",
                "rxnorm_tty_path_steps",
                "rxnorm_tty_edges",
            })
        if "SNOMEDCT_US" in sources or any(source in _SNOMED_FALLBACK_SOURCES for source in sources):
            required.update({
                "walk_edges",
                "friendly_atoms",
                "snomed_top_level_depth",
            })
        try:
            rows = self.con.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'mt4ds'
                  AND table_name IN ({})
                """.format(", ".join(["?"] * len(required))),
                list(required),
            ).fetchall()
        except duckdb.Error as exc:
            # QC-437: narrow the bare ``except Exception`` to duckdb.Error so
            # programming bugs propagate; a failed probe is a degraded gate
            # and must be visible.
            logger.warning(
                "Failed to probe mt4ds prepared patient-friendly tables (%s); "
                "falling back to the legacy raw-mrrel resolver (LNC "
                "patient-friendly unsupported).",
                exc,
            )
            return False
        available = {str(row[0]) for row in rows}
        if available != required:
            # QC-437: a stale/partial prepared schema refused the gate
            # silently before — log the refusal and its consequence once per
            # distinct missing-table set.
            missing = frozenset(required - available)
            if missing not in self._pf_gate_refusal_warned:
                logger.warning(
                    "mt4ds prepared schema is missing patient-friendly tables "
                    "%s — falling back to the legacy raw-mrrel resolver (LNC "
                    "patient-friendly unsupported). Rebuild the persisted "
                    "mt4ds schema: medterm4ds data prepare-derived --db <db>.",
                    sorted(missing),
                )
                self._pf_gate_refusal_warned.add(missing)
            return False
        needs_crosswalk = (
            bool(sources - {"RXNORM"})
            or "SNOMEDCT_US" in sources
            or any(source in _SNOMED_FALLBACK_SOURCES for source in sources)
        )
        if needs_crosswalk and not (
            self._table_exists("crosswalk_edges")
            or self._table_exists("same_cui_edges")
        ):
            return False
        return True

