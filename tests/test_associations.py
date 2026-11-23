"""Tests for the associations graph service (fhir4ds/associations).

Hermetic: a small synthetic associations release (manifest + gz graph +
meta) is written to a tmp cache and pinned via MEDTERM4DS_ASSOCIATIONS_
DIR (mock-free convention — real layout on disk). No unit gate here
downloads from HF. The one live-artifact smoke is marker-gated off by
default (fhir4px_smoke/slow, mirroring the regression suite).

Fixture shape mirrors the live release 2026.11.15.1 (verified
2026-11-16): by_cid / by_name / concepts keyed by display NAME, member
cid prefixes, 13 buckets, provenance vocabulary.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from medterm4ds.services import associations as assoc

REL = "2099.01.01.9"
FORMAT = assoc.ASSOCIATIONS_FORMAT

# Synthetic graph: one condition node with mixed-system buckets (LOINC
# lab incl. a hyphenated code, SNOMED cond, RxNorm med, CPT proc), one
# medication node, one name-only product entry, one leaf cid.
GRAPH = {
    "format": FORMAT,
    "version": "2099-01-01.0000",
    "by_cid": {
        # anchors
        "VAL-COND-SNOMED-88594005": "herpes simplex infection",
        "VAL-MED-RXNORM-2034": "acyclovir",
        # members
        "VAL-LAB-LOINC-24321-2": "Basic Metabolic Panel (2000)",
        "VAL-LAB-LOINC-LP14355-9": "Creatinine",
        "VAL-COND-SNOMED-33839006": "herpes simplex keratitis",
        "VAL-MED-RXNORM-2035": "valacyclovir",
        "VAL-PROC-CPT-99213": "office visit",
        # a leaf: cid with display, no concept node
        "VAL-SYMP-SNOMED-999": "orphan symptom",
        # an ICD-10-CM cid with a hyphenated range-style code
        "VAL-COND-ICD10CM-A00-A09": "intestinal infections",
    },
    "by_name": {
        "herpes simplex infection": "VAL-COND-SNOMED-88594005",
        "acyclovir": "VAL-MED-RXNORM-2034",
    },
    "concepts": {
        "herpes simplex infection": {
            "name": "herpes simplex infection",
            "buckets": {
                "lab": [
                    {"cid": "VAL-LAB-LOINC-24321-2", "name": "Basic Metabolic Panel (2000)", "provenance": "monitoring_recommendation", "distance": 1},
                    {"cid": "VAL-LAB-LOINC-LP14355-9", "name": "Creatinine", "provenance": "monitoring_recommendation", "distance": 0},
                ],
                "condition": [
                    {"cid": "VAL-COND-SNOMED-33839006", "name": "herpes simplex keratitis", "provenance": "disease_context", "distance": 0},
                    {"cid": "VAL-COND-ICD10CM-A00-A09", "name": "intestinal infections", "provenance": "comorbidity_section", "distance": 1},
                ],
                "medication": [
                    {"cid": "VAL-MED-RXNORM-2035", "name": "valacyclovir", "provenance": "direct_indication", "distance": 0},
                ],
            },
        },
        "acyclovir": {
            "name": "acyclovir",
            "buckets": {
                "lab": [
                    {"cid": "VAL-LAB-LOINC-24321-2", "name": "Basic Metabolic Panel (2000)", "provenance": "panel_cooccurrence", "distance": 0},
                ],
                "procedure": [
                    {"cid": "VAL-PROC-CPT-99213", "name": "office visit", "provenance": "dosage_section", "distance": None},
                ],
            },
        },
        # name-only product entry (multi-ingredient naming aid): NOT in
        # by_name/by_cid — resolving to it is impossible via the chain.
        "Acetaminophen / Hydrocodone": {
            "name": "Acetaminophen / Hydrocodone",
            "buckets": {"medication": [
                {"cid": "VAL-MED-RXNORM-197361", "name": "amlo...", "provenance": "population_context", "distance": 0},
            ]},
        },
    },
}


def write_release(
    root: Path, rel: str = REL, *, format_: str = FORMAT,
    locked: bool = True, release_field: str | None = None,
) -> Path:
    base = root / "associations" / rel
    gdir = base / "graph" / rel
    gdir.mkdir(parents=True, exist_ok=True)
    payload = dict(GRAPH, format=format_)
    with gzip.open(gdir / "associations.json.gz", "wt") as fh:
        json.dump(payload, fh)
    (gdir / "associations.meta.json").write_text(json.dumps({
        "format": format_, "version": "2099-01-01.0000",
    }))
    (base / "manifest.json").write_text(json.dumps({
        "release": release_field or rel,
        "bundle_version": "2099-01-01.0000",
        "format": format_,
        "counts": {"concepts": 3, "cid_entries": 9, "members": 7},
        "version_lock": {
            "graph": "2099-01-01.0000", "naming": "2099-01-01.0000",
            "locked": locked,
        },
    }))
    return base


@pytest.fixture
def pinned_release(tmp_path, monkeypatch):
    base = write_release(tmp_path)
    monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_DIR", str(base))
    monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_RELEASE", REL)
    assoc.reset_associations_graph_for_testing()
    yield base
    assoc.reset_associations_graph_for_testing()


class TestAssocParseCid:
    def test_roundtrip_all_prefixes(self):
        g = assoc.AssociationsGraph(REL, {}, {}, {})
        for prefix, source in assoc.CID_PREFIX_TO_SOURCE.items():
            src, code = g.parse_cid(f"{prefix}-12345")
            assert src == source
            assert code == "12345"

    def test_hyphenated_loinc_code(self):
        g = assoc.AssociationsGraph(REL, {}, {}, {})
        assert g.parse_cid("VAL-LAB-LOINC-24321-2") == ("LNC", "24321-2")

    def test_hyphenated_icd10cm_range_code(self):
        g = assoc.AssociationsGraph(REL, {}, {}, {})
        assert g.parse_cid("VAL-COND-ICD10CM-A00-A09") == (
            "ICD10CM", "A00-A09",
        )

    def test_unknown_prefix_raises_loud(self):
        g = assoc.AssociationsGraph(REL, {}, {}, {})
        with pytest.raises(assoc.AssociationsLayoutError, match="VAL-NEW-X"):
            g.parse_cid("VAL-NEW-X-123")

    def test_too_short_cid_raises(self):
        g = assoc.AssociationsGraph(REL, {}, {}, {})
        with pytest.raises(assoc.AssociationsLayoutError):
            g.parse_cid("VAL-LAB")

    def test_registry_uris_pinned_to_canonical_map(self):
        # HCPCS QA-012 doctrine: every prefix's URI is DERIVED from
        # SYSTEM_TO_FHIR_URI and equals a canonical registry value.
        from medterm4ds.engines.fhir import SYSTEM_TO_FHIR_URI

        canonical_values = set(SYSTEM_TO_FHIR_URI.values())
        for prefix, uri in assoc.CID_PREFIX_TO_URI.items():
            assert uri in canonical_values
            assert uri == SYSTEM_TO_FHIR_URI[
                assoc.CID_PREFIX_TO_SOURCE[prefix]
            ]

    def test_target_enum_members(self):
        assert assoc.TARGET_SYSTEM_URIS == frozenset({
            "http://loinc.org",
            "http://snomed.info/sct",
            "http://www.nlm.nih.gov/research/umls/rxnorm",
            "http://www.ama-assn.org/go/cpt",
            "http://www.whocc.no/atc",
            "http://hl7.org/fhir/sid/cvx",
            "http://hl7.org/fhir/sid/icd-10-cm",
        })


class TestAssocGraphServing:
    def test_resolution_chain_cid_name_entry(self, pinned_release):
        g = assoc.get_associations_graph()
        entry = g.entry_for_cid("VAL-COND-SNOMED-88594005")
        assert entry is not None
        assert entry["name"] == "herpes simplex infection"

    def test_leaf_cid_no_entry(self, pinned_release):
        g = assoc.get_associations_graph()
        # by_cid has the display but concepts has no node → honest empty
        assert g.entry_for_cid("VAL-SYMP-SNOMED-999") is None

    def test_unknown_cid_no_entry(self, pinned_release):
        g = assoc.get_associations_graph()
        assert g.entry_for_cid("VAL-MED-RXNORM-DOES-NOT-EXIST") is None

    def test_name_only_product_unreachable_by_cid(self, pinned_release):
        g = assoc.get_associations_graph()
        # The product name has no by_cid entry at all
        assert all(
            v != "Acetaminophen / Hydrocodone" for v in g.by_cid.values()
        )

    def test_mixed_bucket_members_in_fixed_order(self, pinned_release):
        g = assoc.get_associations_graph()
        entry = g.entry_for_cid("VAL-COND-SNOMED-88594005")
        got = [b for b, _m in g.members(entry)]
        # published order: lab < medication < condition
        assert got == ["lab", "lab", "medication", "condition", "condition"]

    def test_per_member_target_filtering(self, pinned_release):
        # The 'condition' bucket mixes SNOMED and ICD-10-CM members —
        # filtering must be per member cid, never per bucket.
        g = assoc.get_associations_graph()
        entry = g.entry_for_cid("VAL-COND-SNOMED-88594005")
        from medterm4ds.engines.fhir import system_to_fhir_uri
        snomed = [
            m["cid"] for _b, m in g.members(entry)
            if system_to_fhir_uri(g.parse_cid(m["cid"])[0])
            == "http://snomed.info/sct"
        ]
        icd = [
            m["cid"] for _b, m in g.members(entry)
            if system_to_fhir_uri(g.parse_cid(m["cid"])[0])
            == "http://hl7.org/fhir/sid/icd-10-cm"
        ]
        assert snomed == ["VAL-COND-SNOMED-33839006"]
        assert icd == ["VAL-COND-ICD10CM-A00-A09"]

    def test_ordering_deterministic(self, pinned_release):
        g = assoc.get_associations_graph()
        entry = g.entry_for_cid("VAL-COND-SNOMED-88594005")
        members = list(g.members(entry))
        members.sort(key=lambda bm: g.sort_key(bm[0], bm[1]))
        once = [m["cid"] for _b, m in members]
        members.sort(key=lambda bm: g.sort_key(bm[0], bm[1]))
        assert once == [m["cid"] for _b, m in members]
        # distance None-last within a bucket
        entry_ac = g.entry_for_cid("VAL-MED-RXNORM-2034")
        keys = [
            g.sort_key(b, m)
            for b, m in g.members(entry_ac)
        ]
        assert keys == sorted(keys)

    def test_unknown_bucket_raises_loud(self):
        g = assoc.AssociationsGraph(REL, {}, {}, {})
        with pytest.raises(assoc.AssociationsLayoutError, match="bucket"):
            g.members({"name": "x", "buckets": {"new_bucket": []}})


class TestAssocManifestValidation:
    def test_release_mismatch_raises(self, tmp_path, monkeypatch):
        write_release(tmp_path, release_field="2098.01.01.1")
        monkeypatch.delenv(
            "MEDTERM4DS_ASSOCIATIONS_DIR", raising=False,
        )
        with pytest.raises(assoc.AssociationsLayoutError, match="release"):
            assoc.resolve(tmp_path, rel=REL)

    def test_format_drift_raises(self, tmp_path, monkeypatch):
        base = write_release(
            tmp_path, format_="fhir4px_associations_v9.9",
            release_field=REL,
        )
        monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_DIR", str(base))
        with pytest.raises(assoc.AssociationsLayoutError, match="format"):
            assoc.resolve(tmp_path, rel=REL)

    def test_unlocked_version_lock_raises(self, tmp_path, monkeypatch):
        base = write_release(tmp_path, locked=False)
        monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_DIR", str(base))
        with pytest.raises(
            assoc.AssociationsLayoutError, match="version_lock",
        ):
            assoc.resolve(tmp_path, rel=REL)

    def test_pinned_but_absent_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "MEDTERM4DS_ASSOCIATIONS_RELEASE", "2099.01.01.9",
        )
        monkeypatch.setenv(
            "MEDTERM4DS_ASSOCIATIONS_DIR",
            str(tmp_path / "associations" / "2099.01.01.9"),
        )
        with pytest.raises(
            assoc.AssociationsLayoutError, match="pinned but absent",
        ):
            assoc.get_associations_graph()

    def test_empty_and_whitespace_env_pins_are_unset(self, monkeypatch):
        monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_RELEASE", "")
        assert assoc.release() == assoc.DEFAULT_ASSOCIATIONS_RELEASE
        monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_RELEASE", "   ")
        assert assoc.release() == assoc.DEFAULT_ASSOCIATIONS_RELEASE
        monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_RELEASE", REL)
        assert assoc.release_explicitly_pinned() is True

    def test_graph_payload_format_drift_raises(self, tmp_path, monkeypatch):
        base = write_release(tmp_path)
        # manifest says v1.4 but the payload says otherwise
        gdir = base / "graph" / REL
        with gzip.open(gdir / "associations.json.gz", "wt") as fh:
            json.dump(dict(GRAPH, format="fhir4px_associations_v0.1"), fh)
        monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_DIR", str(base))
        monkeypatch.setenv("MEDTERM4DS_ASSOCIATIONS_RELEASE", REL)
        assoc.reset_associations_graph_for_testing()
        try:
            with pytest.raises(
                assoc.AssociationsLayoutError, match="format",
            ):
                assoc.get_associations_graph()
        finally:
            assoc.reset_associations_graph_for_testing()


class TestAssocSingleton:
    def test_single_flight_same_object(self, pinned_release):
        assert assoc.get_associations_graph() is assoc.get_associations_graph()

    def test_reset_reloads(self, pinned_release):
        first = assoc.get_associations_graph()
        assoc.reset_associations_graph_for_testing()
        assert assoc.get_associations_graph() is not first


@pytest.mark.slow
@pytest.mark.fhir4px_smoke
class TestAssocLiveArtifactSmoke:
    """ONE live-artifact smoke (network + HF_TOKEN; skipped by default)."""

    def test_live_graph_loads_and_parses(self, monkeypatch):
        pytest.importorskip("huggingface_hub")
        import os

        if not (os.getenv("HF_TOKEN")):
            pytest.skip("HF_TOKEN not set")
        monkeypatch.delenv(
            "MEDTERM4DS_ASSOCIATIONS_DIR", raising=False,
        )
        monkeypatch.delenv(
            "MEDTERM4DS_ASSOCIATIONS_RELEASE", raising=False,
        )
        assoc.reset_associations_graph_for_testing()
        try:
            g = assoc.get_associations_graph()
        finally:
            assoc.reset_associations_graph_for_testing()
        assert g.release == "2026.11.15.1"
        assert g.by_cid["VAL-LAB-LOINC-24325-3"] == (
            "Hepatic Function Panel (2000)"
        )
        src, code = g.parse_cid("VAL-LAB-LOINC-24325-3")
        assert (src, code) == ("LNC", "24325-3")
        entry = g.entry_for_cid("VAL-COND-SNOMED-88594005")
        assert entry is not None and entry.get("buckets", {}).get("lab")
