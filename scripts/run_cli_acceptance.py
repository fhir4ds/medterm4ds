#!/usr/bin/env python3
"""Run small end-to-end CLI acceptance checks against a DuckDB UMLS database."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from medterm4ds.apps.cli import main as cli_main
from medterm4ds.services.inventory import count_source_codes, normalize_sources


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    elapsed_seconds: float
    details: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/mnt/d/medterm/data/umls_local.duckdb")
    parser.add_argument("--sources", default="ICD10CM,CVX")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--partial-limit", type=int, default=5)
    parser.add_argument("--fhir-limit", type=int, default=5)
    parser.add_argument("--memory-profile", default="low")
    parser.add_argument("--work-dir", default=None)
    parser.add_argument("--output-json", default=None)
    parser.add_argument(
        "--prepare-cache",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Exercise LocalLite cache prep during CLI acceptance.",
    )
    parser.add_argument("--progress", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2
    if args.limit < 1:
        print("limit must be at least 1", file=sys.stderr)
        return 2

    try:
        import duckdb
    except ImportError as exc:
        print("DuckDB is required. Install medterm4ds[duckdb].", file=sys.stderr)
        raise SystemExit(2) from exc

    sources = normalize_sources(args.sources)
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        expected_total = min(sum(count_source_codes(con, sources).values()), args.limit)
    finally:
        con.close()

    if expected_total < 1:
        print("No source codes found for acceptance run.", file=sys.stderr)
        return 2

    if args.work_dir:
        work_dir = Path(args.work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        cleanup = False
    else:
        work_dir = Path(tempfile.mkdtemp(prefix="medterm4ds-acceptance-"))
        cleanup = True

    try:
        checks = run_acceptance(
            db_path=db_path,
            work_dir=work_dir,
            sources=sources,
            limit=expected_total,
            partial_limit=min(args.partial_limit, expected_total),
            fhir_limit=min(args.fhir_limit, expected_total),
            memory_profile=args.memory_profile,
            prepare_cache=args.prepare_cache,
            progress=args.progress,
        )
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "db": str(db_path),
            "work_dir": str(work_dir),
            "sources": list(sources),
            "limit": expected_total,
            "checks": [asdict(check) for check in checks],
        }
        if args.output_json:
            Path(args.output_json).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({check.name: check.status for check in checks}, sort_keys=True))
        return 0 if all(check.status in {"pass", "skip"} for check in checks) else 1
    finally:
        if cleanup:
            shutil.rmtree(work_dir, ignore_errors=True)


def run_acceptance(
    *,
    db_path: Path,
    work_dir: Path,
    sources: tuple[str, ...],
    limit: int,
    partial_limit: int,
    fhir_limit: int,
    memory_profile: str,
    prepare_cache: bool,
    progress: bool,
) -> list[CheckResult]:
    checks = [
        _check_jsonl_resume(
            db_path=db_path,
            work_dir=work_dir,
            sources=sources,
            limit=limit,
            partial_limit=partial_limit,
            memory_profile=memory_profile,
            prepare_cache=prepare_cache,
            progress=progress,
        ),
        _check_csv(
            db_path=db_path,
            work_dir=work_dir,
            sources=sources,
            limit=limit,
            memory_profile=memory_profile,
            prepare_cache=prepare_cache,
            progress=progress,
        ),
        _check_fhir(
            db_path=db_path,
            work_dir=work_dir,
            sources=sources,
            limit=fhir_limit,
            memory_profile=memory_profile,
            prepare_cache=prepare_cache,
            progress=progress,
        ),
        _check_lookup_cli(
            db_path=db_path,
            work_dir=work_dir,
            sources=sources,
            memory_profile=memory_profile,
        ),
        _check_map_cli(
            db_path=db_path,
            work_dir=work_dir,
            sources=sources,
            memory_profile=memory_profile,
        ),
        _check_hierarchy_cli(
            db_path=db_path,
            work_dir=work_dir,
            sources=sources,
            memory_profile=memory_profile,
        ),
    ]
    return checks


def _check_jsonl_resume(
    *,
    db_path: Path,
    work_dir: Path,
    sources: tuple[str, ...],
    limit: int,
    partial_limit: int,
    memory_profile: str,
    prepare_cache: bool,
    progress: bool,
) -> CheckResult:
    start = time.perf_counter()
    output = work_dir / "acceptance.jsonl"
    _run_cli(
        db_path=db_path,
        sources=sources,
        output=output,
        output_format="jsonl",
        limit=partial_limit,
        memory_profile=memory_profile,
        prepare_cache=prepare_cache,
        progress=progress,
    )
    _run_cli(
        db_path=db_path,
        sources=sources,
        output=output,
        output_format="jsonl",
        limit=limit,
        memory_profile=memory_profile,
        prepare_cache=prepare_cache,
        progress=progress,
        resume=True,
    )
    rows = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    duplicate_count = len(rows) - len({(row["source"], row["code"]) for row in rows})
    status = "pass" if len(rows) == limit and duplicate_count == 0 else "fail"
    return CheckResult(
        name="jsonl_resume",
        status=status,
        elapsed_seconds=time.perf_counter() - start,
        details={
            "rows": len(rows),
            "expected_rows": limit,
            "duplicates": duplicate_count,
            "checkpoint_exists": output.with_name(f"{output.name}.checkpoint.json").exists(),
        },
    )


def _check_csv(
    *,
    db_path: Path,
    work_dir: Path,
    sources: tuple[str, ...],
    limit: int,
    memory_profile: str,
    prepare_cache: bool,
    progress: bool,
) -> CheckResult:
    start = time.perf_counter()
    output = work_dir / "acceptance.csv"
    _run_cli(
        db_path=db_path,
        sources=sources,
        output=output,
        output_format="csv",
        limit=limit,
        memory_profile=memory_profile,
        prepare_cache=prepare_cache,
        progress=progress,
    )
    with output.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    duplicate_count = len(rows) - len({(row["source"], row["code"]) for row in rows})
    status = "pass" if len(rows) == limit and duplicate_count == 0 else "fail"
    return CheckResult(
        name="csv",
        status=status,
        elapsed_seconds=time.perf_counter() - start,
        details={"rows": len(rows), "expected_rows": limit, "duplicates": duplicate_count},
    )


def _check_fhir(
    *,
    db_path: Path,
    work_dir: Path,
    sources: tuple[str, ...],
    limit: int,
    memory_profile: str,
    prepare_cache: bool,
    progress: bool,
) -> CheckResult:
    start = time.perf_counter()
    output = work_dir / "acceptance.fhir.json"
    _run_cli(
        db_path=db_path,
        sources=sources,
        output=output,
        output_format="fhir-json",
        limit=limit,
        memory_profile=memory_profile,
        prepare_cache=prepare_cache,
        progress=progress,
    )
    resource = json.loads(output.read_text(encoding="utf-8"))
    targets = [
        target
        for group in resource.get("group", [])
        for element in group.get("element", [])
        for target in element.get("target", [])
    ]
    status = (
        "pass"
        if resource.get("resourceType") == "ConceptMap"
        and len(targets) == limit
        and all("equivalence" in target for target in targets)
        and all("relationship" not in target for target in targets)
        else "fail"
    )
    return CheckResult(
        name="fhir_json_r4",
        status=status,
        elapsed_seconds=time.perf_counter() - start,
        details={"targets": len(targets), "expected_targets": limit},
    )


def _check_lookup_cli(
    *,
    db_path: Path,
    work_dir: Path,
    sources: tuple[str, ...],
    memory_profile: str,
) -> CheckResult:
    start = time.perf_counter()
    sample = _first_active_code(db_path, sources)
    if sample is None:
        return CheckResult(
            name="lookup_cli",
            status="skip",
            elapsed_seconds=time.perf_counter() - start,
            details={"reason": "no active source code"},
        )
    source, code = sample
    output = work_dir / "lookup.json"
    status = cli_main(
        [
            "lookup",
            "--db",
            str(db_path),
            "--source",
            source,
            "--code",
            code,
            "--output",
            str(output),
            "--memory-profile",
            memory_profile,
        ]
    )
    payload = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {"results": []}
    rows = payload.get("results", [])
    passed = status == 0 and rows and rows[0].get("source") == source and rows[0].get("code") == code
    return CheckResult(
        name="lookup_cli",
        status="pass" if passed else "fail",
        elapsed_seconds=time.perf_counter() - start,
        details={"source": source, "code": code, "rows": len(rows), "status": status},
    )


def _check_map_cli(
    *,
    db_path: Path,
    work_dir: Path,
    sources: tuple[str, ...],
    memory_profile: str,
) -> CheckResult:
    start = time.perf_counter()
    sample = _first_same_cui_pair(db_path, sources)
    if sample is None:
        return CheckResult(
            name="map_cli",
            status="skip",
            elapsed_seconds=time.perf_counter() - start,
            details={"reason": "no same-CUI source/target pair"},
        )
    source, code, target_source = sample
    output = work_dir / "map.json"
    status = cli_main(
        [
            "map",
            "--db",
            str(db_path),
            "--source",
            source,
            "--code",
            code,
            "--target-source",
            target_source,
            "--output",
            str(output),
            "--memory-profile",
            memory_profile,
        ]
    )
    payload = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {"results": []}
    rows = payload.get("results", [])
    passed = status == 0 and any(
        row.get("source") == source
        and row.get("code") == code
        and row.get("target_source") == target_source
        for row in rows
    )
    return CheckResult(
        name="map_cli",
        status="pass" if passed else "fail",
        elapsed_seconds=time.perf_counter() - start,
        details={
            "source": source,
            "code": code,
            "target_source": target_source,
            "rows": len(rows),
            "status": status,
        },
    )


def _check_hierarchy_cli(
    *,
    db_path: Path,
    work_dir: Path,
    sources: tuple[str, ...],
    memory_profile: str,
) -> CheckResult:
    start = time.perf_counter()
    sample = _first_parent_pair(db_path, sources)
    if sample is None:
        return CheckResult(
            name="hierarchy_cli",
            status="skip",
            elapsed_seconds=time.perf_counter() - start,
            details={"reason": "no same-source parent edge"},
        )
    source, code = sample
    output = work_dir / "hierarchy.json"
    status = cli_main(
        [
            "hierarchy",
            "parents",
            "--db",
            str(db_path),
            "--source",
            source,
            "--code",
            code,
            "--output",
            str(output),
            "--memory-profile",
            memory_profile,
        ]
    )
    payload = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {"results": []}
    rows = payload.get("results", [])
    passed = status == 0 and any(
        row.get("source") == source
        and row.get("code") == code
        and row.get("relationship") == "parent"
        for row in rows
    )
    return CheckResult(
        name="hierarchy_cli",
        status="pass" if passed else "fail",
        elapsed_seconds=time.perf_counter() - start,
        details={"source": source, "code": code, "rows": len(rows), "status": status},
    )


def _run_cli(
    *,
    db_path: Path,
    sources: tuple[str, ...],
    output: Path,
    output_format: str,
    limit: int,
    memory_profile: str,
    prepare_cache: bool,
    progress: bool,
    resume: bool = False,
) -> None:
    argv = [
        "conceptmap",
        "patient-friendly",
        "--db",
        str(db_path),
        "--sources",
        ",".join(sources),
        "--output",
        str(output),
        "--format",
        output_format,
        "--limit",
        str(limit),
        "--memory-profile",
        memory_profile,
        "--batch-size",
        "100",
        "--checkpoint-every",
        "1",
    ]
    if not prepare_cache:
        argv.append("--no-prepare-cache")
    if progress:
        argv.append("--progress")
    if resume:
        argv.append("--resume")
    status = cli_main(argv)
    if status != 0:
        raise RuntimeError(f"CLI acceptance command failed with status {status}: {' '.join(argv)}")


def _first_active_code(db_path: Path, sources: tuple[str, ...]) -> tuple[str, str] | None:
    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit("DuckDB is required. Install medterm4ds[duckdb].") from exc
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            f"""
            SELECT SAB, CODE
            FROM mrconso
            WHERE SUPPRESS = 'N'
              AND CODE IS NOT NULL
              AND CODE != ''
              AND SAB IN ({','.join(['?'] * len(sources))})
            GROUP BY SAB, CODE
            ORDER BY SAB, CODE
            LIMIT 1
            """,
            list(sources),
        ).fetchone()
    finally:
        con.close()
    return (str(rows[0]), str(rows[1])) if rows else None


def _first_same_cui_pair(db_path: Path, sources: tuple[str, ...]) -> tuple[str, str, str] | None:
    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit("DuckDB is required. Install medterm4ds[duckdb].") from exc
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute(
            f"""
            WITH source_atoms AS (
                SELECT SAB, CODE, CUI
                FROM mrconso
                WHERE SUPPRESS = 'N'
                  AND CODE IS NOT NULL
                  AND CODE != ''
                  AND CUI IS NOT NULL
                  AND SAB IN ({','.join(['?'] * len(sources))})
                GROUP BY SAB, CODE, CUI
                ORDER BY SAB, CODE
                LIMIT 1000
            )
            SELECT s.SAB, s.CODE, t.SAB
            FROM source_atoms s
            JOIN mrconso t ON t.CUI = s.CUI AND t.SAB != s.SAB
            WHERE t.SUPPRESS = 'N'
              AND t.CODE IS NOT NULL
              AND t.CODE != ''
            GROUP BY s.SAB, s.CODE, t.SAB
            ORDER BY s.SAB, s.CODE, t.SAB
            LIMIT 1
            """,
            list(sources),
        ).fetchone()
    finally:
        con.close()
    return (str(row[0]), str(row[1]), str(row[2])) if row else None


def _first_parent_pair(db_path: Path, sources: tuple[str, ...]) -> tuple[str, str] | None:
    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit("DuckDB is required. Install medterm4ds[duckdb].") from exc
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute(
            f"""
            WITH source_atoms AS (
                SELECT SAB, CODE, AUI
                FROM mrconso
                WHERE SUPPRESS = 'N'
                  AND CODE IS NOT NULL
                  AND CODE != ''
                  AND AUI IS NOT NULL
                  AND SAB IN ({','.join(['?'] * len(sources))})
                ORDER BY SAB, CODE
                LIMIT 2000
            )
            SELECT c.SAB, c.CODE
            FROM source_atoms c
            JOIN mrrel r ON r.AUI1 = c.AUI AND r.REL = 'PAR'
            JOIN mrconso p ON p.AUI = r.AUI2 AND p.SAB = c.SAB
            WHERE p.SUPPRESS = 'N'
            GROUP BY c.SAB, c.CODE
            ORDER BY c.SAB, c.CODE
            LIMIT 1
            """,
            list(sources),
        ).fetchone()
    finally:
        con.close()
    return (str(row[0]), str(row[1])) if row else None


if __name__ == "__main__":
    raise SystemExit(main())
