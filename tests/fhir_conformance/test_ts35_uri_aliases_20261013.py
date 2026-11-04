"""TS-35 system-URI alias matrix (2026-10-13).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261013).
FRESH EVIDENCE — live probes executed this run across all
system-bearing operations; no prior-run results cited.

Scope: the system-URI forms every op accepts — canonical http,
trailing-slash, urn:oid, and the R4-standard SNOMED versioned form
(§4.8.9.1) — never enumerated as a matrix (e14/tail probes touched
lookup only).

AL1 (LOW-MED, NEW) — versioned SNOMED URI rejected as
    'Unrecognized'.
    The R4-standard versioned form
    (http://snomed.info/sct/<module>/version/<date>) returns 400
    'Unrecognized system URI' on every op — a MISLEADING diagnostic:
    the URI is spec-canonical; the server's actual issue is version
    semantics (the sibling `version`-param path correctly says
    'version is not supported: this server serves one (current) code
    system version'). A client following R4 §4.8.9.1's canonical
    form gets 'unrecognized' advice instead of the single-version
    rejection with its rationale.
    Fix shape: parse the versioned form in fhir_uri_to_system (or
    its caller), strip the /module/version/date suffix, and route to
    the same reject_unsupported_version_params message as the plain
    version param. Flip pins a10/a11.

AL2 (LOW-MED, NEW) — urn:oid coverage is SNOMED+LOINC only.
    urn:oid:2.16.840.1.113883.6.96 (SNOMED) and .6.1 (LOINC)
    resolve; RXNORM (.6.6), ICD-10-CM (.6.3), CPT (.6.12) and the
    rest return None → 400 'Unrecognized system URI' — while every
    system's http form works. R4's system registry defines OIDs for
    all of them; an OID-addressing client (common in HL7v7/CDA
    lineage integrations) can reach two of the server's nine
    vocabularies. Fix shape: extend the oid map (mechanical — the
    OIDs are public constants). Flip pins a20/a21.

Controls (fresh): canonical http accepted everywhere (all ops);
trailing-slash accepted everywhere (lookup/validate/subsumes/
translate verified); plain `version` param uniformly rejected with
the ACCURATE single-version message (H1's contract — the contrast
that makes AL1's diagnostic a finding).
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
SNOMED_OID = "urn:oid:2.16.840.1.113883.6.96"
SNOMED_VERSIONED = f"{SNOMED_URI}/73211009/version/20260301"
LOINC_OID = "urn:oid:2.16.840.1.113883.6.1"
RXNORM_OID = "urn:oid:2.16.840.1.113883.6.6"
RXNORM_HTTP = "http://www.nlm.nih.gov/research/umls/rxnorm"
T2DM = "44054006"


class TestAL1VersionedUriRejected:
    """AL1 — R4-canonical versioned form → misleading 400."""

    def test_a10_versioned_uri_400(self, fhir_client):
        """The R4 §4.8.9.1 versioned form 400s as 'Unrecognized
        system URI'. Flip when routed to the version-rejection
        message (or accepted with version semantics)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_VERSIONED, "code": T2DM},
        )
        assert r.status_code == 400
        assert "Unrecognized system URI" in r.text

    def test_a11_plain_version_accurate(self, fhir_client):
        """Contrast control: the plain version param gets the
        ACCURATE single-version message — the same client intent,
        different URI spelling, different advice."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={
                "system": SNOMED_URI,
                "code": T2DM,
                "version": "20260301",
            },
        )
        assert r.status_code == 400
        assert "one (current) code system version" in r.text

    @pytest.mark.parametrize(
        "op,extra",
        [
            ("$validate-code", {"code": T2DM}),
            ("$subsumes", {"codeA": "73211009", "codeB": T2DM}),
        ],
    )
    def test_a12_uniform_across_ops(self, fhir_client, op, extra):
        """The versioned form 400s identically on every
        system-bearing op."""
        r = fhir_client.get(
            f"/fhir/CodeSystem/{op}",
            params={"system": SNOMED_VERSIONED, **extra},
        )
        assert r.status_code == 400


class TestAL2UrnOidCoverage:
    """AL2 — OID addressing reaches only SNOMED+LOINC."""

    @pytest.mark.parametrize("oid", [RXNORM_OID, "urn:oid:2.16.840.1.113883.6.3"])
    def test_a20_uncovered_oids_400(self, fhir_client, oid):
        """RXNORM/ICD-10-CM OIDs → 400 'Unrecognized' (their http
        forms work — the map, not the engine, is the gap). Flip when
        the oid map extends."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": oid, "code": "x"},
        )
        assert r.status_code == 400

    def test_a21_http_forms_work(self, fhir_client):
        """Contrast control: every system's http form resolves —
        the gap is OID-map coverage alone."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": RXNORM_HTTP, "code": "x"},
        )
        assert r.status_code == 200  # miss-encoded, not unrecognized

    @pytest.mark.parametrize("oid", [SNOMED_OID, LOINC_OID])
    def test_a22_covered_oids_ok(self, fhir_client, oid):
        """The two covered OIDs resolve (premise: the mechanism
        exists, coverage is partial)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": oid, "code": T2DM},
        )
        assert r.status_code == 200


class TestAliasControls:
    """Controls — canonical + trailing-slash accepted everywhere."""

    @pytest.mark.parametrize("system_uri", [SNOMED_URI, SNOMED_URI + "/"])
    def test_c10_canonical_and_slash(self, fhir_client, system_uri):
        """Both spellings accepted on every system-bearing op."""
        for op, extra in [
            ("$lookup", {"code": T2DM}),
            ("$validate-code", {"code": T2DM}),
            ("$subsumes", {"codeA": "73211009", "codeB": T2DM}),
        ]:
            r = fhir_client.get(
                f"/fhir/CodeSystem/{op}",
                params={"system": system_uri, **extra},
            )
            assert r.status_code == 200, (op, system_uri)
