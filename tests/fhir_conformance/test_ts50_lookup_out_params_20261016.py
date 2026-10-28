"""TS-50 $lookup OUT-parameter surface (2026-10-16).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261016c).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: $lookup's OUT side (TS-05's L1 covered the property IN-param;
the emitted parameters were never audited against R4 §4.4.18.1).

LK1 (LOW-MED, NEW) — seven medterm4ds-specific property codes with
    no declaring surface. The property parts emit cui/tty/aui
    (UMLS-internal) plus patient-friendly/match-type/canonical-code/
    canonical-system (medterm4ds extensions). R4: CodeSystem.concept.
    property SHOULD draw from the CodeSystem's declared property
    list; this server HAS no CodeSystem resource (P3: every id 404;
    P1: search inert) — so the extensions are UNDECLARABLE: a client
    cannot discover their meaning through any FHIR surface, and the
    W3-family unintrospectable-domains gap extends to property codes.
    Fix shape: declare them in the (future) CodeSystem resource or
    a TerminologyCapabilities property list (rides Y1's row). Flip
    pins k10/k11 when a declaring surface exists.

LK2 (LOW, NEW) — version Out param never emitted. R4 §4.4.18.1
    version (0..1) reports the code system version used; the server
    serves UMLS snapshot dates per-source but emits nothing. A
    clinical client cannot record WHICH terminology version answered
    its lookup. Fix shape: emit the source's version (the UMLS
    release/SAB version the engine carries). Flip pin k20.

Controls (fresh): the R4-required Out contract held — name (the
    system name 'SNOMED Clinical Terms (US)'), display 1..1 present,
    abstract boolean (R4's own example shows it — N1 iter-14);
    property parts carry code+value pairs; L1 re-verified (property
    In-param accepted-then-ignored: all four values byte-identical
    property lists — its TS-05 pins l10/l11 still green).
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
T2DM = "44054006"

_MEDTERM4DS_PROPERTY_CODES = {
    "patient-friendly", "match-type",
    "canonical-code", "canonical-system",
}


def _params_of(client, extra=None) -> dict:
    params = {"system": SNOMED_URI, "code": T2DM}
    params.update(extra or {})
    r = client.get("/fhir/CodeSystem/$lookup", params=params)
    assert r.status_code == 200, r.text
    return {
        p.get("name"): next(
            (v for k, v in p.items() if k.startswith("value")),
            None,
        )
        for p in r.json().get("parameter", [])
        if p.get("name") != "property"
    }


def _property_codes_of(client, extra=None) -> list[str]:
    params = {"system": SNOMED_URI, "code": T2DM}
    params.update(extra or {})
    r = client.get("/fhir/CodeSystem/$lookup", params=params)
    assert r.status_code == 200, r.text
    codes = []
    for p in r.json().get("parameter", []):
        if p.get("name") == "property":
            for q in p.get("part", []):
                if q.get("name") == "code":
                    codes.append(q.get("valueCode"))
    return codes






def _bm25_index_available() -> bool:
    """CI has the SapBERT model (downloads) but NOT the lexical BM25
    indexes — every suite needing real lexical/hybrid results guards
    on the index dir actually containing indexes."""
    from pathlib import Path

    from medterm4ds.apps.fhir_api import DEFAULT_SEARCH_INDEX_DIR

    d = Path(DEFAULT_SEARCH_INDEX_DIR)
    return d.exists() and any(d.glob("*_bm25.json"))

pytest.mark.skipif(not _bm25_index_available(), reason="BM25 indexes absent (CI)")
class TestLK1UndeclaredPropertyCodes:
    @pytest.fixture(autouse=True)
    def _need_overlay(self, fhir_client):
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": "http://snomed.info/sct", "code": "44054006"},
        )
        codes = set()
        if r.status_code == 200:
            for q in r.json().get("parameter", []):
                if q.get("name") == "property":
                    for part in q.get("part", []):
                        if part.get("name") == "code":
                            codes.add(part.get("valueCode"))
        if "patient-friendly" not in codes:
            pytest.skip("pf overlay not resolving (CI)")
    """LK1 — medterm4ds property extensions are undeclarable."""

    def test_k10_extension_property_codes_emitted(self, fhir_client):
        """The medterm4ds-specific property codes (patient-friendly,
        match-type, canonical-*) ARE emitted — the premise. Flip
        context: when a declaring surface exists, this stays green as
        the declared-extension control."""
        codes = set(_property_codes_of(fhir_client))
        assert _MEDTERM4DS_PROPERTY_CODES <= codes

    def test_k11_no_declaring_surface(self, fhir_client):
        """The declaring surfaces do not exist: CodeSystem read 404s
        for every id (P3) and search is inert (P1) — the extension
        codes cannot be discovered through any FHIR surface. Flip
        when a CodeSystem/TerminologyCapabilities declares them."""
        r = fhir_client.get("/fhir/CodeSystem/snomed")
        assert r.status_code == 404
        r2 = fhir_client.get(
            "/fhir/CodeSystem", params={"url": SNOMED_URI}
        )
        assert r2.status_code == 200
        assert r2.json()["total"] == 0


pytest.mark.skipif(not _bm25_index_available(), reason="BM25 indexes absent (CI)")
class TestLK2VersionNeverEmitted:
    """LK2 — version Out param absent."""

    def test_k20_version_absent(self, fhir_client):
        """R4 §4.4.18.1 version (0..1): never emitted — a clinical
        client cannot record which terminology version answered.
        Flip when the source version is emitted."""
        top = _params_of(fhir_client)
        assert "version" not in top


pytest.mark.skipif(not _bm25_index_available(), reason="BM25 indexes absent (CI)")
class TestLKControls:
    """Controls — the R4-required OUT contract, fresh."""

    def test_c10_name_is_system_name(self, fhir_client):
        top = _params_of(fhir_client)
        assert top.get("name") == "SNOMED Clinical Terms (US)"

    def test_c11_display_present(self, fhir_client):
        top = _params_of(fhir_client)
        assert top.get("display") == "Type 2 diabetes mellitus"

    def test_c12_abstract_boolean(self, fhir_client):
        top = _params_of(fhir_client)
        assert top.get("abstract") is False

    def test_c13_property_parts_code_value(self, fhir_client):
        """Every property part carries code+value (R4 property
        parts shape)."""
        r = fhir_client.get(
            "/fhir/CodeSystem/$lookup",
            params={"system": SNOMED_URI, "code": T2DM},
        )
        props = [
            p for p in r.json()["parameter"]
            if p.get("name") == "property"
        ]
        assert props
        for p in props:
            part_names = {q.get("name") for q in p.get("part", [])}
            assert {"code", "value"} <= part_names

    def test_c14_l1_property_inparam_ignored(self, fhir_client):
        """L1 re-verified: all property filter values yield the SAME
        property list (accepted-then-ignored). The TS-05 pins l10/l11
        remain the primary registration; this is the OUT-side
        cross-check."""
        baseline = _property_codes_of(fhir_client)
        for value in ("display", "cui", "bogus"):
            filtered = _property_codes_of(
                fhir_client, {"property": value}
            )
            assert filtered == baseline, value
