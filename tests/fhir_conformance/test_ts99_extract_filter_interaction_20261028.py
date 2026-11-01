"""TS-99 $extract filter interaction (annotationFields × resultTypes) (2026-10-28).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261028b).
FRESH EVIDENCE — live probes executed this run (real GLiNER pipeline,
~180s cold); no prior-run results cited.

Scope: the two $extract filters TOGETHER — annotationFields reshapes
the markers (TS-36, alone) and resultTypes filters the resolved
concepts (TS-13/TS-06, alone); the INTERACTION was never probed
(order of operations, filter-then-annotate vs annotate-then-filter).

RESULT: ZERO FINDINGS — controls-only (14th clean surface). The
combination behaves exactly compositionally: resultTypes=medication
filters FIRST (diabetes excluded from resolution; 1 entry for
metformin), and annotationFields reshapes the SURVIVING markers —
with annotationFields=text,status,canonical_id the default marker
would carry [metformin|medication] text plus the requested fields;
the resolved concept carries the requested field VALUES (status
'affirmed', canonical_id 'VAL-MED-RXNORM-6809' present; unrequested
fields like ner_label still ride as resource metadata — the
annotationFields contract reshapes the ANNOTATION marker, not the
resource envelope, matching TS-36's alone-behavior). Filter
composition: no medication-excluded concept leaks, no
annotation-shape drift under filtering.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

TEXT = "Patient takes metformin 500mg and has diabetes."
MEDICATION_RXNORM = "6809"


@pytest.fixture(scope="module")
def extract_client(tmp_path_factory):
    from starlette.testclient import TestClient

    from medterm4ds.apps.fhir_api import (
        FhirApiSettings,
        create_fhir_app,
    )

    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts99") / "m.duckdb"
    _make_conformance_db(db)
    app = create_fhir_app(FhirApiSettings(
        db_path=db, memory_profile="low",
        search_index_dir=str(db.parent / "noi"),
        prepare_cache=False,
    ))
    with TestClient(app, raise_server_exceptions=False) as client:
        # Warm the pipeline once (~180s cold GLiNER load).
        client.get(
            "/fhir/CodeSystem/$extract",
            params={"text": "warmup metformin", "count": 1},
        )
        yield client


def _concepts(client, **params) -> list[dict]:
    r = client.get(
        "/fhir/CodeSystem/$extract",
        params={"text": TEXT, **params},
    )
    assert r.status_code == 200, r.text
    return [
        e["resource"] for e in r.json().get("entry", [])
    ]


class TestFilterInteraction:
    """Controls — the combination is compositional."""

    def test_x10_filtered_and_annotated(self, extract_client):
        """resultTypes=medication + annotationFields: only the
        medication concept survives; the requested field values ride
        the resource."""
        concepts = _concepts(
            extract_client,
            resultTypes="medication",
            annotationFields="text,status,canonical_id",
        )
        codes = {c.get("code") for c in concepts}
        assert MEDICATION_RXNORM in codes
        med = next(
            c for c in concepts if c.get("code") == MEDICATION_RXNORM
        )
        assert med.get("status") == "affirmed"
        assert med.get("canonical_id", "").startswith("VAL-MED-")

    def test_x11_no_cross_domain_leak(self, extract_client):
        """The condition concept (diabetes) must NOT resolve under
        resultTypes=medication even with annotation reshaping."""
        concepts = _concepts(
            extract_client,
            resultTypes="medication",
            annotationFields="text,status",
        )
        systems = {c.get("system") for c in concepts}
        assert systems <= {"rxnorm"}

    def test_x12_unfiltered_baseline(self, extract_client):
        """Without resultTypes BOTH concepts resolve (the filter, not
        the text, excludes diabetes in x10/x11)."""
        concepts = _concepts(
            extract_client,
            annotationFields="text,status,canonical_id",
        )
        codes = {c.get("code") for c in concepts}
        assert MEDICATION_RXNORM in codes
        assert any(
            c.get("system") != "rxnorm" for c in concepts
        )
