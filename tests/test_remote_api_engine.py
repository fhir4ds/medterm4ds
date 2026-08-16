from __future__ import annotations

from medterm4ds import (
    CodeRef,
    get_code_infos,
    get_code_mappings,
    get_patient_friendly_names,
    optimize_codes,
    resolve_codes,
)
from medterm4ds.engines.api import RemoteApiEngine
from medterm4ds.services.discovery import (
    get_code_ttys,
    get_source_stats,
    sample_source_codes,
    search_names,
)
from medterm4ds.services.hierarchy import get_code_relations


def test_remote_api_engine_implements_service_protocols():
    calls: list[tuple[str, dict]] = []
    responses = {
        "/health": {"status": "ok"},
        "/lookup": {
            "results": [
                {
                    "source": "ICD10CM",
                    "code": "E11.9",
                    "name": "Type 2 diabetes mellitus",
                    "cui": "C_DIAB",
                    "aui": "ICD_E119",
                    "tty": "PT",
                    "suppress": "N",
                },
                None,
            ]
        },
        "/map": {
            "results": [
                {
                    "source": "ICD10CM",
                    "code": "E11.9",
                    "source_display": "Type 2 diabetes mellitus",
                    "target_source": "SNOMEDCT_US",
                    "target_code": "44054006",
                    "target_display": "Diabetes mellitus type 2",
                    "relationship": "equivalent",
                    "match_type": "same_cui",
                    "match_depth": 0,
                    "source_cui": "C_DIAB",
                    "target_cui": "C_DIAB",
                    "source_aui": "ICD_E119",
                    "target_aui": "SNOMED_DIAB",
                    "target_tty": "PT",
                    "matched_via": {
                        "strategy": "same_cui",
                        "steps": [{"op": "input_atom", "source": "ICD10CM", "code": "E11.9"}],
                    },
                }
            ]
        },
        "/hierarchy": {
            "results": [
                {
                    "source": "ICD10CM",
                    "code": "E11.9",
                    "source_display": "Type 2 diabetes mellitus",
                    "target_source": "ICD10CM",
                    "target_code": "E11",
                    "target_display": "Type 2 diabetes mellitus",
                    "relationship": "parent",
                    "depth": 1,
                    "rel": "PAR",
                    "rela": "isa",
                    "source_cui": "C_E119",
                    "target_cui": "C_E11",
                    "source_aui": "ICD_E119",
                    "target_aui": "ICD_E11",
                }
            ]
        },
        "/patient-friendly": {
            "results": [
                {
                    "source": "ICD10CM",
                    "code": "E11.9",
                    "name": "Diabetes",
                    "friendly_source": "MEDLINEPLUS",
                    "match_type": "exact",
                    "match_depth": 0,
                    "technical_name": "Type 2 diabetes mellitus",
                    "matched_via": {"strategy": "test", "steps": [{"op": "input"}]},
                }
            ]
        },
        "/sources": {"results": [{"source": "ICD10CM", "code_count": 1, "atom_count": 2}]},
        "/sample-codes": {"results": [{"source": "ICD10CM", "code": "E11.9"}]},
        "/code-ttys": {
            "results": [
                {
                    "source": "ICD10CM",
                    "code": "E11.9",
                    "name": "Type 2 diabetes mellitus",
                    "cui": "C_DIAB",
                    "aui": "ICD_E119",
                    "tty": "PT",
                    "suppress": "N",
                }
            ]
        },
        "/search-names": {
            "results": [
                {
                    "source": "ICD10CM",
                    "code": "E11.9",
                    "name": "Type 2 diabetes mellitus",
                    "cui": "C_DIAB",
                    "aui": "ICD_E119",
                    "tty": "PT",
                    "match_type": "contains",
                }
            ]
        },
        "/resolve": {
            "results": [
                {
                    "source": "NDC",
                    "code": "0002-0821-01",
                    "resolved_source": "RXNORM",
                    "resolved_code": "12345",
                    "status": "ndc_resolved",
                    "match_type": "ndc_to_rxcui",
                    "input_display": "00002082101",
                    "resolved_display": "Insulin",
                    "input_cui": None,
                    "resolved_cui": "C_RX",
                    "input_aui": None,
                    "resolved_aui": "RX_AUI",
                    "input_suppress": None,
                    "resolved_suppress": "N",
                    "replacement_relationship": None,
                    "normalized_code": "00002082101",
                    "candidates": [{"source": "RXNORM", "code": "12345"}],
                    "matched_via": {"strategy": "ndc_to_rxcui", "steps": [{"op": "input"}]},
                }
            ]
        },
        # QC-490: /optimize now uses the shared 'results' envelope like every
        # other endpoint (the engine still accepts the legacy 'result' key).
        "/optimize": {
            "results": [
                {
                    "source": "ICD10CM",
                    "relationship": "isa",
                    "strategy": "greedy_hierarchy",
                    "original_count": 2,
                    "optimized_count": 1,
                    "reduction": 50.0,
                    "rules": [{"include_source": "ICD10CM", "include": "E11", "exclude": []}],
                }
            ]
        },
    }

    def transport(path, payload):
        calls.append((path, dict(payload)))
        return responses[path]

    engine = RemoteApiEngine("http://terminology.example", transport=transport)

    infos = get_code_infos([CodeRef("ICD10CM", "E11.9"), CodeRef("CVX", "NOPE")], engine=engine)
    mappings = get_code_mappings(
        [CodeRef("ICD10CM", "E11.9")],
        engine=engine,
        target_sources=["SNOMED"],
        max_depth=1,
    )
    relations = get_code_relations(
        [CodeRef("ICD10CM", "E11.9")],
        engine=engine,
        direction="parents",
    )
    friendly = get_patient_friendly_names([CodeRef("ICD10CM", "E11.9")], engine=engine)
    stats = get_source_stats(engine=engine, sources=["ICD10-CM"])
    samples = sample_source_codes(engine=engine, sources=["ICD10CM"], per_source=1)
    ttys = get_code_ttys([CodeRef("ICD10CM", "E11.9")], engine=engine)
    search = search_names("diabetes", engine=engine, sources=["ICD10CM"], tty_filters=["PT"])
    resolved = resolve_codes([CodeRef("NDC", "0002-0821-01")], engine=engine)
    optimized = optimize_codes(
        [CodeRef("ICD10CM", "E11.40"), CodeRef("ICD10CM", "E11.41")],
        engine=engine,
    )
    health = engine.health()

    assert infos[0].name == "Type 2 diabetes mellitus"
    assert infos[1] is None
    assert mappings[0].target == CodeRef("SNOMEDCT_US", "44054006")
    assert mappings[0].matched_via.to_dict()["strategy"] == "same_cui"
    assert relations[0].target == CodeRef("ICD10CM", "E11")
    assert friendly[0].name == "Diabetes"
    assert stats[0].code_count == 1
    assert samples == [CodeRef("ICD10CM", "E11.9")]
    assert ttys[0].tty == "PT"
    assert search[0].match_type == "contains"
    assert resolved[0].resolved == CodeRef("RXNORM", "12345")
    assert optimized.rules[0].include == CodeRef("ICD10CM", "E11")
    assert health["status"] == "ok"
    assert calls[1][1]["target_sources"] == ["SNOMEDCT_US"]
    assert calls[4][1]["sources"] == ["ICD10CM"]


# QC-481 (LOW): garbage constructor inputs must fail at construction with a
# named-parameter ValueError — not at first call as raw AttributeError /
# TypeError / "unknown url type" ValueError outside the RuntimeError envelope.
import pytest

from medterm4ds.engines.api.engine import (
    DEFAULT_REMOTE_TIMEOUT,
    RemoteApiEngine,
    _truncate_detail,
)


@pytest.mark.parametrize(
    "base_url",
    [None, "", "   ", "127.0.0.1:8931", "ftp://example.com"],
)
def test_remote_engine_rejects_invalid_base_url(base_url):
    with pytest.raises(ValueError, match="base_url"):
        RemoteApiEngine(base_url)


@pytest.mark.parametrize("timeout", ["abc", -1.0, 0, None, True])
def test_remote_engine_rejects_invalid_timeout(timeout):
    with pytest.raises(ValueError, match="timeout"):
        RemoteApiEngine("http://terminology.example", timeout=timeout)


# QC-485 (HIGH): the 30s default broke facade calls the local engine
# completes (optimize measured 55-82s; a 10k-code patient-friendly batch
# ~415s).
def test_remote_engine_default_timeout_accommodates_documented_workloads():
    assert DEFAULT_REMOTE_TIMEOUT >= 300


# QC-490 (LOW): the engine still accepts the legacy singular 'result' key
# from pre-fix servers while preferring the shared 'results' envelope.
def test_remote_engine_optimize_accepts_legacy_result_key():
    legacy = {
        "result": {
            "source": "ICD10CM",
            "relationship": "isa",
            "strategy": "greedy_hierarchy",
            "original_count": 2,
            "optimized_count": 1,
            "reduction": 50.0,
            "rules": [{"include_source": "ICD10CM", "include": "E11", "exclude": []}],
        }
    }
    engine = RemoteApiEngine("http://terminology.example", transport=lambda path, payload: legacy)
    result = engine.optimize_codes([CodeRef("ICD10CM", "E11.9")])
    assert result.rules[0].include.code == "E11"


# QC-479 (MEDIUM): the embedded HTTP error body is truncated so a pydantic
# 422 echoing a 10,001-code input list cannot materialize as a 430k-char
# exception string.
def test_remote_engine_truncates_embedded_error_detail():
    huge = "x" * 430_291
    truncated = _truncate_detail(huge)
    assert len(truncated) < 2_200
    assert "chars truncated" in truncated
    assert _truncate_detail("short message") == "short message"
