"""athena-batch3 fix 1: FA2 — the batch dispatcher seam (2026-10-30).

Campaign athena-batch3. FRESH EVIDENCE — every probe executed this
run against the fixed dispatcher; no prior-run results cited.

FA2 (MEDIUM) RESOLVED: batch entries previously bypassed the
W4/U3 unknown-param rejection on all five ops AND the validation
wings (H1 version, EB date) — a 2-transports × 2-rejection-families
bypass matrix (TS-96/TS-97 pins). The dispatcher now carries the
SAME checks the direct routes run, before dispatch: per-op known
sets on GET entries (via a shim exposing the entry's parsed params
to reject_unknown_query_params), body-side name checks on POST
entries (_parameter_names_present), version/systemVersion/date
validation on whichever channel carries them, and $expand's
declared-but-unsupported names (the EA contract's second gate).

BX1 (MEDIUM) closes with it (the POST-body wing of the same seam).

Tests: all five ops × GET/POST unknown-param rejection + the three
validation wings + every happy path (incl. the property. dotted
prefix and expand offset — the known-set subtleties).
"""

from __future__ import annotations

SNOMED_URI = "http://snomed.info/sct"
ICD10CM_URI = "http://hl7.org/fhir/sid/icd-10-cm"
DM_ISA_URL = f"{SNOMED_URI}/73211009?fhir_vs=isa"
T2DM = "44054006"
DM = "73211009"


def _batch_get(fhir_client, url: str) -> str:
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


def _batch_post(fhir_client, path: str, params: list) -> str:
    r = fhir_client.post(
        "/fhir",
        json={
            "resourceType": "Bundle", "type": "batch",
            "entry": [{
                "request": {"method": "POST", "url": path},
                "resource": {
                    "resourceType": "Parameters",
                    "parameter": params,
                },
            }],
        },
    )
    return r.json()["entry"][0]["response"]["status"]


class TestUnknownParamsFiveOps:
    """FA2 GET wing — all five ops reject unknowns in batch."""

    def test_f1_lookup_get(self, fhir_client):
        assert _batch_get(
            fhir_client,
            f"CodeSystem/$lookup?system={SNOMED_URI}"
            f"&code={T2DM}&bogusParam=1",
        ) == "400"

    def test_f1_lookup_post(self, fhir_client):
        assert _batch_post(
            fhir_client, "CodeSystem/$lookup",
            [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": T2DM},
                {"name": "zzz", "valueString": "1"},
            ],
        ) == "400"

    def test_f2_cs_validate_get(self, fhir_client):
        assert _batch_get(
            fhir_client,
            f"CodeSystem/$validate-code?system={SNOMED_URI}"
            f"&code={T2DM}&bogusParam=1",
        ) == "400"

    def test_f2_cs_validate_post(self, fhir_client):
        assert _batch_post(
            fhir_client, "CodeSystem/$validate-code",
            [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": T2DM},
                {"name": "zzz", "valueString": "1"},
            ],
        ) == "400"

    def test_f3_vs_validate_get(self, fhir_client):
        assert _batch_get(
            fhir_client,
            f"ValueSet/$validate-code?url={DM_ISA_URL}"
            f"&system={SNOMED_URI}&code={T2DM}&bogusParam=1",
        ) == "400"

    def test_f4_translate_get(self, fhir_client):
        assert _batch_get(
            fhir_client,
            f"ConceptMap/$translate?system={SNOMED_URI}"
            f"&code={T2DM}&targetSystem={ICD10CM_URI}"
            "&bogusParam=1",
        ) == "400"

    def test_f4_translate_post(self, fhir_client):
        assert _batch_post(
            fhir_client, "ConceptMap/$translate",
            [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": T2DM},
                {
                    "name": "targetSystem",
                    "valueUri": ICD10CM_URI,
                },
                {"name": "zzz", "valueString": "1"},
            ],
        ) == "400"

    def test_f5_subsumes_get(self, fhir_client):
        assert _batch_get(
            fhir_client,
            f"CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={DM}&codeB={T2DM}&bogus=1",
        ) == "400"


class TestValidationWings:
    """FA2 validation wings — H1/EB fire in batch too."""

    def test_w1_lookup_version(self, fhir_client):
        assert _batch_get(
            fhir_client,
            f"CodeSystem/$lookup?system={SNOMED_URI}"
            f"&code={T2DM}&version=9.9",
        ) == "400"

    def test_w2_lookup_systemversion(self, fhir_client):
        assert _batch_get(
            fhir_client,
            f"CodeSystem/$lookup?system={SNOMED_URI}"
            f"&code={T2DM}&systemVersion=9.9",
        ) == "400"

    def test_w3_expand_date_garbage(self, fhir_client):
        assert _batch_get(
            fhir_client,
            f"ValueSet/$expand?url={DM_ISA_URL}&date=garbage",
        ) == "400"

    def test_w4_expand_designations(self, fhir_client):
        """The EA contract's SECOND gate: includeDesignations passes
        the known-set but the direct route rejects it explicitly —
        the batch seam now mirrors that."""
        assert _batch_get(
            fhir_client,
            f"ValueSet/$expand?url={DM_ISA_URL}"
            "&includeDesignations=true",
        ) == "400"

    def test_w5_validate_date_post_body(self, fhir_client):
        assert _batch_post(
            fhir_client, "CodeSystem/$validate-code",
            [
                {"name": "system", "valueUri": SNOMED_URI},
                {"name": "code", "valueCode": T2DM},
                {"name": "date", "valueDateTime": "garbage"},
            ],
        ) == "400"


class TestHappyPaths:
    """The contract that must NOT break."""

    def test_h1_all_ops(self, fhir_client):
        assert _batch_get(
            fhir_client,
            f"CodeSystem/$lookup?system={SNOMED_URI}&code={T2DM}",
        ) == "200"
        assert _batch_get(
            fhir_client,
            f"CodeSystem/$validate-code?system={SNOMED_URI}"
            f"&code={T2DM}",
        ) == "200"
        assert _batch_get(
            fhir_client,
            f"ValueSet/$validate-code?url={DM_ISA_URL}"
            f"&system={SNOMED_URI}&code={T2DM}",
        ) == "200"
        assert _batch_get(
            fhir_client, f"ValueSet/$expand?url={DM_ISA_URL}",
        ) == "200"
        assert _batch_get(
            fhir_client,
            f"ConceptMap/$translate?system={SNOMED_URI}"
            f"&code={T2DM}&targetSystem={ICD10CM_URI}",
        ) == "200"
        assert _batch_get(
            fhir_client,
            f"CodeSystem/$subsumes?system={SNOMED_URI}"
            f"&codeA={DM}&codeB={T2DM}",
        ) == "200"

    def test_h2_lookup_dotted_property(self, fhir_client):
        """The property. known-prefix survives (R4 dotted
        sub-selectors)."""
        assert _batch_get(
            fhir_client,
            f"CodeSystem/$lookup?system={SNOMED_URI}"
            f"&code={T2DM}&property.code=cui",
        ) == "200"

    def test_h3_expand_offset(self, fhir_client):
        assert _batch_get(
            fhir_client,
            f"ValueSet/$expand?url={DM_ISA_URL}&offset=1",
        ) == "200"
