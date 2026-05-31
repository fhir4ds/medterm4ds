"""Adapter around the dirty working tree of `/mnt/d/medterm`.

This is intentionally a test/comparison adapter, not a production engine.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from medterm4ds.core.models import CodeRef, FriendlyNameResult


class MedtermBulkBaselineEngine:
    """Call medterm's current bulk patient-friendly implementation."""

    def __init__(self, con, medterm_path: str | Path = "/mnt/d/medterm"):
        self.con = con
        self.medterm_path = Path(medterm_path)

    def get_patient_friendly_names(
        self,
        codes: Sequence[CodeRef],
        max_depth: int = 5,
    ) -> list[FriendlyNameResult]:
        src_path = str(self.medterm_path / "src")
        if src_path not in sys.path:
            sys.path.insert(0, src_path)

        from medterm.bulk.transforms.patient_friendly import get_patient_friendly_names

        legacy_rows = get_patient_friendly_names(
            [code.as_pair() for code in codes],
            self.con,
            max_depth=max_depth,
        )
        return [FriendlyNameResult.from_legacy_dict(row) for row in legacy_rows]
