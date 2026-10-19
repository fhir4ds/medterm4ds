"""TS-62 $closure OUTPUT semantics (2026-10-18).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261018e).
FRESH EVIDENCE — live probes executed this run; spec fetched live
(hl7.org/fhir/R4/conceptmap-operation-closure.html, Out table quoted
in-test); no prior-run results cited.

Scope: the $closure OUTPUT side. Input/mounting were covered
(L1/L2/L3, C1 poison, CM03-01 token shape); the response CONTENT was
never audited against §4.9.16.2's Out contract. R4 live quote:

    return 1..1 ConceptMap — "A list of new entries (code/system ->
    code/system) that the client should add to its closure table. The
    only kind of entry mapping equivalences that can be returned are
    equal, specializes, subsumes and unmatched. Note: as this is the
    only out parameter, it is a resource, ... the result of this
    operation is returned directly as a resource."

CL1+CM03-01 (extension, MEDIUM) — response wrapper is Parameters, not
    the ConceptMap resource returned directly. The spec's example
    response body IS a ConceptMap (identifier/version/status + group
    entries). CM03-01 registered the valueString-token-vs-ConceptMap
    type mismatch; this adds the WRAPPER dimension: even a
    resource-valued `return` inside Parameters is not "returned
    directly as a resource". Fix shape: return the ConceptMap as the
    response body (Parameters wrapper dropped) or keep Parameters and
    document the deviation. Pinned cl10.

CL2 (MEDIUM, extends C1) — NO equivalence information anywhere in the
    response. The spec's closure contract exists so the client builds
    a LOCAL subsumption table; the equivalences (equal / specializes /
    subsumes / unmatched — exactly the four C1 said were unused) are
    the payload. The server echoes a FLAT concept list: adding
    73211009 + 44054006 (parent/child) + 3738000 (unrelated) returns
    three display-only codings with ZERO relationship entries — the
    client cannot distinguish related from unrelated inputs and must
    re-ask the server for every pair (defeating $closure's purpose).
    Fix shape: build the ConceptMap groups from the closure table's
    known relations (the manager already walks ancestors/descendants
    for incomplete detection). Pinned cl20/cl21.

CL3 (LOW) — echo is ALL concepts, not the NEW entries. Spec: "a list
    of NEW entries ... the client should add". Every call re-transmits
    the full table (grows linearly with the closure); semantically
    safe (client merge is idempotent) but bandwidth-wasteful and
    divergent from the incremental contract. Pinned cl30.

L3 RE-VERIFIED ALIVE (MEDIUM, registered TS-14): the version-resync
    wipe survives every fix-batch — 'version' is a DECLARED param
    (passes the W4 known-set gate) so a resync request (version=<stale
    token>, no concepts) still falls into the reset branch and WIPES
    the closure at 200 (grown token 78752450f266 → empty-table hash
    77509ea1ea0d). Pinned cl40/cl41 fresh; fix = honor version as
    resync-since (return entries newer than the token) or 501, never
    reset.

Controls (fresh): init/reset 200 + token + incomplete=false (empty
    table, no failed walks); add lifecycle token CHANGES on growth;
    incomplete=false on clean fixture walks; concept echo carries
    system+code+display (the display data is correct — CL2's gap is
    relationships, not display); unknown-code poison (C1) still
    400s first-unknown.
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
DM = "73211009"
T2DM = "44054006"
HEPATITIS = "3738000"  # unrelated to the DM subtree
EMPTY_TABLE_HASH_PREFIX = "77509ea1ea0d"


def _closure(client, params):
    return client.post(
        "/fhir/CodeSystem/$closure",
        json={
            "resourceType": "Parameters",
            "parameter": params,
        },
    )


def _token(body: dict):
    for p in body.get("parameter", []):
        if p.get("name") == "return":
            return p.get("valueString")
    return None


def _concepts(body: dict):
    return [
        p.get("valueCoding", {})
        for p in body.get("parameter", [])
        if p.get("name") == "concept"
    ]


@pytest.fixture(scope="module")
def closure_name():
    return "ts62-fresh"


class TestCL1Wrapper:
    """CL1 — ConceptMap returned directly vs Parameters wrapper."""

    def test_cl10_response_is_parameters(self, fhir_client, closure_name):
        """The response resourceType is Parameters; R4 §4.9.16.2 note
        says the ConceptMap result "is returned directly as a
        resource". Folds into CM03-01's token fix. Flip when the body
        is a ConceptMap."""
        r = _closure(fhir_client, [
            {"name": "name", "valueString": closure_name},
        ])
        assert r.status_code == 200
        assert r.json()["resourceType"] == "Parameters"


class TestCL2NoEquivalences:
    """CL2 — flat echo, zero relationship entries."""

    def test_cl20_related_unrelated_indistinguishable(
        self, fhir_client, closure_name
    ):
        """Adding parent+child+unrelated returns three display-only
        codings: no equal/specializes/subsumes/unmatched entries
        anywhere in the response. Flip when the ConceptMap groups (or
        equivalent relationship parts) land."""
        _closure(fhir_client, [
            {"name": "name", "valueString": closure_name},
        ])  # reset
        r = _closure(fhir_client, [
            {"name": "name", "valueString": closure_name},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": DM}},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": T2DM}},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": HEPATITIS}},
        ])
        assert r.status_code == 200
        text = r.text
        for eq in ("equal", "specializes", "subsumes", "unmatched"):
            assert eq not in text

    def test_cl21_echo_carries_display_data(self, fhir_client, closure_name):
        """The echo is not empty data — codings carry system+code+
        display (the gap is relationships, not display). Premise
        control for cl20."""
        r = _closure(fhir_client, [
            {"name": "name", "valueString": closure_name},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": T2DM}},
        ])
        assert r.status_code == 200
        concepts = _concepts(r.json())
        assert any(
            c.get("code") == T2DM and c.get("display")
            for c in concepts
        )


class TestCL3FullEcho:
    """CL3 — all concepts re-transmitted, not new entries."""

    def test_cl30_growth_echoes_everything(
        self, fhir_client, closure_name
    ):
        """After adding A, a second call adding B echoes BOTH A and B
        (the spec asks for the NEW entries only). Semantically safe
        (idempotent merge), bandwidth-divergent. Flip when incremental
        entries land."""
        _closure(fhir_client, [
            {"name": "name", "valueString": closure_name},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": DM}},
        ])
        r = _closure(fhir_client, [
            {"name": "name", "valueString": closure_name},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": T2DM}},
        ])
        codes = {c.get("code") for c in _concepts(r.json())}
        assert DM in codes and T2DM in codes


class TestL3ResyncWipeAlive:
    """L3 re-verified — version-resync (declared param) wipes."""

    def test_cl40_resync_wipes(self, fhir_client, closure_name):
        """version=<stale> with no concepts → reset branch → empty-table
        hash. Registered TS-14 (l30); survives all fix-batches because
        'version' passes the W4 known-set gate. Flip when resync
        semantics (or 501) land."""
        grown = _closure(fhir_client, [
            {"name": "name", "valueString": closure_name},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": T2DM}},
        ])
        t_grown = _token(grown.json())
        assert not t_grown.startswith(EMPTY_TABLE_HASH_PREFIX)

        resync = _closure(fhir_client, [
            {"name": "name", "valueString": closure_name},
            {"name": "version", "valueString": "stale-token"},
        ])
        assert resync.status_code == 200
        assert _token(resync.json()).startswith(EMPTY_TABLE_HASH_PREFIX)

    def test_cl41_version_is_declared(self, fhir_client, closure_name):
        """Premise: 'version' passes the W4 known-set gate (a resync
        request is not an unknown-param 400 — it reaches the reset
        branch). Contrast with the closed L1 typo variant."""
        r = _closure(fhir_client, [
            {"name": "name", "valueString": closure_name},
            {"name": "version", "valueString": "stale"},
            {"name": "zzz", "valueString": "x"},  # unknown → 400 first
        ])
        assert r.status_code == 400


class TestTS62Controls:
    """Controls — lifecycle mechanics held."""

    def test_c10_init_token_and_complete(self, fhir_client):
        r = _closure(fhir_client, [
            {"name": "name", "valueString": "ts62-ctl-init"},
        ])
        assert r.status_code == 200
        assert _token(r.json()) is not None
        incomplete = next(
            (p.get("valueBoolean") for p in r.json()["parameter"]
             if p.get("name") == "incomplete"),
            None,
        )
        assert incomplete is False

    def test_c11_token_changes_on_growth(
        self, fhir_client, closure_name
    ):
        before = _closure(fhir_client, [
            {"name": "name", "valueString": closure_name},
        ])
        after = _closure(fhir_client, [
            {"name": "name", "valueString": closure_name},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": DM}},
        ])
        assert _token(before.json()) != _token(after.json())

    def test_c12_unknown_code_poison_400(self, fhir_client):
        """C1 contract held: unknown code fails loudly."""
        r = _closure(fhir_client, [
            {"name": "name", "valueString": "ts62-ctl-poison"},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": "99999999"}},
        ])
        assert r.status_code == 400
