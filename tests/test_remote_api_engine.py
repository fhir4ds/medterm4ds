from __future__ import annotations

from medterm4ds import (
    CodeRef,
    get_code_infos,
    get_code_mappings,
    get_patient_friendly_names,
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
    assert health["status"] == "ok"
    assert calls[1][1]["target_sources"] == ["SNOMEDCT_US"]
    assert calls[4][1]["sources"] == ["ICD10CM"]
