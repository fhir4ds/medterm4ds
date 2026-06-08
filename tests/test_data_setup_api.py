from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import duckdb
import pytest

import medterm4ds as mt
from medterm4ds.services import data_setup


def test_top_level_umls_build_and_verify_helpers(tmp_path):
    rrf_dir = tmp_path / "rrf"
    rrf_dir.mkdir()
    (rrf_dir / "MRCONSO.RRF").write_text(
        "\n".join(
            [
                "C1|ENG|P|L1|PF|S1|Y|A1||||ICD10CM|PT|E11.9|Type 2 diabetes mellitus|0|N|",
                "CS0|ENG|P|LS0|PF|SS0|Y|AS0||||SNOMEDCT_US|PT|100|SNOMED root|0|N|",
                "CS1|ENG|P|LS1|PF|SS1|Y|AS1||||SNOMEDCT_US|PT|200|SNOMED top child|0|N|",
                "CS2|ENG|P|LS2|PF|SS2|Y|AS2||||SNOMEDCT_US|PT|300|SNOMED deep child|0|N|",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (rrf_dir / "MRREL.RRF").write_text(
        "\n".join(
            [
                "C1|A1|AUI|PAR|C2|A2|AUI|isa|R1||ICD10CM|ICD10CM|||N|",
                "CS1|AS1|AUI|PAR|CS0|AS0|AUI|isa|RS1||SNOMEDCT_US|SNOMEDCT_US|||N|",
                "CS2|AS2|AUI|PAR|CS1|AS1|AUI|isa|RS2||SNOMEDCT_US|SNOMEDCT_US|||N|",
                "",
            ]
        ),
        encoding="utf-8",
    )

    db_path = mt.build_umls_duckdb(
        rrf_dir=rrf_dir,
        output_db=tmp_path / "umls.duckdb",
    )
    report = mt.verify_umls_duckdb(db_path)

    assert db_path.exists()
    assert report["has_required_tables"] is True
    assert report["has_snomed_top_level_depth"] is True
    assert report["source_counts"] == {"ICD10CM": 1, "SNOMEDCT_US": 3}
    assert "ICD10CM" in mt.DEFAULT_UMLS_VERIFY_SOURCES

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        assert dict(con.execute("SELECT code, min_top_depth FROM snomed_top_level_depth").fetchall()) == {
            "100": 1,
            "200": 2,
            "300": 3,
        }
    finally:
        con.close()

    prepare_report = mt.prepare_umls_duckdb(db_path, replace=False)
    assert prepare_report["snomed_top_level_depth"]["status"] == "exists"


def test_build_umls_duckdb_rejects_ambiguous_umls_local_output(tmp_path):
    with pytest.raises(RuntimeError, match="ambiguous output DB name"):
        mt.build_umls_duckdb(
            rrf_dir=tmp_path,
            output_db=tmp_path / "umls_local.duckdb",
        )


def test_download_umls_release_public_wrapper(monkeypatch, tmp_path):
    captured = {}

    def fake_download_release(**kwargs):
        captured.update(kwargs)
        return Path(kwargs["output_dir"]) / "umls.zip"

    monkeypatch.setattr(data_setup, "download_release", fake_download_release)

    path = mt.download_umls_release(
        output_dir=tmp_path,
        api_key="test-key",
        extract=True,
    )

    assert path == tmp_path / "umls.zip"
    assert captured == {
        "output_dir": tmp_path,
        "api_key": "test-key",
        "release_type": "umls-metathesaurus-full-subset",
        "release_version": None,
        "current": None,
        "extract": True,
    }


def test_download_umls_release_can_pin_release_version(monkeypatch, tmp_path):
    captured = {}

    def fake_download_release(**kwargs):
        captured.update(kwargs)
        return Path(kwargs["output_dir"]) / "umls-2025AB-metathesaurus-full.zip"

    monkeypatch.setattr(data_setup, "download_release", fake_download_release)

    path = mt.download_umls_release(
        output_dir=tmp_path,
        api_key="test-key",
        release_version="2025AB",
    )

    assert path == tmp_path / "umls-2025AB-metathesaurus-full.zip"
    assert captured["release_type"] == "umls-metathesaurus-full-subset"
    assert captured["release_version"] == "2025AB"
    assert captured["current"] is None


def test_annotate_umls_duckdb_records_manifest_rows(tmp_path):
    db_path = tmp_path / "umls.duckdb"
    con = duckdb.connect(str(db_path))
    con.close()

    annotations = mt.annotate_umls_duckdb(
        db_path,
        db_role="current_candidate",
        release_version="2025AB",
        source_archive=tmp_path / "umls-2025AB.zip",
    )

    assert annotations == {
        "db_role": "current_candidate",
        "umls_release": "2025AB",
        "source_archive": str(tmp_path / "umls-2025AB.zip"),
    }
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = dict(
            con.execute(
                "SELECT key, value FROM mt4ds.prepare_manifest WHERE key IN ('db_role', 'umls_release')"
            ).fetchall()
        )
    finally:
        con.close()
    assert rows == {"db_role": "current_candidate", "umls_release": "2025AB"}


def test_current_release_omits_current_filter_and_selects_version(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps(
                [
                    {
                        "fileName": "umls-2026AA-metathesaurus-full.zip",
                        "releaseVersion": "2026AA",
                        "downloadUrl": "https://example.test/2026AA.zip",
                    },
                    {
                        "fileName": "umls-2025AB-metathesaurus-full.zip",
                        "releaseVersion": "2025AB",
                        "downloadUrl": "https://example.test/2025AB.zip",
                    },
                ]
            ).encode("utf-8")

    def fake_urlopen(url):
        captured["url"] = url
        return FakeResponse()

    monkeypatch.setattr(data_setup, "urlopen", fake_urlopen)

    release = data_setup.current_release(
        release_type="umls-metathesaurus-full-subset",
        release_version="2025AB",
    )

    query = parse_qs(urlparse(captured["url"]).query)
    assert query == {"releaseType": ["umls-metathesaurus-full-subset"]}
    assert release["fileName"] == "umls-2025AB-metathesaurus-full.zip"
