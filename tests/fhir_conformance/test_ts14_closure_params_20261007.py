"""TS-14 $closure parameter + mounting semantics (2026-10-07).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261007).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: $closure's In-parameter contract (name/concept/version), the
mounting point, and across-calls state semantics. Prior registrations:
CM03-01 (Out 'return' token vs 1..1 ConceptMap) and C1 (mixed-batch
poison). This sweep covers the INPUT side + spec location.

Spec research LIVE this run: R4 conceptmap-operation-closure.html —
In: name 1..1 string, concept 0..* Coding, version 0..1 string
("request to resynchronise — send all new entries since the nominated
version"); Out: return 1..1 ConceptMap ("list of NEW entries ... add
to its closure table", equivalences equal/specializes/subsumes/
unmatched); URL: [base]/$closure (system-level op on ConceptMap);
worked example shows concept-add via valueCoding.

Survey (fresh):
* Correct-form adds work: concept via valueCoding → 200; content-hash
  token CHANGES as concepts land (78752450 → 39dfa7a0 after growth) —
  version_hash contract (QC-283) held.
* incomplete flag surfaced false; reset-on-empty-body (no concept
  entries) is the documented init/reset semantic.

L1 (MEDIUM, NEW) — unknown In-parameter silently RESETS the closure.
    A request carrying ONLY unknown params (e.g. 'entities' — no such
    R4 param) sees saw_concept_entry=False → falls to the reset
    branch → 200 with the closure WIPED (fresh probe: token reverted
    to the empty-table hash 77509ea1…, concept echo 0 after a typo'd
    add-attempt). QC-264 guarded the all-malformed-concept case but a
    wrong PARAM NAME bypasses it. R4 $closure is NOT idempotent, but
    an add-intent request must never destroy state. Fix: reject
    bodies carrying non-(name|concept|version) params with 400, or at
    minimum never take the reset branch when ANY other param is
    present.

L2 (LOW-MED, NEW) — $closure mounted on the wrong resource; spec
    endpoints 405.
    R4 defines the op at [base]/$closure (ConceptMap-level). The
    server only serves /fhir/CodeSystem/$closure; /fhir/$closure and
    /fhir/ConceptMap/$closure both 405. The CapabilityStatement also
    declares closure under CodeSystem's operation list (metadata
    survey, TS-13 run). Fix: mount aliases at the spec URL(s) and/or
    declare under ConceptMap in the CapabilityStatement.

L3 (MEDIUM, NEW) — version resync requests WIPE the closure.
    In 'version' ("send all new entries since the nominated version")
    is read nowhere AND a version-carrying request has no concept
    entries, so it falls into the reset branch: fresh probe — resync
    against an old token after growth returned the EMPTY-TABLE hash
    (77509ea1…), destroying both concepts. Silent 200 + state loss.
    Same root as L1 (reset-on-no-concept-entries is too broad). Fix:
    treat version-carrying requests as resync (delta or explicit
    unsupported-501), never reset.
"""

from __future__ import annotations

import pytest

SNOMED_URI = "http://snomed.info/sct"
T2DM = "44054006"
DM = "73211009"
EMPTY_HASH_PREFIX = "77509ea1"  # empty-closure content hash prefix


def _closure(fhir_client, params):
    return fhir_client.post(
        "/fhir/CodeSystem/$closure",
        json={"resourceType": "Parameters", "parameter": params},
    )


def _token(resp_json) -> str:
    for q in resp_json["parameter"]:
        if q.get("name") == "return":
            return q.get("valueString", "")
    return ""


class TestL1UnknownParamResets:
    """L1 — unknown In-param silently wipes the closure."""

    def test_l10_add_lands(self, fhir_client):
        """Control: correct-form add → 200, non-empty-hash token."""
        r = _closure(fhir_client, [
            {"name": "name", "valueString": "l1"},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": T2DM}},
        ])
        assert r.status_code == 200
        assert not _token(r.json()).startswith(EMPTY_HASH_PREFIX)

    def test_l11_typo_param_wipes(self, fhir_client):
        """THE FINDING: after a successful add, a request with an
        unknown param name ('entities' — not an R4 In param) returns
        200 AND the closure is wiped (token = empty-table hash, zero
        concept echoes). WHEN the fix lands (400 on unknown params /
        no-reset-with-other-params), flip to preserved-state."""
        add = _closure(fhir_client, [
            {"name": "name", "valueString": "l1b"},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": T2DM}},
        ])
        assert add.status_code == 200
        grown = _closure(fhir_client, [
            {"name": "name", "valueString": "l1b"},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": DM}},
        ])
        t_grown = _token(grown.json())
        assert not t_grown.startswith(EMPTY_HASH_PREFIX)

        typo = _closure(fhir_client, [
            {"name": "name", "valueString": "l1b"},
            {"name": "entities",
             "valueString": '[{"code": "44054006"}]'},
        ])
        # c-fixbatch4 (W4): unknown body params on $closure now 400 at
        # the boundary — the typo'd add-request can no longer reach the
        # reset branch. L1 RESOLVED (destructive variant closed here;
        # the reset-branch breadth note remains for any future param).
        assert typo.status_code == 400, (
            f"unknown-param request now {typo.status_code} — flip "
            "this pin to 400."
        )
        # The wipe never happened: the closure still holds its concepts.
        state = _closure(fhir_client, [
            {"name": "name", "valueString": "l1b"},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": T2DM}},
        ])
        t_state = _token(state.json())
        assert not t_state.startswith(EMPTY_HASH_PREFIX), (
            "closure was wiped despite the 400 — state must survive "
            "a rejected request."
        )

    def test_l12_empty_body_reset_control(self, fhir_client):
        """Control: a name-only request IS the documented init/reset
        semantic (no add intent expressed) — wipe here is correct."""
        _closure(fhir_client, [
            {"name": "name", "valueString": "l1c"},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": T2DM}},
        ])
        r = _closure(fhir_client, [
            {"name": "name", "valueString": "l1c"},
        ])
        assert r.status_code == 200
        assert _token(r.json()).startswith(EMPTY_HASH_PREFIX)


class TestL2WrongMount:
    """L2 — spec endpoints for $closure 405."""

    @pytest.mark.parametrize("path", ["/fhir/$closure",
                                      "/fhir/ConceptMap/$closure"])
    def test_l20_spec_endpoints_405(self, fhir_client, path):
        """R4 mounts $closure at [base]/$closure. WHEN aliases land,
        flip to 200 + Parameters shape."""
        r = fhir_client.post(path, json={
            "resourceType": "Parameters",
            "parameter": [{"name": "name", "valueString": "x"}],
        })
        assert r.status_code == 405, (
            f"{path} now {r.status_code} — flip this pin."
        )

    def test_l21_current_mount_works(self, fhir_client):
        """Control: the CodeSystem-mounted path serves the op."""
        r = _closure(fhir_client, [
            {"name": "name", "valueString": "l2c"},
        ])
        assert r.status_code == 200


class TestL3VersionResyncWipes:
    """L3 — version resync request wipes the closure."""

    def test_l30_resync_wipes(self, fhir_client):
        """A resync against an OLD token (no concept entries) falls
        into the reset branch and DESTROYS the closure: fresh probe
        returned the EMPTY-TABLE hash after two concepts were stored.
        WHEN the fix lands (version honored as resync, or explicit
        unsupported), flip to preserved state or the 501."""
        r1 = _closure(fhir_client, [
            {"name": "name", "valueString": "l3"},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": T2DM}},
        ])
        old_token = _token(r1.json())
        grown = _closure(fhir_client, [
            {"name": "name", "valueString": "l3"},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": DM}},
        ])
        assert not _token(grown.json()).startswith(EMPTY_HASH_PREFIX)

        resync = _closure(fhir_client, [
            {"name": "name", "valueString": "l3"},
            {"name": "version", "valueString": old_token},
        ])
        assert resync.status_code == 200
        assert _token(resync.json()).startswith(EMPTY_HASH_PREFIX), (
            "version request no longer resets — resync semantics "
            "landed; flip this pin."
        )


class TestClosureInputControls:
    """Controls: documented input contract, fresh."""

    def test_c10_token_tracks_content(self, fhir_client):
        """Content-hash token changes as concepts land (QC-283)."""
        r1 = _closure(fhir_client, [
            {"name": "name", "valueString": "c10"},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": T2DM}},
        ])
        t1 = _token(r1.json())
        r2 = _closure(fhir_client, [
            {"name": "name", "valueString": "c10"},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": DM}},
        ])
        t2 = _token(r2.json())
        assert t1 != t2

    def test_c11_incomplete_false(self, fhir_client):
        """incomplete flag surfaced false on clean adds."""
        r = _closure(fhir_client, [
            {"name": "name", "valueString": "c11"},
            {"name": "concept",
             "valueCoding": {"system": SNOMED_URI, "code": T2DM}},
        ])
        flag = next(
            (q.get("valueBoolean") for q in r.json()["parameter"]
             if q.get("name") == "incomplete"),
            None,
        )
        assert flag is False

    def test_c12_unknown_system_400(self, fhir_client):
        """QC-271 contract: unrecognized system URI rejected 400 (not
        silently stored with zero relations)."""
        r = _closure(fhir_client, [
            {"name": "name", "valueString": "c12"},
            {"name": "concept",
             "valueCoding": {"system": "http://bogus.example/x",
                             "code": "1"}},
        ])
        assert r.status_code == 400
