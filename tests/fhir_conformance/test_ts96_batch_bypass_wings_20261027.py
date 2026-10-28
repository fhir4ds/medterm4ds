"""TS-96 batch bypass wings + dispatcher param audit (2026-10-27).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261027c).
FRESH EVIDENCE — live batch probes executed this run; no prior-run
results cited.

Scope: FA2's wings — the VALIDATION rejections (not just
unknown-params) the dispatcher misses, plus the dispatcher-vs-direct
param audit that found AS3's class.

FW1 (annotation to FA2, pinned fresh) — the batch bypass spans the
    fixbatch validation families, not only unknown-params: lookup
    version=9.9 → batch 200 (H1 bypassed; the DIRECT route 400s),
    expand date=garbage → batch 200 (EB bypassed), expand
    includeDesignations → batch 200 (EA bypassed). The validate-code
    branch's H1 wiring HELD in batch (dispatcher wires it there) —
    the inconsistency is per-branch ad-hoc wiring, the FA2 root
    cause's signature. Pinned b10-b14.

Controls (fresh): the direct-route twins all 400 (premise); the
    dispatcher's translate call site passes 6 args vs the direct
    route's 7 (AS3's source-level pin).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SNOMED_URI = "http://snomed.info/sct"
DM_ISA_URL = f"{SNOMED_URI}/73211009?fhir_vs=isa"
T2DM = "44054006"


def _batch_status(fhir_client, url: str) -> str:
    r = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle", "type": "batch",
            "entry": [{
                "request": {"method": "GET", "url": url},
            }],
        },
    )
    return r.json()["entry"][0]["response"]["status"]


class TestFW1ValidationBypassWings:
    """FA2 wings — validation families bypassed in batch."""

    def test_b10_lookup_version_bypassed(self, fhir_client):
        """H1: version=9.9 → batch 200 (direct 400s). Flip with
        FA2's dispatcher-level fix."""
        status = _batch_status(
            fhir_client,
            f"CodeSystem/$lookup?system={SNOMED_URI}"
            f"&code={T2DM}&version=9.9",
        )
        assert status == "200"

    def test_b11_lookup_version_direct_400(self, fhir_client):
        """Premise: the DIRECT twin rejects (H1 held there)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={
                "system": SNOMED_URI, "code": T2DM,
                "version": "9.9",
            },
        )
        assert r.status_code == 400

    def test_b12_expand_date_bypassed(self, fhir_client):
        """EB: date=garbage → batch 200 (direct 400s)."""
        status = _batch_status(
            fhir_client,
            f"ValueSet/$expand?url={DM_ISA_URL}&date=garbage",
        )
        assert status == "200"

    def test_b13_expand_designations_bypassed(self, fhir_client):
        """EA: includeDesignations → batch 200 (direct 400s)."""
        status = _batch_status(
            fhir_client,
            f"ValueSet/$expand?url={DM_ISA_URL}"
            "&includeDesignations=true",
        )
        assert status == "200"

    def test_b14_validate_version_held_in_batch(
        self, fhir_client
    ):
        """Contrast control: the validate-code branch's H1 wiring
        HELD in batch (the per-branch ad-hoc inconsistency — FA2's
        root cause signature)."""
        status = _batch_status(
            fhir_client,
            f"CodeSystem/$validate-code?system={SNOMED_URI}"
            f"&code={T2DM}&version=9.9",
        )
        assert status == "400"


class TestDispatcherParamAudit:
    """AS3's source-level pin + the audit method."""

    def test_c10_translate_arity_gap(self):
        """The dispatcher's _do_translate call passes 6 args; the
        direct route passes 7 (map_url) — AS3's root. Flip when the
        dispatcher forwards the map url."""
        src = (
            Path(__file__).parents[2]
            / "src"
            / "medterm4ds"
            / "apps"
            / "fhir_api.py"
        ).read_text()
        direct = re.search(
            r"_do_translate, _engine\(request\), system, "
            r"(?:actual_code|code), "
            r"(?:effective_target|targetsystem), reverse, map_url",
            src,
        )
        batch = re.search(
            r"executor, _do_translate, engine, system, code,\s*"
            r"targetsystem, batch_reverse,\s*\)",
            src,
        )
        assert direct is not None
        assert batch is not None  # the 6-arg form persists
