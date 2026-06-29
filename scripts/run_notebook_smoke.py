#!/usr/bin/env python3
"""Execute example notebooks against a tiny synthetic UMLS DuckDB database."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import builtins
import json
import os
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from synthetic_umls import create_synthetic_umls_db

from medterm4ds.engines.duckdb.prepared import verify_mt4ds_schema
from medterm4ds.services.schema_reporting import (
    empty_schema_report_metadata,
    report_db_role_metadata,
    schema_report_metadata,
)


@dataclass(frozen=True)
class NotebookResult:
    notebook: str
    status: str
    elapsed_seconds: float
    executed_cells: int
    error: str | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notebook-dir", default=str(ROOT / "notebooks"))
    parser.add_argument("--db", default=None, help="DuckDB database path. Defaults to a synthetic DB.")
    parser.add_argument("--db-role", default=None)
    parser.add_argument("--output-json", default=None)
    return parser.parse_args()


def _schema_metadata(db_path: Path) -> dict[str, Any]:
    try:
        import duckdb

        con = duckdb.connect(str(db_path), read_only=True)
        try:
            report = verify_mt4ds_schema(con)
        finally:
            con.close()
    except Exception:
        return empty_schema_report_metadata()
    return schema_report_metadata(report)


def main() -> int:
    args = parse_args()
    notebook_dir = Path(args.notebook_dir)
    notebooks = sorted(notebook_dir.glob("*.ipynb"))
    # Skip notebooks that require the real UMLS DB or external search indexes
    SKIP_AGAINST_SYNTHETIC = {"fhir_terminology_server_demo.ipynb"}
    notebooks = [nb for nb in notebooks if nb.name not in SKIP_AGAINST_SYNTHETIC]
    if not notebooks:
        print(f"No notebooks found in {notebook_dir}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="medterm4ds-notebooks-") as tmp:
        db_path = Path(args.db) if args.db else create_synthetic_umls_db(Path(tmp) / "umls.duckdb")
        db_role = args.db_role or ("unknown" if args.db else "synthetic")
        results = [_execute_notebook(path, db_path=db_path) for path in notebooks]
        db_metadata = _schema_metadata(db_path)

    db_role_metadata = report_db_role_metadata(db_role, db_metadata)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "notebook_dir": str(notebook_dir),
        "db_path": str(db_path),
        "db_role": db_role_metadata["db_role"],
        "db_role_source": db_role_metadata["db_role_source"],
        "manifest_db_role": db_metadata.get("manifest_db_role"),
        "source_archive": db_metadata.get("source_archive"),
        "umls_release": db_metadata.get("umls_release"),
        "prepared_schema_version": db_metadata.get("prepared_schema_version"),
        "patient_friendly_policy_version": db_metadata.get("patient_friendly_policy_version"),
        "prepared_tables": db_metadata.get("prepared_tables"),
        "missing_prepared_tables": db_metadata.get("missing_prepared_tables"),
        "schema_errors": db_metadata.get("schema_errors"),
        "results": [asdict(result) for result in results],
    }
    if args.output_json:
        Path(args.output_json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({result.notebook: result.status for result in results}, sort_keys=True))
    return 0 if all(result.status == "pass" for result in results) else 1


def _execute_notebook(path: Path, *, db_path: Path) -> NotebookResult:
    start = time.perf_counter()
    namespace: dict[str, Any] = {
        "__name__": "__notebook__",
        "display": _display,
        "MEDTERM4DS_DB": str(db_path),
    }
    previous_db = os.environ.get("MEDTERM4DS_DB")
    os.environ["MEDTERM4DS_DB"] = str(db_path)
    executed_cells = 0
    try:
        notebook = json.loads(path.read_text(encoding="utf-8"))
        for index, cell in enumerate(notebook.get("cells", []), start=1):
            if cell.get("cell_type") != "code":
                continue
            source = _cell_source(cell)
            if not source.strip():
                continue
            code = compile(source, f"{path}:{index}", "exec")
            exec(code, namespace, namespace)
            executed_cells += 1
    except Exception as exc:  # noqa: BLE001
        return NotebookResult(
            notebook=path.name,
            status="fail",
            elapsed_seconds=time.perf_counter() - start,
            executed_cells=executed_cells,
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if previous_db is None:
            os.environ.pop("MEDTERM4DS_DB", None)
        else:
            os.environ["MEDTERM4DS_DB"] = previous_db

    return NotebookResult(
        notebook=path.name,
        status="pass",
        elapsed_seconds=time.perf_counter() - start,
        executed_cells=executed_cells,
    )


def _cell_source(cell: dict[str, Any]) -> str:
    source = cell.get("source", "")
    if isinstance(source, list):
        return "".join(source)
    return str(source)


def _display(*values: Any) -> None:
    """Small display replacement for smoke execution outside Jupyter."""
    if values:
        builtins.print(values[-1])


if __name__ == "__main__":
    raise SystemExit(main())
