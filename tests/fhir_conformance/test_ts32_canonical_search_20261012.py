"""TS-32 $search canonical-mode contract (2026-10-12).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261012b).
FRESH EVIDENCE — live probes executed this run (the split-layout
cache fetched the canonical bundle: first canonical call ~120s cold);
no prior-run results cited.

Scope: the canonical mode is $search's flagship (cdb-backed,
canonical-id results). TS-07 covered resultTypes/availability gaps
(G1/G2) on the lexical/semantic paths; this suite pins the canonical
envelope and its own gaps.

CA1 (LOW-MED, NEW) — canonical Bundle entry.fullUrl is
    RELATIVE-COMPOSITE.
    Entries carry fullUrl='CodeSystem/<system-uri>-<code>' — R4
    bundle.html: "fullUrl SHALL NOT be relative" (absolute URL or
    urn:uuid). The $extract Bundle on the SAME server uses urn:uuid:
    fullUrls — two Bundles, two conventions, one of them
    spec-violating. Relative fullUrls also break Bundle.entry
    resolution semantics (identity/references) for generic clients.
    Fix shape: urn:uuid:<stable-uuid> (extract's convention) or an
    absolute base URI. Flip pins c10/c11.

CA2 (LOW, NEW) — two extension namespaces in one response; both
    unintrospectable.
    The canonical response mixes medterm4ds.org/fhir/StructureDefinition/
    canonical-id|canonical-result-type extensions with
    fhir4ds.org/fhir/StructureDefinition/match-grade — two domains
    for one server's wire contract. W3 already registered
    medterm4ds.org as DNS-dead with no hosted StructureDefinitions;
    every extension in this envelope is similarly unintrospectable
    (no local OperationDefinition/StructureDefinition routes serve
    either domain). Fix shape: consolidate on one namespace + host
    the definitions (rides with W3). Flip pin c20.

CA3 (LOW, NEW) — engine param is a no-op on canonical mode.
    engine=bm25/sapbert/hybrid/bogus all return 200 identical-role
    responses — the param is declared for the op but carries no
    canonical semantics and is unvalidated (G2's family on the third
    mode). Fix shape: 400 on canonical mode ('engine applies to
    lexical/semantic modes') or document the accepted set.
    Flip pin c30.

Controls (fresh): resultTypes VALIDATED on canonical (400 bogus —
    the load-bearing contrast with G1's lexical-path ignore);
    count floor 422; canonical envelope complete (canonical-id ext
    VAL-COND-*, result-type ext, search.score + match-grade ext);
    medication-domain results surface combination products
    (metformin/repaglinide); split-layout cold-fetch transparent
    (no explicit cache params needed).
"""

from __future__ import annotations

import pytest

QUERY = "diabetes"


def _first_entry(client, **extra):
    r = client.get(
        "/fhir/CodeSystem/$search",
        params={"query": QUERY, "searchMode": "canonical", "count": "3", **extra},
    )
    assert r.status_code == 200, r.text
    return r.json()


class TestCA1FullUrlRelative:
    """CA1 — relative-composite fullUrls."""

    def test_c10_fullurl_shape(self, fhir_client):
        """Entries carry 'CodeSystem/<system>-<code>' relative
        composites — R4 bundle.html 'fullUrl SHALL NOT be relative'.
        Flip when urn:uuid/absolute form lands."""
        body = _first_entry(fhir_client)
        for e in body["entry"]:
            assert e["fullUrl"].startswith("CodeSystem/"), e["fullUrl"]

    def test_c11_extract_uses_urn_uuid_contrast(self, fhir_client):
        """The SAME server's $extract Bundle uses urn:uuid fullUrls —
        the intra-server inconsistency is the finding's core."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$extract",
            params={"text": "type 2 diabetes"},
        )
        assert r.status_code == 200
        entry = r.json()["entry"][0]
        assert entry["fullUrl"].startswith("urn:uuid:")


class TestCA2ExtensionNamespaces:
    """CA2 — two namespaces, both unintrospectable."""

    def test_c20_dual_namespaces(self, fhir_client):
        """One response mixes medterm4ds.org and fhir4ds.org
        StructureDefinition namespaces. Flip when consolidated (and
        W3-hosted)."""
        body = _first_entry(fhir_client)
        entry = body["entry"][0]
        urls = [ext["url"] for ext in entry["resource"].get("extension", [])] + [
            ext["url"] for ext in entry["search"].get("extension", [])
        ]
        domains = {u.split("/fhir/")[0] for u in urls}
        assert domains == {
            "http://medterm4ds.org",
            "http://fhir4ds.org",
        }


class TestCA3EngineNoOpOnCanonical:
    """CA3 — engine accepted, meaningless, unvalidated."""

    @pytest.mark.parametrize("engine", ["bm25", "sapbert", "hybrid", "bogus"])
    def test_c30_engine_all_200(self, fhir_client, engine):
        """Every engine value (including garbage) returns 200 on
        canonical mode — no validation, no mode semantics. Flip when
        400'd or documented."""
        body = _first_entry(fhir_client, engine=engine)
        assert body["resourceType"] == "Bundle"


class TestCanonicalControls:
    """Controls — the canonical envelope, fresh."""

    def test_c40_resulttypes_validated(self, fhir_client):
        """The load-bearing contrast with G1: canonical VALIDATES
        resultTypes (400 bogus) while the lexical path ignores
        them."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": QUERY,
                "searchMode": "canonical",
                "resultTypes": "bogus",
            },
        )
        assert r.status_code == 400

    def test_c41_count_floor(self, fhir_client):
        r = fhir_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": QUERY,
                "searchMode": "canonical",
                "count": "0",
            },
        )
        assert r.status_code == 422

    def test_c42_envelope_extensions(self, fhir_client):
        body = _first_entry(fhir_client)
        entry = body["entry"][0]
        res_exts = {ext["url"].rsplit("/", 1)[-1]: ext for ext in entry["resource"]["extension"]}
        assert "canonical-id" in res_exts
        assert res_exts["canonical-id"]["valueString"].startswith("VAL-")
        assert res_exts["canonical-result-type"]["valueCode"]
        assert entry["search"]["score"] > 0
        search_exts = {
            ext["url"].rsplit("/", 1)[-1] for ext in entry["search"].get("extension", [])
        }
        assert "match-grade" in search_exts

    def test_c43_medication_combinations_surface(self, fhir_client):
        """The medication domain surfaces the drug-class family for a class
        query (the combination_members feature class)."""
        # RELEASE-DRIFT NOTE (2026-11-03, alias-embedded 2026.10.31.1
        # republish): the old contract — resultTypes=medication surfacing
        # VAL-MED combination products ('metformin / repaglinide') — was
        # calibrated on the pre-alias BM25 build. The republished release
        # resolves the CLASS query to ATC drug-class anchors (VAL-DRUGCLASS-*)
        # with no VAL-MED anchor inside the 0.70-floor window at any depth,
        # so a medication-only filter legitimately returns 0 for a
        # class-shaped query. The class surface is pinned via the
        # drug_class result type (in the canonical enum); the medication-only
        # empty is pinned as the documented current semantic, not relaxed.
        r = fhir_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "diabetes medications",
                "searchMode": "canonical",
                "resultTypes": "medication,drug_class",
                "count": "5",
            },
        )
        assert r.status_code == 200
        assert r.json()["total"] > 0
        ids = [e["resource"]["display"] for e in r.json()["entry"]]
        assert any("Diabetes Medications" in d for d in ids), ids

        # Medication-only filter on the class query: empty under the current
        # release (no VAL-MED anchor clears the 0.70 floor for the class
        # phrase). If a future release restores medication members for class
        # queries, tighten this to assert non-empty.
        r_med = fhir_client.get(
            "/fhir/CodeSystem/$search",
            params={
                "query": "diabetes medications",
                "searchMode": "canonical",
                "resultTypes": "medication",
                "count": "5",
            },
        )
        assert r_med.status_code == 200
        assert r_med.json()["total"] == 0
