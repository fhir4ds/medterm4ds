#!/usr/bin/env python3
"""Build and optionally publish medterm4ds distributions."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-verify", action="store_true", help="Skip make verify before building.")
    parser.add_argument("--clean", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--publish", choices=("none", "testpypi", "pypi"), default="none")
    parser.add_argument("--repository-url", default=None, help="Explicit twine repository URL.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.skip_verify:
        _run(["make", "verify"])
    if args.clean:
        shutil.rmtree(ROOT / "dist", ignore_errors=True)
        shutil.rmtree(ROOT / "build", ignore_errors=True)
    _run([sys.executable, "-m", "build"])
    dist_files = sorted(str(path) for path in (ROOT / "dist").glob("*"))
    if not dist_files:
        raise RuntimeError("No distribution files were built.")
    _run([sys.executable, "-m", "twine", "check", *dist_files])
    if args.publish != "none":
        command = [sys.executable, "-m", "twine", "upload"]
        if args.repository_url:
            command.extend(["--repository-url", args.repository_url])
        elif args.publish == "testpypi":
            command.extend(["--repository", "testpypi"])
        command.extend(dist_files)
        _run(command)
    return 0


def _run(command: list[str]) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


if __name__ == "__main__":
    raise SystemExit(main())
