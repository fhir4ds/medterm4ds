#!/usr/bin/env python3
"""Install the built wheel into a fresh venv and run a tiny notebook-style smoke."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import textwrap
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", default=None, help="Wheel path. Defaults to dist/medterm4ds-*.whl.")
    parser.add_argument("--extras", default="duckdb,dataframe", help="Comma-separated extras to install.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    wheel = Path(args.wheel) if args.wheel else _latest_wheel()
    if not wheel.exists():
        print(f"Wheel not found: {wheel}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="medterm4ds-wheel-") as tmp:
        venv_dir = Path(tmp) / "venv"
        venv.EnvBuilder(with_pip=True).create(venv_dir)
        python = _venv_python(venv_dir)
        install_target = str(wheel.resolve())
        if args.extras:
            install_target = f"{install_target}[{args.extras}]"
        _run([python, "-m", "pip", "install", "--upgrade", "pip"])
        _run([python, "-m", "pip", "install", install_target])
        smoke_path = Path(tmp) / "wheel_smoke.py"
        smoke_path.write_text(_smoke_script(), encoding="utf-8")
        _run([python, str(smoke_path)])
    return 0


def _latest_wheel() -> Path:
    wheels = sorted((ROOT / "dist").glob("medterm4ds-*.whl"))
    if not wheels:
        return ROOT / "dist" / "medterm4ds-*.whl"
    return wheels[-1]


def _venv_python(venv_dir: Path) -> str:
    if sys.platform == "win32":
        return str(venv_dir / "Scripts" / "python.exe")
    return str(venv_dir / "bin" / "python")


def _run(command: list[str]) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)


def _smoke_script() -> str:
    return textwrap.dedent(
        """
        from pathlib import Path
        import tempfile

        import duckdb
        import medterm4ds as mt

        assert callable(mt.build_umls_duckdb)
        assert callable(mt.verify_umls_duckdb)
        assert callable(mt.download_umls_release)

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "umls.duckdb"
            con = duckdb.connect(str(db))
            try:
                con.execute(
                    "CREATE TABLE mrconso (CODE VARCHAR, TTY VARCHAR, STR VARCHAR, AUI VARCHAR, SUPPRESS VARCHAR, SAB VARCHAR, CUI VARCHAR)"
                )
                con.execute("CREATE TABLE mrrel (AUI1 VARCHAR, AUI2 VARCHAR, RELA VARCHAR, REL VARCHAR)")
                con.execute(
                    "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
                    ("E11.9", "PT", "Type 2 diabetes mellitus", "A_E119", "N", "ICD10CM", "C_DIAB"),
                )
            finally:
                con.close()

            with mt.connect(db, memory_profile="low") as terms:
                info = terms.lookup("ICD10CM", "E11.9")
                df = terms.lookup_df("ICD10CM", ["E11.9"])

            assert info.name == "Type 2 diabetes mellitus"
            assert df.to_dict("records")[0]["code"] == "E11.9"
        """
    ).strip()


if __name__ == "__main__":
    raise SystemExit(main())
