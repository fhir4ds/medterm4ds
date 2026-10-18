"""TS-60 $expand URL-pattern conventions matrix (2026-10-18).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261018c).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the implicit ValueSet URL convention matrix (R4 §4.7.3.1) —
fhir_vs forms, all-of-system /vs forms, versioned-system urls,
malformed roots, url|version suffixes, instance-level $expand. The
isa happy path is heavily probed by earlier suites; the CONVENTION
MATRIX was never swept as one surface.

UP1 (LOW, NEW) — url|version suffix fails with a MISLEADING
    diagnostic. R4 §3.1.0.7 canonical url|version form
    (...fhir_vs=isa|1.0) is rejected as "Unsupported fhir_vs value:
    'isa|1.0'" — the diagnostic frames it as an unknown fhir_vs KIND
    (listing isa/refset as supported) when the actual input was the
    spec's url version-suffix convention applied to a supported
    value. A client reading the message concludes it spelled the
    KIND wrong. Same diagnostic-clarity family as BE1 (foreign-base
    404) and AL1 (versioned SNOMED URI). Single-version rejection is
    the right verdict (H1/N2 family); the message should say so.
    Fix shape: parse the |version suffix, reject with 'unversioned
    single-snapshot ValueSets; version suffix not supported'. Flip
    pin up10.

CONTROLS (fresh, all CONFORMANT — the convention matrix is clean):
    - isa standard + spec-canonical (?fhir_vs=isa/<code>) both expand
      identically (alias pair).
    - ?fhir_vs (no value) = all-of-system; /vs forms work for SNOMED
      and LOINC.
    - Versioned SYSTEM in url (723100002/version/...) accepted —
      identifies the served edition (consistent with single-version).
    - refset/expression/members fhir_vs kinds fail loudly naming the
      supported values.
    - Nonnumeric + unknown roots 400 (QC-247 held) incl. the
      trailing-slash root form.
    - Trailing & and extra query params tolerated in the url.
    - urn url + instance-level /ValueSet/{id}/$expand fail loudly
      (no stored ValueSets — the documented P1/P3 stub doctrine).
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
LOINC_VS = "http://loinc.org/vs"
T2DM = "44054006"
DM = "73211009"


class TestUP1UrlVersionSuffix:
    """UP1 — url|version diagnostic misframes the failure."""

    def test_up10_version_suffix_misleading_400(self, fhir_client):
        """The canonical url|version form 400s framing the input as
        an unsupported fhir_vs KIND (message lists isa/refset as
        'supported') — the client is misdirected at the kind when
        the real fact is the version suffix. Flip when the
        diagnostic names versions (or the suffix is honored)."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": f"{SNOMED_URI}/{DM}?fhir_vs=isa|1.0"},
        )
        assert r.status_code == 400
        diag = r.json()["issue"][0]["diagnostics"]
        assert "fhir_vs value" in diag and "isa" in diag


class TestTS60Controls:
    """Controls — the convention matrix, fresh."""

    def test_c10_isa_alias_pair(self, fhir_client):
        """Standard and spec-canonical isa forms expand
        identically."""
        r1 = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": f"{SNOMED_URI}/{DM}?fhir_vs=isa"},
        )
        r2 = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": f"{SNOMED_URI}?fhir_vs=isa/{DM}"},
        )
        assert r1.status_code == r2.status_code == 200
        c1 = {e["code"] for e in r1.json()["expansion"]["contains"]}
        c2 = {e["code"] for e in r2.json()["expansion"]["contains"]}
        assert c1 == c2 == {DM, T2DM}

    def test_c11_fhir_vs_no_value_all_of_system(self, fhir_client):
        """?fhir_vs (no value) = the whole-system implicit set."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": f"{SNOMED_URI}?fhir_vs"},
        )
        assert r.status_code == 200
        assert r.json()["expansion"]["total"] >= 3

    @pytest.mark.parametrize(
        "url",
        [
            f"{SNOMED_URI}/vs",
            LOINC_VS,
        ],
    )
    def test_c12_all_of_system_vs_forms(self, fhir_client, url):
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": url, "count": 5},
        )
        assert r.status_code == 200
        assert len(r.json()["expansion"]["contains"]) >= 1

    def test_c13_versioned_system_url_accepted(self, fhir_client):
        """Versioned SYSTEM in the url identifies the served edition
        — accepted (contrast: block-level versions are the CV3
        finding; this form is the spec's own edition convention)."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={
                "url": (
                    "http://snomed.info/sct/723100002/version/20260301"
                    f"?fhir_vs=isa/{DM}"
                ),
            },
        )
        assert r.status_code == 200
        assert r.json()["expansion"]["total"] == 2

    @pytest.mark.parametrize(
        "kind", ["refset", "expression", "members"]
    )
    def test_c14_unsupported_kinds_fail_loudly(
        self, fhir_client, kind
    ):
        """Unimplemented fhir_vs kinds 400 naming supported values."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={
                "url": (
                    f"{SNOMED_URI}?fhir_vs={kind}"
                    if kind != "refset"
                    else f"{SNOMED_URI}/{DM}?fhir_vs={kind}"
                ),
            },
        )
        assert r.status_code == 400

    @pytest.mark.parametrize(
        "code", ["ABCDEF", "99999999"]
    )
    def test_c15_bad_roots_400(self, fhir_client, code):
        """Nonnumeric and unknown roots 400 (QC-247 unknown-root
        contract). NB: the trailing-slash case lives in the
        spec-canonical VALUE form ('?fhir_vs=isa/73211009/' → 400,
        pinned below) — the PATH form ('/73211009/?fhir_vs=isa')
        200s via slash normalization, consistent with the TS-14
        alias matrix."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": f"{SNOMED_URI}/{code}?fhir_vs=isa"},
        )
        assert r.status_code == 400

    def test_c15b_value_form_trailing_slash_400(self, fhir_client):
        """Spec-canonical value form with trailing slash on the code
        → 400 naming the code (the actual survey observation)."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": f"{SNOMED_URI}?fhir_vs=isa/73211009/"},
        )
        assert r.status_code == 400

    def test_c15c_path_form_trailing_slash_normalized(self, fhir_client):
        """Path form ('/code/?fhir_vs=isa') 200s — slash
        normalization, consistent with TS-14's trailing-slash
        aliases."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": f"{SNOMED_URI}/{DM}/?fhir_vs=isa"},
        )
        assert r.status_code == 200

    def test_c16_query_noise_tolerated(self, fhir_client):
        """Trailing & and an extra query param ride along fine."""
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": f"{SNOMED_URI}/{DM}?fhir_vs=isa&"},
        )
        assert r.status_code == 200

    def test_c17_urn_url_fails_loudly(self, fhir_client):
        r = fhir_client.get(
            "/fhir/ValueSet/$expand",
            params={"url": "urn:medterm4ds:vs:1"},
        )
        assert r.status_code == 400

    def test_c18_instance_level_expand_404(self, fhir_client):
        """[base]/ValueSet/{id}/$expand 404s (no stored ValueSets —
        documented stub doctrine P1/P3)."""
        r = fhir_client.get(
            "/fhir/ValueSet/snomed-isa-73211009/$expand"
        )
        assert r.status_code == 404
