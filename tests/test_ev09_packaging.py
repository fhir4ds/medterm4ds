"""EV-09 (packaging-examples) pins — iterations 25-27.

Covers: sdist include-list honesty (the v0.0.3 'Tag completeness trap'
follow-up: ``/scripts`` was listed in the sdist include list but scripts/
is fully gitignored, so the entry could never contribute a file), console
entry points declared = implemented, ``all`` extra coverage, and example
docs using only kwargs the shipped extraction API accepts (the
``categories=`` drift: three website docs called ``mt.extract`` with a
kwarg removed long ago — every copy-paste raised TypeError).
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
WEBSITE_DOCS = ROOT / "web" / "website" / "docs"


def _pyproject_text() -> str:
    return PYPROJECT.read_text()


class TestEv09SkepticSdistIncludeHonesty:
    """Every sdist include entry must name a path with tracked content.

    hatchling excludes gitignored paths, so an include entry naming a
    fully-gitignored directory ships NOTHING while reading as if it ships
    (``/scripts`` — v0.0.3 QA-001 trap). The shipped tests that execute
    ROOT/scripts/*.py skip cleanly when the dir is absent, so the trap is
    mitigated; the dead include entry is still misleading and is removed.
    """

    def test_no_dead_sdist_include_entries(self):
        text = _pyproject_text()
        block = re.search(
            r"\[tool\.hatch\.build\.targets\.sdist\]\s*include\s*=\s*\[(.*?)\]",
            text,
            re.S,
        )
        assert block is not None, "no sdist include list found in pyproject.toml"
        entries = re.findall(r'"(/[^"]+)"', block.group(1))
        assert entries, "sdist include list is empty"

        tracked = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.splitlines()
        tracked_roots = {t.split("/", 1)[0] for t in tracked}

        dead = [
            e
            for e in entries
            # strip leading slash -> top-level name; files (e.g. /README.md)
            # match tracked files exactly, directories match any tracked
            # path under that root.
            if e.lstrip("/") not in tracked
            and e.lstrip("/").split("/", 1)[0] not in tracked_roots
        ]
        assert not dead, (
            f"sdist include entries name paths with no tracked content: {dead} "
            "(gitignored dirs ship nothing — remove the entry; see v0.0.3 QA-001)"
        )

    def test_scripts_dir_not_promised_in_sdist(self):
        """Direct pin: /scripts must not appear in the include list."""
        block = re.search(
            r"\[tool\.hatch\.build\.targets\.sdist\]\s*include\s*=\s*\[(.*?)\]",
            _pyproject_text(),
            re.S,
        )
        assert block is not None
        assert '"/scripts"' not in block.group(1)


class TestEv09SkepticEntryPointsDeclaredAndImplemented:
    """[project.scripts] targets must be importable callables."""

    def test_console_scripts_exist(self):
        import importlib

        block = re.search(
            r"\[project\.scripts\](.*?)(?:\n\[|\Z)", _pyproject_text(), re.S
        )
        assert block is not None, "no [project.scripts] table"
        targets = re.findall(r'=\s*"?([\w.]+):(\w+)"?', block.group(1))
        assert targets, "no console scripts declared"
        for module_path, func in targets:
            module = importlib.import_module(module_path)
            assert callable(getattr(module, func)), (
                f"{module_path}:{func} is not callable"
            )


class TestEv09SkepticAllExtraCoverage:
    """The ``all`` extra must be the union of the feature extras' deps."""

    @staticmethod
    def _extra_deps(extra: str) -> set[str]:
        from packaging.requirements import Requirement

        text = _pyproject_text()
        block = re.search(
            rf"^{extra}\s*=\s*\[(.*?)\]", text, re.S | re.M
        )
        assert block is not None, f"extra {extra!r} not found"
        # drop comment lines (they quote example installs like en-core-web-sm)
        body = "\n".join(
            ln for ln in block.group(1).splitlines() if not ln.lstrip().startswith("#")
        )
        return {Requirement(r.strip()).name for r in re.findall(r'"([^"]+)"', body)}

    def test_all_covers_feature_extras_union(self):
        feature_extras = ["search", "extraction", "api", "fhir", "mcp", "dataframe"]
        union: set[str] = set()
        for extra in feature_extras:
            union |= self._extra_deps(extra)
        all_deps = self._extra_deps("all")
        assert all_deps == union, (
            f"`all` extra drifted from the union of feature extras: "
            f"missing={union - all_deps} extra={all_deps - union}"
        )

    def test_hard_dependencies_parse(self):
        from packaging.requirements import InvalidRequirement, Requirement

        text = _pyproject_text()
        block = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.S | re.M)
        assert block is not None
        for req in re.findall(r'"([^"]+)"', block.group(1)):
            Requirement(req)  # raises InvalidRequirement on drift


class TestEv09HistorianExtractDocsKwargs:
    """Website example code must only use kwargs the shipped API accepts.

    Found drifting: three docs called ``mt.extract(..., categories=...)``
    — the parameter is ``result_types`` (and has been across 0.0.x);
    ``ExtractionService.extract`` takes no ``**kwargs``, so the docs'
    primary example raised TypeError on copy-paste.
    """

    DOC_GLOB = "**/*.md"
    SURFACES = {
        "extract": ("medterm4ds.services.extraction", "ExtractionService", "extract"),
        "find_terms": ("medterm4ds.services.extraction", "ExtractionService", "find_terms"),
        "resolve_spans": (
            "medterm4ds.services.extraction",
            "ExtractionService",
            "resolve_spans",
        ),
    }

    @staticmethod
    def _python_blocks(path: Path):
        for m in re.finditer(r"```python\n(.*?)```", path.read_text(), re.S):
            try:
                yield ast.parse(m.group(1))
            except SyntaxError:
                continue  # narrative fragments, not runnable blocks

    def _surface_params(self) -> dict[str, set[str]]:
        import importlib
        import inspect

        params: dict[str, set[str]] = {}
        for surface, (mod, cls, meth) in self.SURFACES.items():
            fn = getattr(getattr(importlib.import_module(mod), cls), meth)
            sig = inspect.signature(fn)
            params[surface] = {
                p.name
                for p in sig.parameters.values()
                if p.name not in ("self", "text") and p.kind not in (p.VAR_KEYWORD, p.VAR_POSITIONAL)
            }
        return params

    def test_website_extract_examples_use_valid_kwargs(self):
        if not WEBSITE_DOCS.is_dir():
            pytest.skip("website docs not present in this checkout")
        allowed = self._surface_params()
        offenders: list[str] = []
        for path in sorted(WEBSITE_DOCS.glob(self.DOC_GLOB)):
            for tree in self._python_blocks(path):
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    func = node.func
                    name = getattr(func, "attr", None) or getattr(func, "id", None)
                    if name not in allowed or not node.keywords:
                        continue
                    bad = [
                        kw.arg or "**"
                        for kw in node.keywords
                        if kw.arg not in allowed[name]
                    ]
                    if bad:
                        offenders.append(f"{path.relative_to(ROOT)}: {name}(...{bad})")
        assert not offenders, (
            "example docs call extraction surfaces with unknown kwargs "
            f"(copy-paste raises TypeError): {offenders}"
        )

    def test_no_categories_kwarg_anywhere_in_docs(self):
        """Cheap grep pin: the removed ``categories=`` kwarg must not return."""
        if not WEBSITE_DOCS.is_dir():
            pytest.skip("website docs not present in this checkout")
        hits = [
            str(p.relative_to(ROOT))
            for p in sorted(WEBSITE_DOCS.glob(self.DOC_GLOB))
            if re.search(r"\bmt\.extract\([^)]*categories\s*=", p.read_text(), re.S)
        ]
        assert not hits, f"docs still use the removed categories= kwarg: {hits}"


class TestEv09HistorianScriptsTrapMitigated:
    """The 9 ROOT/scripts-dependent tests must keep their skip guards.

    scripts/ is gitignored operator-local tooling; the suites that execute
    scripts/*.py skip (not fail) in clean checkouts and sdists. If these
    guards are removed, every clean checkout of a tag fails its own suite
    again (the original v0.0.3 QA-001 trap).
    """

    @pytest.mark.parametrize(
        "suite", ["test_hardening_scripts.py", "test_notebooks.py"]
    )
    def test_script_suites_skip_when_scripts_absent(self, suite):
        source = (ROOT / "tests" / suite).read_text()
        assert "skipif" in source and '"scripts"' in source or "/scripts" in source, (
            f"{suite} lost its scripts/-presence skipif guard"
        )

    def test_scripts_dir_is_untracked(self):
        """scripts/ stays operator-local: no tracked file under scripts/."""
        result = subprocess.run(
            ["git", "ls-files", "scripts"], cwd=ROOT, capture_output=True, text=True
        )
        assert result.stdout.strip() == "", (
            "scripts/ gained tracked files — revisit the sdist include "
            "decision (v0.0.3 QA-001)"
        )


class TestEv09ExplorerBaseInstallImports:
    """The base install (duckdb + huggingface-hub only) must stay usable.

    PR-#19 introduced a module-level ``from spacy... import`` in
    services/extraction_cues.py; apps/cli.py's ``build_parser()`` (via
    services.extraction) and apps/fhir_api.py's module import (via
    DEFAULT_SEARCH_MODE) pulled it in eagerly — ``medterm4ds --version``
    crashed with ModuleNotFoundError: No module named 'spacy' on every
    install without the extraction extra, and ``pip install
    medterm4ds[fhir]`` lost create_fhir_app. spacy must stay an
    extraction-extra (medspacy-transitive) dependency.
    """

    BLOCK_SPACY = """
import sys

class _Blocker:
    def find_spec(self, name, path=None, target=None):
        if name == "spacy" or name.startswith("spacy."):
            raise ImportError("blocked: simulating base install (no spacy)")

sys.meta_path.insert(0, _Blocker())
"""

    def _run_without_spacy(self, code: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-c", self.BLOCK_SPACY + code],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )

    def test_cli_build_parser_works_without_spacy(self):
        result = self._run_without_spacy(
            "from medterm4ds.apps.cli import build_parser;"
            "p = build_parser(); assert p is not None; print('OK')"
        )
        assert result.returncode == 0, (
            f"base-install CLI broken (ModuleNotFoundError?): {result.stderr[-500:]}"
        )

    def test_fhir_app_factory_importable_without_spacy(self):
        result = self._run_without_spacy(
            "from medterm4ds.apps.fhir_api import create_fhir_app;"
            "print('OK')"
        )
        assert result.returncode == 0, (
            f"[fhir]-only install lost create_fhir_app: {result.stderr[-500:]}"
        )

    def test_extraction_service_module_importable_without_spacy(self):
        """services.extraction re-exports cue constants; must import lazily."""
        result = self._run_without_spacy(
            "from medterm4ds.services.extraction import DEFAULT_SEARCH_MODE;"
            "assert DEFAULT_SEARCH_MODE; print('OK')"
        )
        assert result.returncode == 0, result.stderr[-500:]

    def test_cues_runtime_still_requires_spacy_loudly(self):
        """The lazy boundary fails LOUDLY at use time (not import time)."""
        result = self._run_without_spacy(
            "from medterm4ds.services.extraction_cues import"
            " _register_doc_extensions\n"
            "try:\n"
            "    _register_doc_extensions()\n"
            "except ImportError as exc:\n"
            "    assert 'spacy' in str(exc), exc\n"
            "else:\n"
            "    raise AssertionError('spacy import should fail when blocked')\n"
            "print('OK')"
        )
        assert result.returncode == 0, result.stderr[-500:]
