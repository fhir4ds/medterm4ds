"""TS-22 CLI transport-parity survey (2026-10-10).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261010).
FRESH EVIDENCE — every probe executed this run via the REAL CLI
(``medterm4ds`` entry point) against a synthetic DB; no prior-run
results cited.

Scope: the CLI is a first-class shipped surface (pyproject
[project.scripts]) but had never been maintenance-swept against the
contracts four fix-batches landed on FHIR (and iter-32 on MCP). This
suite pins where the surfaces agree (controls) and diverge.

CLI1 (MEDIUM, NEW) — search display-canonicalization is dead.
    With MEDTERM4DS_DB set, every ``medterm4ds search`` invocation
    warns "could not open <db> for display canonicalization ('Namespace'
    object has no attribute 'memory_profile')" and serves RAW index
    displays. The engine-open itself SUCCEEDS; the crash is in
    _local_duckdb_config_from_args(args), which reads
    args.memory_profile — a flag QC-382 deliberately REMOVED from the
    search parser (search reads BM25/SapBERT indexes, not DuckDB).
    The best-effort except then discards the engine, so QC-400's
    one-display convention (engine preferred term, the same display
    Python/FHIR/MCP emit) never runs on the CLI search path. The
    warning text is also misleading (blames the DB open, not the
    config construction).
    Fix shape: getattr(args, 'memory_profile', None) in
    _local_duckdb_config_from_args (or a search-specific config), and
    name the real failure if the best-effort path ever fires.
    Pinned: c10 (warning fires on a happy-path query), c11 (warning
    names the AttributeError — the tell), c12 (no warning without
    MEDTERM4DS_DB — the documented engine-less default).

CLI2 (LOW-MED, NEW) — whitespace extract runs the full pipeline.
    ``medterm4ds extract '   '`` fetched SapBERT + loaded the NLP
    stack and returned {"results": []} after 4m24s wall — the exact
    exhaustion surface W2 pinned on FHIR (79s cold there), with no
    CLI-side pre-check. The SIBLING ``search`` command rejects
    whitespace pre-service ('Error: query must not be empty', exit 1)
    — same command family, opposite discipline.
    Fix shape: strip() pre-check → 'Error: text must not be empty'
    exit 1 (mirrors search + W2's fix shape).
    Pinned: c20 (whitespace extract exits 0 with empty results after
    the full pipeline — the cost is documented in the docstring;
    flip when the pre-check lands). NOT re-run per suite invocation:
    one 4-minute probe was evidence; the pin asserts the OUTPUT
    contract cheaply via the (already cached) model path.

CLI3 (LOW, NEW) — unknown-source vs unknown-code miss shapes identical.
    ``lookup --source NOTASAB --code 44054006`` and ``lookup --source
    SNOMEDCT_US --code 99999999`` both emit a null-field record
    (aui/cui/name all null) and exit 0 — a typo'd vocabulary is
    indistinguishable from a missing concept. Z3's shape (iter-32,
    MCP), now pinned on CLI: three surfaces, three behaviors (FHIR
    400s the unknown source; MCP and CLI resolve null-record).
    Fix shape: distinguish unknown-vocabulary from unknown-code in
    the miss record (source absent from the DB's sources list →
    distinguishable field or non-zero exit).
    Pinned: c30 (both misses byte-shape-identical modulo echoed
    fields), c31 (exit 0 both).

Controls (fresh) — where the CLI is CONFORMANT or STRONGER than FHIR:
    map requires --target-source (argparse; V3's gap never existed
    here), unknown target → 'Error: target source(s) not found' +
    non-zero exit; whitespace/empty --code/--source rejected exit 1;
    URI-form --source rejected (QC-322 message); --limit >= 1;
    extract --result-types validated fail-loud naming valid values
    (E1's contract, already conformant on CLI).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SNOMED = "SNOMEDCT_US"
ICD10CM = "ICD10CM"
T2DM = "44054006"

_CLI = str(Path(sys.executable).parent / "medterm4ds")


@pytest.fixture(scope="module")
def cli_db(tmp_path_factory):
    """Synthetic DB for CLI probes (shared fixture builder)."""
    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts22_cli") / "umls.duckdb"
    _make_conformance_db(db)
    return db


def _run(args: list[str], env_extra: dict | None = None):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        [_CLI, *args], capture_output=True, text=True, timeout=600, env=env,
    )


class TestCLI1CanonicalizationDead:
    """CLI1 — search display-canonicalization crashed by design debt."""

    def test_c10_warning_on_happy_query(self, cli_db):
        """A happy-path search with MEDTERM4DS_DB set emits the
        canonicalization warning — QC-400 never runs. Flip when
        _local_duckdb_config_from_args tolerates the search parser's
        Namespace (getattr default)."""
        r = _run(
            ["search", "diabetes", "--mode", "lexical"],
            env_extra={"MEDTERM4DS_DB": str(cli_db)},
        )
        assert "display canonicalization" in r.stderr

    def test_c11_warning_names_attribute_error(self, cli_db):
        """The warning's cause is the AttributeError tell — the DB
        open succeeded; the CONFIG construction crashed on the flag
        QC-382 removed. (Diagnostic pin: names the real defect.)"""
        r = _run(
            ["search", "diabetes", "--mode", "lexical"],
            env_extra={"MEDTERM4DS_DB": str(cli_db)},
        )
        assert "memory_profile" in r.stderr

    def test_c12_no_env_no_warning(self):
        """Without MEDTERM4DS_DB the engine-less path is the DOCUMENTED
        default (QC-400 note) — no warning, raw displays by design."""
        env = {
            k: v
            for k, v in os.environ.items()
            if k != "MEDTERM4DS_DB"
        }
        r = subprocess.run(
            [_CLI, "search", "diabetes", "--mode", "lexical"],
            capture_output=True, text=True, timeout=600, env=env,
        )
        assert "display canonicalization" not in r.stderr


class TestCLI2WhitespaceExtract:
    """CLI2 — whitespace extract pays the full pipeline for nothing."""

    def test_c20_whitespace_extract_empty_ok(self):
        """'   ' extract → exit 0 + empty results (the full pipeline
        cost is the finding; asserted via the output contract — the
        4m24s evidence is recorded in the suite docstring, not
        re-paid per run). Flip when the strip() pre-check lands
        (exit 1 + 'text must not be empty')."""
        r = _run(["extract", "   "])
        assert r.returncode == 0
        body = json.loads(r.stdout)
        assert body == {"results": []}

    def test_c21_search_whitespace_rejected(self):
        """Sibling control: search DOES reject whitespace pre-service
        — the same-command-family asymmetry that makes CLI2 a
        finding."""
        r = _run(["search", "   ", "--mode", "lexical"])
        assert r.returncode == 1
        assert "query must not be empty" in r.stderr


class TestCLI3MissShapes:
    """CLI3 — unknown-source vs unknown-code indistinguishable."""

    def test_c30_identical_null_shapes(self, cli_db):
        r_src = _run(
            ["lookup", "--db", str(cli_db),
             "--source", "NOTASAB", "--code", T2DM]
        )
        r_code = _run(
            ["lookup", "--db", str(cli_db),
             "--source", SNOMED, "--code", "99999999"]
        )
        assert r_src.returncode == 0
        assert r_code.returncode == 0
        rec_src = r_src.stdout
        rec_code = r_code.stdout
        for rec in (rec_src, rec_code):
            body = json.loads(rec)
            entry = body["results"][0]
            for null_field in ("aui", "cui", "name"):
                assert entry[null_field] is None
        # The only difference is the echoed inputs — a client cannot
        # tell a typo'd vocabulary from a missing concept.

    def test_c31_exit_zero_both(self, cli_db):
        """Both miss classes exit 0 (success-shaped). Flip with the
        distinguishing fix."""
        for args in (
            ["lookup", "--db", str(cli_db),
             "--source", "NOTASAB", "--code", T2DM],
            ["lookup", "--db", str(cli_db),
             "--source", SNOMED, "--code", "99999999"],
        ):
            r = _run(args)
            assert r.returncode == 0


class TestCLIControls:
    """Controls — CLI conformance (several STRONGER than FHIR)."""

    def test_k10_map_target_required(self, cli_db):
        """map without --target-source: argparse failure (V3's gap
        never existed on CLI)."""
        r = _run(
            ["map", "--db", str(cli_db), "--source", SNOMED,
             "--code", T2DM]
        )
        assert r.returncode == 2  # argparse usage error

    def test_k11_map_unknown_target_error(self, cli_db):
        """Unknown target system → Error + non-zero exit (stronger
        than FHIR's V2-era silent widening)."""
        r = _run(
            ["map", "--db", str(cli_db), "--source", SNOMED,
             "--code", T2DM, "--target-source", "NOTASAB"]
        )
        assert r.returncode != 0
        assert "not found" in r.stdout or "not found" in r.stderr

    def test_k12_whitespace_code_rejected(self, cli_db):
        r = _run(
            ["lookup", "--db", str(cli_db),
             "--source", SNOMED, "--code", "   "]
        )
        assert r.returncode == 1
        assert "non-empty" in r.stderr

    def test_k13_uri_source_rejected(self, cli_db):
        """QC-322 message on the CLI transport."""
        r = _run(
            ["lookup", "--db", str(cli_db),
             "--source", "http://snomed.info/sct", "--code", T2DM]
        )
        assert r.returncode != 0
        assert "SAB" in r.stderr or "URI" in r.stderr

    def test_k14_limit_positive(self):
        r = _run(["search", "diabetes", "--limit", "0"])
        assert r.returncode == 2  # argparse validation

    def test_k15_extract_result_types_fail_loud(self):
        """E1's contract already conformant on CLI: bogus result type
        → Error naming the valid values (no 500, no silent ignore)."""
        r = _run(
            ["extract", "diabetes", "--result-types", "bogus"]
        )
        assert r.returncode != 0
        assert "Unknown result type" in r.stdout + r.stderr
        assert "condition" in r.stdout + r.stderr  # names valid values
