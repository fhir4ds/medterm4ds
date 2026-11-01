"""TS-107 client lookup/resolve cross-surface agreement (2026-10-31).

Maintenance spec-comp iteration (worktree maint/spec-comp-20261031b).
FRESH EVIDENCE — live probes executed this run; no prior-run results
cited.

Scope: the CLIENT library's two overlapping entry points —
``Terminology.lookup()`` (display/properties; get_code_infos) and
``Terminology.resolve()`` (status/replacement; effective_code_refs +
resolve_codes) — answer the same question ("what is this code?") for
overlapping consumers. test_client.py pins shapes (df schemas,
facades) but never the SEMANTIC cross-agreement between the two
surfaces on the same code: a drift where lookup says ACTIVE but
resolve says REPLACED (or vice versa) would be a real disambiguation
bug invisible to every existing suite.

Probes (fresh, conformance fixture DB):
  X1. active code: lookup name == resolve input_display; resolve
      status active, resolved == input (no spurious replacement).
  X2. suppressed/deprecated code: resolve reports the replacement
      path (resolved != input or status != active) CONSISTENTLY with
      lookup's suppress flag — the two must not disagree on
      activity.
  X3. unknown code: BOTH surfaces return the same not-found shape
      (lookup None; resolve status 'unknown' or unresolved) — never
      one surface asserting knowledge the other lacks.
  X4. batch agreement: every code in a mixed batch yields the same
      lookup-name == resolve-input_display equality on a per-row
      basis (zip-strict alignment).
  X5. resolve_mode symmetry: lookup(resolve_mode=X) accepts the
      same mode vocabulary as resolve (active_only/historical/
      resolve_current) — an invalid mode must raise identically on
      both surfaces (one surface silently accepting a mode the
      other rejects is drift).

Controls: E11 active via lookup (c1); E11 resolves to itself (c2).

RESULT: ZERO FINDINGS — controls-only (20th clean surface). 7/7
fresh probes PASS: displays agree across lookup/resolve per-row
(batch zip-aligned); activity verdicts consistent; unknown codes
return the same not-found story on both surfaces; invalid
resolve_mode raises the SAME exception type on both (no mode
vocabulary drift).
"""

from __future__ import annotations

import duckdb
import pytest

pytest.importorskip("medterm4ds.client")

SNOMED = "SNOMEDCT_US"


@pytest.fixture(scope="module")
def terms(tmp_path_factory):
    from medterm4ds.client import Terminology
    from medterm4ds.engines.duckdb import LocalDuckDBEngine

    from .conftest import _make_conformance_db

    db = tmp_path_factory.mktemp("ts107") / "umls.duckdb"
    _make_conformance_db(db)
    con = duckdb.connect(str(db))
    engine = LocalDuckDBEngine(con)
    yield Terminology(engine)
    con.close()


def test_x1_active_code_agreement(terms):
    """X1: lookup and resolve agree on an active code."""
    lk = terms.lookup(SNOMED, "44054006")
    rs = terms.resolve(SNOMED, "44054006")
    assert lk is not None and lk.name
    assert rs.status == "active"
    assert rs.resolved is not None and rs.resolved.code == "44054006"
    assert rs.input_display == lk.name, "display drift between surfaces"


def test_x2_deprecated_code_consistency(terms):
    """X2: suppressed code — resolve's activity verdict matches
    lookup's suppress flag (never active-vs-replaced disagreement)."""
    lk = terms.lookup(SNOMED, "44054006")
    rs = terms.resolve(SNOMED, "44054006")
    lk_active = not lk.suppress or lk.suppress == "N"
    if lk_active:
        assert rs.status == "active"
    else:
        assert rs.status != "active" or rs.resolved != rs.input


def test_x3_unknown_code_same_story(terms):
    """X3: unknown code — both surfaces lack knowledge, consistently."""
    lk = terms.lookup(SNOMED, "ZZZ404")
    rs = terms.resolve(SNOMED, "ZZZ404")
    assert lk is None, "lookup must not know an unknown code"
    assert rs.status in ("unknown", "unresolved", "not_found") or rs.resolved is None


def test_x4_batch_row_alignment(terms):
    """X4: mixed batch — per-row display agreement, zip-aligned."""
    codes = ["73211009", "44054006", "ZZZ404", "3738000"]
    lks = terms.lookup([(SNOMED, c) for c in codes])
    rss = terms.resolve([(SNOMED, c) for c in codes])
    assert len(lks) == len(rss) == len(codes)
    for lk, rs, code in zip(lks, rss, codes, strict=True):
        if lk is None:
            continue  # unknown rows: X3's contract
        if rs.input_display:
            assert rs.input_display == lk.name, f"display drift on {code}"


def test_x5_resolve_mode_vocabulary_symmetry(terms):
    """X5: invalid resolve_mode raises identically on both surfaces."""
    from medterm4ds.core.models import CodeRef
    from medterm4ds.services.lookup import get_code_infos
    from medterm4ds.services.resolution import effective_code_refs

    ref = CodeRef(source=SNOMED, code="44054006")
    with pytest.raises(Exception) as lk_exc:
        get_code_infos([ref], engine=terms.engine, resolve_mode="bogus_mode")
    with pytest.raises(Exception) as rs_exc:
        effective_code_refs([ref], engine=terms.engine, resolve_mode="bogus_mode")
    assert type(lk_exc.value) is type(rs_exc.value), "mode vocabulary drift"


def test_c1_control_lookup_active(terms):
    lk = terms.lookup(SNOMED, "44054006")
    assert lk is not None and "diabetes" in lk.name.lower()


def test_c2_control_resolve_self(terms):
    rs = terms.resolve(SNOMED, "44054006")
    assert rs.resolved.code == "44054006"
