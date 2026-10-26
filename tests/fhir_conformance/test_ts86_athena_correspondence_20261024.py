"""TS-86 Athena correspondence vs engine systems (2026-10-24).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261024d).
FRESH EVIDENCE — live cross-check executed this run; no prior-run
results cited.

Scope: the bridge's vocabulary→FHIR-system table
(ATHENA_VOCAB_TO_FHIR) against the ENGINE's canonical system map
(fhir_uri_to_system). If the two disagree, the bridge artifacts
carry system URIs the FHIR server itself cannot resolve — codes
that join in the VD but fail $lookup/$validate-code on the same
server (a cross-surface integrity break at birth of the Athena
family).

AC1 (LOW, NEW) — two correspondence entries are UNRESOLVABLE by the
    engine: HCPCS (urn:oid:2.16.840.1.113883.6.14) and NDC
    (http://hl7.org/fhir/sid/ndc) both map fhir_uri_to_system →
    None. The engine's HCPCS/OID coverage gap (AL2's registered
    matrix: only SNOMED+LOINC OIDs resolve) meets the bridge at
    birth: Athena-sourced HCPCS/NDC codings in a VD join produce
    system URIs that $validate-code would 400 on. Not a converter
    bug (the URIs are the R4-correct identifiers; the ENGINE is the
    narrower side — AL2's fix closes this too). Registered as the
    Athena-side instance of AL2 with the concrete artifact impact.
    Pinned ac10.

Controls (fresh): the other 8 entries resolve correctly, including
    the tricky ones — CPT4→CPT (vocabulary spelling differs from
    the engine SAB exactly as the omoponfhir precedent predicts),
    RxNorm Extension folding onto RXNORM (same FHIR system, distinct
    OMOP vocabulary by design), SNOMED→SNOMEDCT_US, LOINC→LNC.
    Pinned ac11/ac12.
"""

from __future__ import annotations

import pytest

pytest.importorskip("medterm4ds.services.athena")

from medterm4ds.engines.fhir import fhir_uri_to_system  # noqa: E402
from medterm4ds.services.athena import (  # noqa: E402
    ATHENA_VOCAB_TO_FHIR,
)


class TestAC1UnresolvableEntries:
    """AC1 — HCPCS/NDC URIs unresolvable by the engine (AL2's
    Athena-side instance)."""

    @pytest.mark.parametrize(
        "vocab,uri",
        [
            ("HCPCS", "urn:oid:2.16.840.1.113883.6.14"),
            ("NDC", "http://hl7.org/fhir/sid/ndc"),
        ],
    )
    def test_ac10_engine_resolves(self, vocab, uri):
        """AC1 FIXED (fix/athena-batch1): the engine's alias map
        now resolves the bridge's HCPCS urn:oid and NDC forms —
        bridge-joinable AND $validate-code-resolvable on the same
        server (closes AL2's OID-gap rows with it)."""
        assert ATHENA_VOCAB_TO_FHIR[vocab] == uri
        sab = fhir_uri_to_system(uri)
        assert sab == vocab


class TestTS86Controls:
    """Controls — the resolvable majority."""

    @pytest.mark.parametrize(
        "vocab,expected_sab",
        [
            ("SNOMED", "SNOMEDCT_US"),
            ("RxNorm", "RXNORM"),
            ("RxNorm Extension", "RXNORM"),
            ("LOINC", "LNC"),
            ("ICD10CM", "ICD10CM"),
            ("ICD10PCS", "ICD10PCS"),
            ("CPT4", "CPT"),
            ("ATC", "ATC"),
        ],
    )
    def test_ac11_resolvable_entries(self, vocab, expected_sab):
        uri = ATHENA_VOCAB_TO_FHIR[vocab]
        assert fhir_uri_to_system(uri) == expected_sab

    def test_ac12_extension_folds_onto_rxnorm(self):
        """RxNorm Extension shares the RxNorm FHIR system BY DESIGN
        (distinct OMOP vocabulary, same wire system) — pinned so the
        fold is not 'fixed' away."""
        assert (
            ATHENA_VOCAB_TO_FHIR["RxNorm Extension"]
            == ATHENA_VOCAB_TO_FHIR["RxNorm"]
        )
