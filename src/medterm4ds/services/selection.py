"""Candidate ranking and frontier selection.

Provides deterministic ranking of terminology candidates, used by
higher-level resolution and patient-friendly workflows.

Design note (Tier C Phase 6 investigation, 2026-06-26):
These primitives (rank_candidates, select_frontier) are currently NOT
called by any code in src/ outside this module. The patient-friendly
prepared resolver (`services/patient_friendly_prepared.py`) does its
own ranking inline via SQL (using best_atoms.rank and ROW_NUMBER),
which is more efficient for batched prepared-table queries than calling
a Python-side ranker per candidate.

They remain as a public API for callers who want Python-side candidate
ranking over a small set, and as documentation of the ranking policy
(_TTY_PRIORITY ordering, broad-name filtering, combo-name mismatch
detection). If patient_friendly_prepared.py ever moves away from
SQL-side ranking, these functions are the canonical implementation
to adopt.
"""
from __future__ import annotations

import re
from collections.abc import Sequence

from medterm4ds.core.models import CodeRef

# Canonical TTY priority for display-name selection
_TTY_PRIORITY: dict[str, int] = {
    "PT": 0,
    "HT": 1,
    "FN": 2,
    "MH": 3,
    "LN": 4,
}

_DEFAULT_TTY_RANK = 99
_COMBO_SEP_HINTS = (" and ", "/", " + ", " with ")
_COMBO_TERM_STOPWORDS = {
    "and",
    "with",
    "only",
    "product",
    "tablet",
    "tablets",
    "injection",
    "oral",
    "inhaler",
    "solution",
    "powder",
    "spray",
    "ointment",
    "cream",
    "gel",
    "patch",
    "sustained",
    "release",
    "extended",
    "drug",
    "therapy",
    "combination",
    "strength",
    "strengths",
    "mg",
    "mcg",
    "iu",
    "units",
    "unit",
    "percent",
    "ml",
    "l",
    "meq",
}


class Candidate:
    """One candidate code with metadata for deterministic ranking."""

    __slots__ = (
        "code",
        "name",
        "tty",
        "is_active",
        "source_priority",
        "frontier_depth",
        "is_acceptable",
    )

    def __init__(
        self,
        code: CodeRef,
        *,
        name: str | None = None,
        tty: str | None = None,
        is_active: bool = True,
        source_priority: int = 0,
        frontier_depth: int | None = 0,
        is_acceptable: bool = True,
    ):
        self.code = code
        self.name = name
        self.tty = tty
        self.is_active = is_active
        self.source_priority = source_priority
        self.frontier_depth = 0 if frontier_depth is None else frontier_depth
        self.is_acceptable = is_acceptable

    @property
    def tty_rank(self) -> int:
        return _TTY_PRIORITY.get(self.tty or "", _DEFAULT_TTY_RANK)

    def sort_key(self) -> tuple:
        """Return a tuple for deterministic sorting.

        Order: active first, acceptable first, nearest frontier first, then
        source priority (lower is better), TTY rank (lower is better), and
        alphabetical name/code.
        """
        return (
            0 if self.is_active else 1,
            0 if self.is_acceptable else 1,
            self.frontier_depth,
            self.source_priority,
            self.tty_rank,
            self.name or "",
            self.code.code,
        )


def rank_candidates(
    candidates: Sequence[Candidate],
    *,
    prefer_active: bool = True,
    prefer_source_priority: Sequence[str] | None = None,
) -> list[Candidate]:
    """Deterministic candidate ranking.

    Parameters
    ----------
    candidates:
        Input candidates to rank.
    prefer_active:
        If True (default), active candidates sort before inactive ones.
    prefer_source_priority:
        If provided, sources listed earlier get a lower (better) priority
        number.  Unlisted sources get ``len(list)``.
    Candidate frontier semantics:
        Candidates with ``is_acceptable=False`` sort after acceptable candidates.
        Candidates with lower ``frontier_depth`` sort before farther candidates.

    Returns
    -------
    list[Candidate]
        Candidates sorted from best to worst.
    """
    if not candidates:
        return []

    source_rank: dict[str, int] = {}
    if prefer_source_priority:
        source_rank = {
            source: idx for idx, source in enumerate(prefer_source_priority)
        }
        default_rank = len(prefer_source_priority)
    else:
        default_rank = 0

    ranked: list[Candidate] = []
    for c in candidates:
        c.source_priority = source_rank.get(c.code.source, default_rank)
        ranked.append(c)

    if prefer_active:
        ranked.sort(key=lambda c: c.sort_key())
    else:
        # Ignore active/inactive distinction
        ranked.sort(
            key=lambda c: (
                c.sort_key()[1],
                c.sort_key()[2],
                c.sort_key()[3],
                c.sort_key()[4],
                c.sort_key()[5],
                c.sort_key()[6],
            )
        )

    return ranked


def select_frontier(
    candidates: Sequence[Candidate],
    *,
    max_results: int = 1,
    prefer_source_priority: Sequence[str] | None = None,
    acceptable_only: bool = True,
) -> list[Candidate]:
    """Select top-N candidates from the nearest acceptable frontier.

    Parameters
    ----------
    candidates:
        Input candidates.
    max_results:
        Maximum number of results to return.
    prefer_source_priority:
        Source preference applied only within the selected frontier. For
        patient-friendly naming, pass ``("MEDLINEPLUS", "CHV")`` so
        MEDLINEPLUS wins only when it is at the same depth as CHV.
    acceptable_only:
        If True, ignore candidates marked ``is_acceptable=False``. This lets
        broad or guard-failed labels stay available for audit without being
        selected.

    Returns
    -------
    list[Candidate]
        Top candidates, up to *max_results*.
    """
    if max_results <= 0:
        return []

    pool = list(candidates)
    if acceptable_only:
        pool = [candidate for candidate in pool if candidate.is_acceptable]
    elif any(candidate.is_acceptable for candidate in pool):
        pool = [candidate for candidate in pool if candidate.is_acceptable]

    if not pool:
        return []

    min_depth = min(candidate.frontier_depth for candidate in pool)
    frontier = [
        candidate
        for candidate in pool
        if candidate.frontier_depth == min_depth
    ]
    ranked = rank_candidates(
        frontier,
        prefer_source_priority=prefer_source_priority,
    )
    return ranked[:max_results]


def is_combo_name_mismatch(source_name: str | None, candidate_name: str | None) -> bool:
    """Return True when a combination source name has no meaningful token overlap.

    This guard is intentionally narrow. It only activates for source names that
    look like combinations, so ordinary single-concept synonyms are not rejected
    just because they use different wording.
    """
    if not source_name or not candidate_name:
        return False
    if not any(sep in source_name.lower() for sep in _COMBO_SEP_HINTS):
        return False
    source_tokens = _normalize_combo_tokens(source_name)
    candidate_tokens = _normalize_combo_tokens(candidate_name)
    return bool(
        source_tokens
        and candidate_tokens
        and source_tokens.isdisjoint(candidate_tokens)
    )


def _normalize_combo_tokens(name: str | None) -> set[str]:
    if not name:
        return set()
    tokens = re.sub(r"[^a-z0-9]+", " ", name.lower())
    return {
        token
        for token in tokens.split()
        if token and token not in _COMBO_TERM_STOPWORDS and len(token) > 2
    }
