"""AMUX-44 regression guard: no machine-specific absolute host paths and no
fhir4px-model references may ship as defaults in package code.

The original bug class: ``/mnt/d/...`` literals worked on the author's
machine, silently degraded in containers, and the /mnt/d/medterm4ds tree
was later deleted outright — defaults then pointed at paths that exist
nowhere. fhir4px-model is a separate data home (451 GB, pending its own
migration), not a stable API surface; shipped code must never default
into it (the terminology release provisioned under the cache root is the
source of truth — see medterm4ds.core.paths).

The guard scans AST string literals (code, not prose): comments and
docstrings may legitimately reference the history. Non-Python shipped
files (deploy manifests/README, notebooks, pyproject) are scanned as
text because everything in them is configuration.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Machine-specific absolute host paths and the forbidden data-home repo.
HOST_PATH_PATTERNS = (
    re.compile(r"/mnt/[a-z]/"),  # WSL drive mounts (/mnt/d/...)
    re.compile(r"/home/[a-z][^/\s]*/"),  # user homes (/home/jmontavon/...)
    re.compile(r"[CD]:\\\\"),  # Windows drive paths (C:\, D:\)
    re.compile(r"fhir4px-model"),  # data home, never a default target
)

PY_SCOPES = [
    REPO / "src" / "medterm4ds",
    REPO / "deploy",
]
TEXT_SCOPES = [
    (REPO / "deploy", ("*.py", "*.md", "Dockerfile*")),
    (REPO / "notebooks", ("*.ipynb",)),
    (REPO / "pyproject.toml", ()),
]


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Linenos of module/class/function docstrings (allowed prose)."""
    docNodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docNodes.add(first.lineno)
    return docNodes


def _python_violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = _docstring_nodes(tree)
    bad: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if node.lineno in docs:
            continue
        for pat in HOST_PATH_PATTERNS:
            if pat.search(node.value):
                bad.append(
                    f"{path.relative_to(REPO)}:{node.lineno}: "
                    f"{pat.pattern!r} in literal {node.value[:80]!r}"
                )
    return bad


def _text_violations(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    bad: list[str] = []
    for i, line in enumerate(text.splitlines(), 1):
        for pat in HOST_PATH_PATTERNS:
            if pat.search(line):
                bad.append(
                    f"{path.relative_to(REPO)}:{i}: {pat.pattern!r} in {line.strip()[:80]!r}"
                )
    return bad


def test_no_host_paths_or_fhir4px_model_in_shipped_code():
    violations: list[str] = []
    for scope in PY_SCOPES:
        for path in sorted(scope.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            violations += _python_violations(path)
    for scope, patterns in TEXT_SCOPES:
        if scope.is_file():
            violations += _text_violations(scope)
            continue
        for pattern in patterns:
            for path in sorted(scope.rglob(pattern)):
                if ".ipynb_checkpoints" in path.parts or "__pycache__" in path.parts:
                    continue
                violations += _text_violations(path)
    assert not violations, (
        "Machine-specific host paths / fhir4px-model references found in "
        "shipped code. Defaults must resolve env var → cache-root-derived "
        "(medterm4ds.core.paths) → fail loud. Offenders:\n  " + "\n  ".join(violations)
    )
