#!/usr/bin/env python3
"""Download a UMLS Metathesaurus release and optionally build DuckDB data."""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from medterm4ds.services.data_setup import (  # noqa: E402
    DEFAULT_UMLS_RELEASE_TYPE,
    annotate_umls_duckdb,
    build_umls_duckdb,
    download_umls_release,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-key", default=None, help="UMLS/UTS API key. Defaults to UMLS_API_KEY.")
    parser.add_argument(
        "--release-type",
        default=DEFAULT_UMLS_RELEASE_TYPE,
        help="UTS releaseType. Defaults to umls-metathesaurus-full-subset.",
    )
    parser.add_argument("--release-version", default=None, help="Optional release version such as 2025AB.")
    parser.add_argument(
        "--current",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Filter UTS release metadata to current=true/false. Omitted by default.",
    )
    parser.add_argument("--output-dir", default="data/umls", help="Raw release download/extract directory.")
    parser.add_argument("--archive", default=None, help="Use an existing UMLS release archive instead of downloading.")
    parser.add_argument("--extract", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--build", action="store_true", help="Build DuckDB after downloading/extracting.")
    parser.add_argument("--rrf-dir", default=None, help="RRF directory to build from. Required if --build and auto-detection fails.")
    parser.add_argument(
        "--db-role",
        default=None,
        help="DB role to record in mt4ds.prepare_manifest. Required when --build is used.",
    )
    parser.add_argument("--output-db", default="data/umls_current.duckdb", help="DuckDB output path for --build.")
    parser.add_argument("--replace", action="store_true", help="Replace output database if it exists.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.build and not args.db_role:
        print("--db-role is required when --build is used.", file=sys.stderr)
        return 2
    if args.build and Path(args.output_db).name == "umls_local.duckdb":
        print(
            "Refusing ambiguous output DB name 'umls_local.duckdb'. "
            "Use a role/release-specific path such as data/umls_current.duckdb "
            "or data/umls_2025ab.duckdb.",
            file=sys.stderr,
        )
        return 2
    if args.build and Path(args.output_db).exists() and not args.replace:
        print(
            f"Output database exists: {args.output_db}. Pass --replace to rebuild it.",
            file=sys.stderr,
        )
        return 2

    output_dir = Path(args.output_dir)
    if args.archive:
        archive = Path(args.archive)
        inferred_release_version = _infer_release_version(archive)
        effective_release_version = args.release_version or inferred_release_version
        if (
            args.build
            and args.release_version
            and inferred_release_version
            and args.release_version.upper() != inferred_release_version
        ):
            print(
                f"--release-version {args.release_version} does not match "
                f"archive-inferred release {inferred_release_version}.",
                file=sys.stderr,
            )
            return 2
        if args.build and not effective_release_version:
            print(
                "--release-version is required for --build when it cannot be inferred "
                "from the archive name.",
                file=sys.stderr,
            )
            return 2
        if not archive.exists():
            print(f"Archive not found: {archive}", file=sys.stderr)
            return 2
        if args.extract and zipfile.is_zipfile(archive):
            _extract_archive(archive, output_dir / archive.stem)
    else:
        archive = download_umls_release(
            output_dir=output_dir,
            api_key=args.api_key,
            release_type=args.release_type,
            release_version=args.release_version,
            current=args.current,
            extract=args.extract,
        )
        inferred_release_version = _infer_release_version(archive)
        effective_release_version = args.release_version or inferred_release_version

    payload: dict[str, object] = {
        "archive": str(archive),
        "release_type": args.release_type,
        "release_version": effective_release_version,
        "archive_inferred_release_version": inferred_release_version,
        "current": args.current,
        "output_dir": str(output_dir),
        "extracted": bool(args.extract),
    }
    if args.build:
        release_version = effective_release_version
        if not release_version:
            print(
                "--release-version is required for --build when it cannot be inferred "
                "from the archive name.",
                file=sys.stderr,
            )
            return 2
        rrf_dir = Path(args.rrf_dir) if args.rrf_dir else _infer_rrf_dir(output_dir, archive)
        db_path = build_umls_duckdb(
            rrf_dir=rrf_dir,
            output_db=args.output_db,
            replace=args.replace,
            db_role=args.db_role,
            release_version=release_version,
            source_archive=archive,
        )
        annotations = annotate_umls_duckdb(
            db_path,
            db_role=args.db_role,
            release_version=release_version,
            source_archive=archive,
        )
        payload.update({
            "rrf_dir": str(rrf_dir),
            "db": str(db_path),
            "db_role": args.db_role,
            "release_version": release_version,
            "annotations": annotations,
        })

    print(json.dumps(payload, indent=2))
    return 0


def _extract_archive(archive: Path, extract_dir: Path) -> None:
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as release:
        release.extractall(extract_dir)


def _infer_rrf_dir(output_dir: Path, archive: Path) -> Path:
    extract_dir = output_dir / archive.stem
    candidates = [
        path
        for path in extract_dir.rglob("MRCONSO.RRF")
        if (path.parent / "MRREL.RRF").exists()
    ]
    if candidates:
        return candidates[0].parent

    nlm_candidates = [
        path.parent
        for path in extract_dir.rglob("*.nlm")
        if path.is_file()
    ]
    if nlm_candidates:
        return sorted(set(nlm_candidates))[0]

    raise RuntimeError(
        f"Could not infer RRF directory under {extract_dir}. "
        "Pass --rrf-dir explicitly."
    )


def _infer_release_version(archive: Path) -> str | None:
    match = re.search(r"(20[0-9]{2}[A-Z]{2})", archive.name.upper())
    return match.group(1) if match else None


if __name__ == "__main__":
    raise SystemExit(main())
