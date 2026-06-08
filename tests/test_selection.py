"""Tests for shared candidate selection primitives."""
from __future__ import annotations

from medterm4ds.core.models import CodeRef
from medterm4ds.services.selection import (
    Candidate,
    is_combo_name_mismatch,
    select_frontier,
)


FRIENDLY_SOURCE_PRIORITY = ("MEDLINEPLUS", "CHV")


def test_select_frontier_prefers_medlineplus_over_chv_at_same_depth() -> None:
    candidates = [
        Candidate(
            CodeRef(source="CHV", code="CHV_URTI"),
            name="cold and flu",
            frontier_depth=2,
        ),
        Candidate(
            CodeRef(source="MEDLINEPLUS", code="MP_URTI"),
            name="Upper Respiratory Infections",
            frontier_depth=2,
        ),
    ]

    selected = select_frontier(
        candidates,
        prefer_source_priority=FRIENDLY_SOURCE_PRIORITY,
    )

    assert [candidate.code.source for candidate in selected] == ["MEDLINEPLUS"]


def test_select_frontier_prefers_closer_chv_over_farther_medlineplus() -> None:
    candidates = [
        Candidate(
            CodeRef(source="CHV", code="CHV_URTI"),
            name="cold and flu",
            frontier_depth=1,
        ),
        Candidate(
            CodeRef(source="MEDLINEPLUS", code="MP_RESP"),
            name="Respiratory Diseases",
            frontier_depth=2,
        ),
    ]

    selected = select_frontier(
        candidates,
        prefer_source_priority=FRIENDLY_SOURCE_PRIORITY,
    )

    assert [candidate.code.source for candidate in selected] == ["CHV"]


def test_select_frontier_skips_unacceptable_closer_candidate() -> None:
    candidates = [
        Candidate(
            CodeRef(source="MEDLINEPLUS", code="MP_DISEASE"),
            name="Diseases",
            frontier_depth=0,
            is_acceptable=False,
        ),
        Candidate(
            CodeRef(source="CHV", code="CHV_SPECIFIC"),
            name="specific condition",
            frontier_depth=1,
        ),
    ]

    selected = select_frontier(
        candidates,
        prefer_source_priority=FRIENDLY_SOURCE_PRIORITY,
    )

    assert [candidate.code.code for candidate in selected] == ["CHV_SPECIFIC"]


def test_combo_name_mismatch_rejects_unrelated_chv_candidate() -> None:
    assert is_combo_name_mismatch(
        "alcohol use with cocaine-induced psychotic disorder",
        "brain findings",
    )


def test_combo_name_mismatch_keeps_overlapping_candidate() -> None:
    assert not is_combo_name_mismatch(
        "alcohol use with cocaine-induced psychotic disorder",
        "alcohol related disorders",
    )


def test_combo_name_mismatch_does_not_apply_to_single_concept_source() -> None:
    assert not is_combo_name_mismatch("kidney injury", "renal trauma")
