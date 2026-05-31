#!/usr/bin/env python3
"""Run small end-to-end CLI acceptance checks against a DuckDB UMLS database."""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
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
        return 0 if all(check.status == "pass" for check in checks) else 1
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


if __name__ == "__main__":
    raise SystemExit(main())
