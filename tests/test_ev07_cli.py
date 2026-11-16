"""EV-07 (cli-breadth) iteration 19-21 pins — TestEv07*.

SKEPTIC (iteration 19): hostile CLI invocations — empty/URI inputs, hostile
numeric flags, corrupt/missing/dir --db, unknown flags, bad output paths,
conflicting cache-refresh flags. All must exit with a clean one-line error
(SystemExit / argparse), never a Python traceback, never success-shaped
output for an invalid invocation.

EXPLORER (iteration 21): --min-grade defers to the service (EV05-EXPLORER-1
carry-in on the CLI surface) + env-var argparse defaults.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from medterm4ds.apps import cli
from medterm4ds.apps.cli import build_parser, main


@pytest.fixture(scope="module")
def empty_db(tmp_path_factory) -> Path:
    """A VALID DuckDB file with no UMLS schema (right magic, wrong database)."""
    path = tmp_path_factory.mktemp("ev07") / "empty.duckdb"
    con = duckdb.connect(str(path))
    con.close()
    return path


@pytest.fixture(scope="module")
def corrupt_db(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("ev07") / "corrupt.duckdb"
    path.write_bytes(b"this is not a duckdb database file" * 64)
    return path


@pytest.fixture(scope="module")
def schema_db(tmp_path_factory) -> Path:
    """Minimal mrconso-schema DB so query commands reach their write path."""
    path = tmp_path_factory.mktemp("ev07") / "schema.duckdb"
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE mrconso (CODE VARCHAR, TTY VARCHAR, STR VARCHAR, "
        "AUI VARCHAR, SUPPRESS VARCHAR, SAB VARCHAR, CUI VARCHAR)"
    )
    con.execute("CREATE TABLE mrrel (AUI1 VARCHAR, AUI2 VARCHAR, RELA VARCHAR, REL VARCHAR)")
    con.execute(
        "INSERT INTO mrconso VALUES ('E11.9','PT','Type 2 diabetes mellitus',"
        "'A1','N','ICD10CM','C1')"
    )
    con.close()
    return path


class TestEv07SkepticEmptyInputs:
    """S1/S2: empty/whitespace/URI-form --source/--code on every pair command."""

    CMDS = [
        ["lookup"],
        ["map", "--target-source", "ICD10CM"],
        ["resolve"],
        ["hierarchy", "parents"],
        ["code-ttys"],
    ]

    @pytest.mark.parametrize("cmd", CMDS)
    @pytest.mark.parametrize("source", ["", "   "])
    def test_empty_source_rejected(self, cmd, source, empty_db):
        with pytest.raises(SystemExit) as ei:
            main([*cmd, "--db", str(empty_db), "--source", source, "--code", "E11"])
        assert ei.value.code != 0
        assert "non-empty vocabulary" in str(ei.value)

    @pytest.mark.parametrize("cmd", CMDS)
    @pytest.mark.parametrize("code", ["", "   "])
    def test_empty_code_rejected(self, cmd, code, empty_db):
        with pytest.raises(SystemExit) as ei:
            main([*cmd, "--db", str(empty_db), "--source", "ICD10CM", "--code", code])
        assert ei.value.code != 0
        assert "non-empty terminology code" in str(ei.value)

    @pytest.mark.parametrize("source", ["http://snomed.info/sct", "urn:oid:2.16.840.1.113883.6.90"])
    def test_uri_source_rejected(self, source, empty_db):
        with pytest.raises(SystemExit) as ei:
            main(["lookup", "--db", str(empty_db), "--source", source, "--code", "73211009"])
        assert "SAB" in str(ei.value)

    def test_uri_target_source_rejected(self, empty_db):
        with pytest.raises(SystemExit) as ei:
            main([
                "map", "--db", str(empty_db), "--source", "ICD10CM", "--code", "E11",
                "--target-source", "http://hl7.org/fhir/sid/icd-10-cm",
            ])
        assert "SAB" in str(ei.value)

    def test_empty_target_source_rejected(self, empty_db):
        with pytest.raises(SystemExit) as ei:
            main([
                "map", "--db", str(empty_db), "--source", "ICD10CM", "--code", "E11",
                "--target-source", "",
            ])
        assert "non-empty vocabulary" in str(ei.value)

    def test_empty_bulk_sources_rejected_before_artifacts(self, empty_db, tmp_path):
        # QC-387/395: explicit-empty --sources never silently exports 0 rows;
        # the guard fires before any output file exists.
        out = tmp_path / "o.jsonl"
        with pytest.raises(SystemExit) as ei:
            main([
                "bulk", "lookup", "--db", str(empty_db), "--sources", "",
                "--output", str(out),
            ])
        assert "at least one non-empty vocabulary" in str(ei.value)
        assert not out.exists()

    def test_empty_conceptmap_target_sources_rejected(self, empty_db, tmp_path):
        with pytest.raises(SystemExit):
            main([
                "conceptmap", "mapping", "--db", str(empty_db),
                "--sources", "ICD10CM", "--target-sources", "",
                "--output", str(tmp_path / "o.jsonl"),
            ])


class TestEv07SkepticHostileNumbers:
    """S4/S5: hostile numeric flags exit clean (parse-time or guarded)."""

    @pytest.mark.parametrize("value", ["0", "-1"])
    @pytest.mark.parametrize("limit_flag", ["--limit", "--per-source"])
    def test_zero_negative_limits_rejected(self, limit_flag, value, empty_db):
        cmd = ["sample-codes", "--db", str(empty_db), limit_flag, value] if limit_flag == "--per-source" else \
              ["search", "diabetes", limit_flag, value]
        with pytest.raises(SystemExit) as ei:
            main(cmd)
        assert ei.value.code != 0
        assert "Traceback" not in str(ei.value)

    @pytest.mark.parametrize("value", ["0", "-1", "-5"])
    def test_negative_max_depth_bulk_rejected_before_artifacts(self, value, empty_db, tmp_path):
        # QC-377: validation fires before the DB connection and any output.
        out = tmp_path / "o.jsonl"
        with pytest.raises(SystemExit) as ei:
            main([
                "bulk", "hierarchy", "--db", str(empty_db), "--sources", "ICD10CM",
                "--direction", "ancestors", "--max-depth", value, "--output", str(out),
            ])
        assert "at least 1" in str(ei.value)
        assert not out.exists()

    @pytest.mark.parametrize("value", ["0", "-1"])
    def test_athena_negative_limit_clean(self, value, empty_db, capsys):
        rc = main(["athena-concepts", "--db", str(empty_db), "--query", "x", "--limit", value])
        assert rc == 2
        assert "limit must be at least 1" in capsys.readouterr().err

    def test_athena_negative_max_depth_clean(self, empty_db, capsys):
        rc = main(["athena-hierarchy", "--db", str(empty_db), "--concept-id", "1", "--max-depth", "-1"])
        assert rc == 2
        assert "max_depth must be at least 1" in capsys.readouterr().err


class TestEv07SkepticHierarchyDepthFloor:
    """EV07-SKEPTIC-1 (MEDIUM, RESOLVED): bulk/single hierarchy require
    max_depth >= 1, validated BEFORE the DB connection and output artifacts
    (QC-377 family). Pre-fix, ``bulk hierarchy --max-depth 0`` raised the
    service ValueError inside write_checkpointed_rows after a 0-byte output
    file existed."""

    def test_bulk_hierarchy_depth_zero_rejected_before_artifacts(self, schema_db, tmp_path):
        out = tmp_path / "o.jsonl"
        with pytest.raises(SystemExit) as ei:
            main([
                "bulk", "hierarchy", "--db", str(schema_db), "--sources", "ICD10CM",
                "--direction", "ancestors", "--max-depth", "0", "--output", str(out),
            ])
        assert "at least 1" in str(ei.value)
        assert not out.exists()

    @pytest.mark.parametrize("value", ["0", "-1", "-5"])
    def test_single_hierarchy_depth_rejected_pre_db(self, value, schema_db):
        # Pre-DB: the guard fires even when the DB path is nonexistent.
        with pytest.raises(SystemExit) as ei:
            main([
                "hierarchy", "ancestors", "--db", str(schema_db) + ".missing.duckdb",
                "--source", "ICD10CM", "--code", "E11.9", "--max-depth", value,
            ])
        assert "at least 1" in str(ei.value)

    def test_bulk_patient_friendly_depth_zero_still_valid(self, schema_db, tmp_path):
        # The patient-friendly surface DOCUMENTS 0 ("exact/same-CUI only") —
        # the floor is per-surface, not global.
        parser = build_parser()
        args = parser.parse_args([
            "bulk", "patient-friendly", "--db", str(schema_db), "--sources", "ICD10CM",
            "--output", str(tmp_path / "pf.jsonl"), "--max-depth", "0",
        ])
        from medterm4ds.apps import cli as cli_mod

        # Validation passes (no SystemExit); the export itself is not run.
        cli_mod._validate_cli_max_depth(args)
        cli_mod._validate_cli_max_depth(args, warn_on_zero=True)


class TestEv07SkepticDbPaths:
    """S6: missing / dir / corrupt / wrong-schema / conn-string --db."""

    def test_missing_db_clean(self, tmp_path):
        with pytest.raises(SystemExit) as ei:
            main(["lookup", "--db", str(tmp_path / "nope.duckdb"), "--source", "ICD10CM", "--code", "E11"])
        assert "Database not found" in str(ei.value)

    def test_dir_db_clean(self, tmp_path):
        with pytest.raises(SystemExit) as ei:
            main(["lookup", "--db", str(tmp_path), "--source", "ICD10CM", "--code", "E11"])
        assert "is a directory" in str(ei.value)

    def test_empty_string_db_clean(self, tmp_path):
        with pytest.raises(SystemExit) as ei:
            main(["lookup", "--db", "", "--source", "ICD10CM", "--code", "E11"])
        assert "is a directory" in str(ei.value)  # '' resolves to CWD → dir

    def test_corrupt_db_clean(self, corrupt_db):
        with pytest.raises(SystemExit) as ei:
            main(["lookup", "--db", str(corrupt_db), "--source", "ICD10CM", "--code", "E11"])
        assert "not a valid DuckDB database file" in str(ei.value)

    def test_wrong_schema_db_clean(self, empty_db):
        # EV07-QA-02 chokepoint in main(): CatalogException → actionable error.
        with pytest.raises(SystemExit) as ei:
            main(["lookup", "--db", str(empty_db), "--source", "ICD10CM", "--code", "E11"])
        assert "not a medterm4ds UMLS database" in str(ei.value)

    def test_conn_string_suffix_db_rejected(self, empty_db):
        with pytest.raises(SystemExit) as ei:
            main(["lookup", "--db", str(empty_db) + "?mode=ro", "--source", "ICD10CM", "--code", "E11"])
        assert ei.value.code != 0
        assert "Traceback" not in str(ei.value)


class TestEv07SkepticOutputPaths:
    """S8: hostile --output paths surface clean errors."""

    def test_output_missing_dir_clean(self, schema_db, tmp_path):
        with pytest.raises(SystemExit) as ei:
            main([
                "lookup", "--db", str(schema_db), "--source", "ICD10CM", "--code", "E11.9",
                "--output", str(tmp_path / "nodir" / "out.json"),
            ])
        assert "cannot write to" in str(ei.value)

    def test_output_weird_extension_clean(self, schema_db, tmp_path):
        with pytest.raises(SystemExit) as ei:
            main([
                "lookup", "--db", str(schema_db), "--source", "ICD10CM", "--code", "E11.9",
                "--output", str(tmp_path / "out.weird"),
            ])
        assert "Could not infer output format" in str(ei.value)

    def test_bulk_output_weird_extension_clean(self, empty_db, tmp_path):
        with pytest.raises(SystemExit) as ei:
            main([
                "bulk", "lookup", "--db", str(empty_db), "--sources", "ICD10CM",
                "--output", str(tmp_path / "out.weird"),
            ])
        assert "Could not infer output format" in str(ei.value)


class TestEv07SkepticUnknownArgs:
    """S7: unknown subcommands/flags/directions exit via argparse."""

    def test_unknown_subcommand(self):
        with pytest.raises(SystemExit) as ei:
            main(["bogus-subcommand"])
        assert ei.value.code != 0

    def test_unknown_data_subcommand(self):
        with pytest.raises(SystemExit) as ei:
            main(["data", "bogus"])
        assert ei.value.code != 0

    def test_unknown_hierarchy_direction(self):
        with pytest.raises(SystemExit) as ei:
            main(["hierarchy", "sideways", "--db", "x.duckdb"])
        assert ei.value.code != 0

    def test_unknown_flag(self, empty_db):
        with pytest.raises(SystemExit) as ei:
            main(["lookup", "--db", str(empty_db), "--source", "ICD10CM", "--code", "E11", "--bogus"])
        assert ei.value.code != 0


class TestEv07SkepticConflictingFlags:
    """S9: --split + --terminology exit clean per the 0.0.12 contract."""

    def test_split_plus_terminology_clean(self, monkeypatch):
        monkeypatch.delenv("MEDTERM4DS_CACHE_DIR", raising=False)
        # No stub: the real cache_refresh raises RuntimeError BEFORE any
        # network access; @_data_cli_errors converts it to a clean SystemExit.
        with pytest.raises(SystemExit) as ei:
            main(["data", "cache-refresh", "--split", "--terminology"])
        assert ei.value.code != 0
        assert "mutually exclusive" in str(ei.value)

    def test_version_flag(self, capsys):
        import medterm4ds

        with pytest.raises(SystemExit) as ei:
            main(["--version"])
        assert ei.value.code == 0
        assert medterm4ds.__version__ in capsys.readouterr().out


class TestEv07HistorianIngestEnvelope:
    """EV07-HISTORIAN-1 (MEDIUM, RESOLVED): run_data_ingest_athena was
    added after the QC-445 data-family error-envelope sweep and never got
    @_data_cli_errors — a missing CONCEPT.csv dumped a raw
    FileNotFoundError traceback (observed failing-first pre-fix)."""

    def test_ingest_missing_csv_clean_error(self, tmp_path):
        empty_dir = tmp_path / "empty-athena"
        empty_dir.mkdir()
        import pytest as _pytest

        with _pytest.raises(SystemExit) as ei:
            main([
                "data", "ingest-athena",
                "--db", str(tmp_path / "ing.duckdb"),
                "--athena-dir", str(empty_dir),
                "--accept-licenses",
            ])
        assert "CONCEPT.csv not found" in str(ei.value)
        assert "Traceback" not in str(ei.value)

    def test_ingest_missing_db_dir_clean_error(self, tmp_path):
        import pytest as _pytest

        with _pytest.raises(SystemExit) as ei:
            main([
                "data", "ingest-athena",
                "--db", str(tmp_path / "nodir" / "x.duckdb"),
                "--athena-dir", str(tmp_path),
                "--accept-licenses",
            ])
        assert "Error:" in str(ei.value)
        assert "Traceback" not in str(ei.value)


class TestEv07HistorianCleanValueErrorSweep:
    """QC-049/059/197/219 wrapper-family sweep across ALL run_* handlers.

    run_lookup/run_resolve carry NO except-ValueError wrapper — verified
    unreachable-ValueError: every ValueError the services raise
    (validate_source_sab / validate_code_nonempty) is pre-guarded at the
    CLI boundary (_code_source_pairs empty/URI guards; resolve-mode is
    argparse-choices). Pin that boundary invariant: hostile --source/--code
    shapes never reach the service layer on these two commands."""

    @pytest.mark.parametrize("cmd", [["lookup"], ["resolve"]])
    @pytest.mark.parametrize("source", ["", "  ", "http://snomed.info/sct"])
    def test_boundary_guards_cover_unwrapped_handlers(self, cmd, source, empty_db):
        with pytest.raises(SystemExit) as ei:
            main([*cmd, "--db", str(empty_db), "--source", source, "--code", "E11"])
        assert ei.value.code != 0
        assert "Traceback" not in str(ei.value)

    def test_optimize_empty_source_wrapped_clean(self, empty_db):
        # run_optimize bypasses _code_source_pairs (single --source) but has
        # the QC-197 wrapper — service ValueError surfaces as a clean error.
        with pytest.raises(SystemExit) as ei:
            main(["optimize", "--db", str(empty_db), "--source", "", "--code", "E11"])
        assert ei.value.code != 0
        assert "Traceback" not in str(ei.value)


class TestEv07ExplorerMinGradeDefersToService:
    """EV07-EXPLORER-1 (MEDIUM, RESOLVED — EV05-EXPLORER-1 carry-in):
    ``extract --min-grade`` defaulted to DEFAULT_MIN_GRADE ('certain'),
    overriding the service's canonical-mode default floor (probable) — the
    same default request returned exact-only on the CLI but exact+probable
    on Python/FHIR. The CLI now defaults to None and defers to the service
    (the EV05 fhir_api.py fix pattern). Failing-first: default was
    'certain' pre-fix."""

    def test_default_is_none(self):
        args = build_parser().parse_args(["extract", "some text"])
        assert args.min_grade is None

    def test_explicit_value_still_accepted_and_validated(self):
        args = build_parser().parse_args(["extract", "t", "--min-grade", "certain"])
        assert args.min_grade == "certain"
        import pytest as _pytest

        with _pytest.raises(SystemExit):
            build_parser().parse_args(["extract", "t", "--min-grade", "garbage"])

    def test_run_extract_forwards_none_to_service(self, monkeypatch):
        import medterm4ds.services.extraction as extraction_mod

        captured = {}

        def fake_extract(text, *, format=None, **kwargs):  # noqa: A002
            captured.update(kwargs)
            captured["format"] = format
            return []

        monkeypatch.setattr(extraction_mod, "extract", fake_extract, raising=False)
        from medterm4ds.apps import cli as cli_mod

        args = build_parser().parse_args(["extract", "some text"])
        rc = cli_mod.run_extract(args)
        assert rc == 0
        assert captured["min_grade"] is None
        # mode still defaults to the service constant (canonical).
        assert captured["mode"] == extraction_mod.DEFAULT_SEARCH_MODE

    def test_legacy_mode_default_defers_to_service_constant(self, monkeypatch):
        # In legacy modes None → self._min_grade = DEFAULT_MIN_GRADE
        # (env-or-'certain') INSIDE the service — the CLI does not
        # second-guess the mode-dependent resolution.
        import medterm4ds.services.extraction as extraction_mod

        captured = {}

        def fake_extract(text, **kwargs):
            captured.update(kwargs)
            return []

        monkeypatch.setattr(extraction_mod, "extract", fake_extract, raising=False)
        from medterm4ds.apps import cli as cli_mod

        args = build_parser().parse_args(["extract", "t", "--mode", "hybrid"])
        cli_mod.run_extract(args)
        assert captured["min_grade"] is None
        assert captured["mode"] == "hybrid"


class TestEv07ExplorerExtractArgForwarding:
    """Comma-list flags split into lists and forward verbatim (QC-169 shape)."""

    def test_comma_lists_split_and_forward(self, monkeypatch):
        import medterm4ds.services.extraction as extraction_mod

        captured = {}

        def fake_extract(text, **kwargs):
            captured.update(kwargs)
            return []

        monkeypatch.setattr(extraction_mod, "extract", fake_extract, raising=False)
        from medterm4ds.apps import cli as cli_mod

        args = build_parser().parse_args([
            "extract", "t",
            "--ner-labels", "disorder,medication",
            "--result-types", "condition,medication",
            "--annotation-fields", "text,name,status",
            "--include-negated",
        ])
        cli_mod.run_extract(args)
        assert captured["ner_labels"] == ["disorder", "medication"]
        assert captured["result_types"] == ["condition", "medication"]
        assert captured["annotation_fields"] == ["text", "name", "status"]
        assert captured["include_negated"] is True


class TestEv07ExplorerSearchNoDb:
    """QC-382 (INTENDED, pinned): ``search`` deliberately carries no --db."""

    def test_search_parser_has_no_db_flag(self):
        import pytest as _pytest

        with _pytest.raises(SystemExit):
            build_parser().parse_args(["search", "diabetes", "--db", "x.duckdb"])

    def test_run_text_search_opens_no_connection_without_env(self, monkeypatch, capsys):
        monkeypatch.delenv("MEDTERM4DS_DB", raising=False)
        from medterm4ds.services import search as search_mod

        def fake_search(query, *, mode=None, sources=None, count=None,
                        result_types=None, engine=None):
            captured.update(query=query, engine=engine)
            return []

        captured = {}
        monkeypatch.setattr(search_mod, "search", fake_search, raising=False)
        from medterm4ds.apps import cli as cli_mod

        rc = cli_mod.run_text_search(build_parser().parse_args(["search", "diabetes"]))
        assert rc == 0
        assert captured["engine"] is None
        assert '"results"' in capsys.readouterr().out


class TestEv07ExplorerOutputDeterminism:
    """Repeated invocations produce byte-identical output (json/jsonl/csv)."""

    def test_lookup_stdout_json_deterministic(self, schema_db, capsys):
        argv = ["lookup", "--db", str(schema_db), "--source", "ICD10CM", "--code", "E11.9"]
        main(argv)
        first = capsys.readouterr().out
        main(argv)
        assert first == capsys.readouterr().out

    def test_lookup_file_jsonl_deterministic(self, schema_db, tmp_path):
        argv = [
            "lookup", "--db", str(schema_db), "--source", "ICD10CM", "--code", "E11.9",
            "--output", str(tmp_path / "a.jsonl"),
        ]
        main(argv)
        first = (tmp_path / "a.jsonl").read_bytes()
        argv[argv.index("--output") + 1] = str(tmp_path / "b.jsonl")
        main(argv)
        assert first == (tmp_path / "b.jsonl").read_bytes()


class TestEv07ExplorerResumeInterplay:
    """resume/checkpoint flag interplay."""

    def test_bulk_map_resume_rejected(self, schema_db, tmp_path):
        with pytest.raises(SystemExit) as ei:
            main([
                "bulk", "map", "--db", str(schema_db), "--sources", "ICD10CM",
                "--target-sources", "SNOMEDCT_US", "--output", str(tmp_path / "m.jsonl"),
                "--resume",
            ])
        assert "not supported" in str(ei.value)

    def test_resume_fhir_json_rejected(self, schema_db, tmp_path):
        with pytest.raises(SystemExit) as ei:
            main([
                "conceptmap", "patient-friendly", "--db", str(schema_db),
                "--sources", "ICD10CM", "--output", str(tmp_path / "o.json"),
                "--resume",
            ])
        assert "not resumable" in str(ei.value)

    def test_resume_cold_run_writes_rows_no_duplicate_on_warm(self, schema_db, tmp_path):
        out = tmp_path / "r.jsonl"
        argv = [
            "bulk", "lookup", "--db", str(schema_db), "--sources", "ICD10CM",
            "--output", str(out), "--resume", "--no-prepare-cache",
        ]
        assert main(argv) == 0
        first = out.read_text().splitlines()
        assert main(argv) == 0
        warm = out.read_text().splitlines()
        assert len(first) == len(warm)  # resume skips completed codes


class TestEv07ExplorerEnvDefaults:
    """QC-464: MEDTERM4DS_* engine knobs are argparse FALLBACK defaults."""

    def test_threads_env_default(self, monkeypatch):
        monkeypatch.setenv("MEDTERM4DS_THREADS", "7")
        args = build_parser().parse_args(["lookup", "--db", "x", "--source", "S", "--code", "C"])
        assert args.threads == 7

    def test_memory_limit_env_default(self, monkeypatch):
        monkeypatch.setenv("MEDTERM4DS_MEMORY_LIMIT", "3GB")
        args = build_parser().parse_args(["lookup", "--db", "x", "--source", "S", "--code", "C"])
        assert args.memory_limit == "3GB"

    def test_flag_overrides_env(self, monkeypatch):
        monkeypatch.setenv("MEDTERM4DS_THREADS", "7")
        args = build_parser().parse_args([
            "lookup", "--db", "x", "--source", "S", "--code", "C", "--threads", "2",
        ])
        assert args.threads == 2

    def test_garbage_env_exits_clean_not_traceback(self, monkeypatch):
        monkeypatch.setenv("MEDTERM4DS_MEMORY_LIMIT", "nonsense")
        with pytest.raises(SystemExit) as ei:  # CR-043 envelope in main()
            main(["lookup", "--db", "x", "--source", "S", "--code", "C"])
        assert "size string" in str(ei.value)


class TestEv07SkepticIntended:
    """SKEPTIC classifications (INTENDED, pinned so a future flip is loud)."""

    def test_extract_empty_text_empty_result_is_intended(self, monkeypatch):
        # QC-162: whitespace/empty text degrades gracefully to 0 spans on the
        # NER path (no wrong-answer risk like BM25 embedding would have).
        captured = {}

        class _FakeResult(list):
            pass

        import medterm4ds.services.extraction as extraction_mod

        def fake_extract(text, **kwargs):
            captured["text"] = text
            return []

        monkeypatch.setattr(extraction_mod, "extract", fake_extract, raising=False)
        rc = cli.run_extract(
            build_parser().parse_args(["extract", ""])
        )
        assert rc == 0
        assert captured["text"] == ""
