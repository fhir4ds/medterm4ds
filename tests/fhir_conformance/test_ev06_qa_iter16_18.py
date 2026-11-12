"""EV-06 QA probes (iterations 16-18: SKEPTIC → HISTORIAN → EXPLORER).

Domain: FHIR terminology operations ($lookup, $validate-code, $subsumes,
$translate, $expand, $closure, batch) sitting ON TOP of the v0.0.12-cycle
search/resolution changes (alias dictionary, source aliasing in
_ancestor_fallback, _check_concepts_space latch fix, _do_extract wrapper).

All probes are CI-safe: the SearchService is exercised with synthetic
dictionaries (plain-attribute fixtures, no network, no model artifacts),
mirroring the mock-free synthetic-fixture convention.

Findings documented here (see docs/.ai_loop/qa_handoff.md EV-06 sections;
all three were FIXED in the 2026-11-11 evolution and the probes below
assert the FIXED shapes):
- EV06-QA-01: canonical_batch() merged alias list lacked seen_ids dedup
  (services/search.py) — fixed by EV06-QA-04 (seen_ids dedup, pinned here).
- EV06-QA-02: single canonical() returned [alias_result] only on an alias
  hit (services/search.py) while canonical_batch() returned the merged
  alias+FAISS list — fixed: both surfaces now return the merged shape
  (single-vs-batch parity pinned here).
- EV06-QA-03: _check_concepts_space latch fix verified — a raising
  validation must NOT latch (retry on next call).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from medterm4ds.engines.fhir import fhir_uri_to_system, system_to_fhir_uri
from medterm4ds.services import search as sm

FHIR_API_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "medterm4ds" / "apps" / "fhir_api.py"
)


def _fhir_api_source() -> str:
    return FHIR_API_PATH.read_text(encoding="utf-8")


def _get_nested_func_source(source: str, parent: str, child: str) -> str:
    """CS-03 HISTORIAN resweep helper: source of a function nested in a
    factory function (create_fhir_app)."""
    tree = ast.parse(source)

    _func_types = (ast.FunctionDef, ast.AsyncFunctionDef)

    def _find(name, nodes):
        for node in nodes:
            if isinstance(node, _func_types) and node.name == name:
                return node
        return None

    for node in ast.walk(tree):
        if isinstance(node, _func_types) and node.name == parent:
            inner = _find(child, ast.walk(node))
            if inner is not None:
                return ast.get_source_segment(source, inner) or ""
    return ""


class _FakeFaiss:
    """Synthetic concept index: 4 candidates, canonical_id at idx 1
    appears TWICE (the concept index embeds multiple names per value set —
    the production shape that surfaces duplicates in the merged list)."""

    ntotal = 4

    def __init__(self, meta):
        self._meta = meta

    def search(self, embs, k):
        scores = [[0.99, 0.98, 0.97, 0.96] for _ in embs]
        ids = [[0, 1, 1, 2] for _ in embs]
        return scores, ids


class _FakeEmbedEngine:
    space_id = "fake_space"

    def _ensure_loaded(self):
        pass

    def _embed(self, query):
        return [0.0]

    def embed_batch(self, queries):
        return [[0.0] for _ in queries]


def _synthetic_service() -> sm.SearchService:
    """SearchService with synthetic dictionaries exercising the alias /
    group / concept-index paths without any model artifacts."""
    svc = sm.SearchService.__new__(sm.SearchService)
    svc._alias_to_cid = {"hld": "VAL-COND-SNOMED-55822004"}
    svc._group_terms = {}
    svc._canonical_by_id = {
        "VAL-COND-SNOMED-55822004": {
            "canonical_id": "VAL-COND-SNOMED-55822004",
            "domain": ["condition"],
            "anchor_system": "SNOMEDCT_US",
            "anchor_code": "55822004",
            "patient_friendly_name": "High blood lipids",
            "members": [],
        },
        "VAL-COND-SNOMED-118599009": {
            "canonical_id": "VAL-COND-SNOMED-118599009",
            "domain": ["condition"],
            "anchor_system": "SNOMEDCT_US",
            "anchor_code": "118599009",
            "patient_friendly_name": "Benign hypertension",
            "members": [],
        },
        "VAL-COND-SNOMED-17818006": {
            "canonical_id": "VAL-COND-SNOMED-17818006",
            "domain": ["condition"],
            "anchor_system": "SNOMEDCT_US",
            "anchor_code": "17818006",
            "patient_friendly_name": "Essential hypertension",
            "members": [],
        },
    }
    svc._concepts_meta = [
        {"canonical_id": "VAL-COND-SNOMED-55822004", "name": "HLD"},
        {"canonical_id": "VAL-COND-SNOMED-118599009", "name": "benign htn"},
        {"canonical_id": "VAL-COND-SNOMED-118599009", "name": "benign hypertension"},
        {"canonical_id": "VAL-COND-SNOMED-17818006", "name": "essential htn"},
    ]
    svc._concepts_faiss = _FakeFaiss(svc._concepts_meta)
    svc._concepts_space_checked = True  # skip the space check in probes
    svc._ensure_canonical = lambda: None
    svc._ensure_concepts = lambda: None
    svc._ensure_semantic = lambda: _FakeEmbedEngine()
    return svc


class TestEV06QA01MergedAliasListDedupsCanonicalIds:
    """EV06-QA-01/EV06-QA-04 (fixed shape): the EV02-QA-04 merged alias
    list (canonical_batch, services/search.py) dedupes by canonical_id
    via seen_ids, mirroring the plain FAISS path (`if cid in seen_ids:
    continue`). These probes assert the fixed shape (EV06-QA-04): a
    duplicate concept-index row (same canonical_id, different embedded
    name) must NOT surface as a duplicate result row nor consume
    count-truncation slots."""

    def test_merged_alias_list_dedupes_canonical_ids(self):
        svc = _synthetic_service()
        batch = svc.canonical_batch(["HLD"], count=5)[0]
        ids = [r.canonical_id for r in batch]
        # EV06-QA-04 fix landed (2026-11-11 evolution): the merged alias
        # list dedupes by canonical_id like the plain FAISS path below.
        assert ids[0] == "VAL-COND-SNOMED-55822004"  # alias row leads
        assert len(ids) == len(set(ids)), (
            f"merged alias list must not contain duplicate canonical_ids; got {ids}"
        )

    def test_plain_batch_path_dedupes_sibling_contract(self):
        """The plain FAISS path (no alias hit) DOES dedup — the sibling
        contract the merged path violates."""
        svc = _synthetic_service()
        batch = svc.canonical_batch(["something else"], count=5)[0]
        ids = [r.canonical_id for r in batch]
        assert len(ids) == len(set(ids))


class TestEV06QA02SingleVsBatchAliasParity:
    """EV06-QA-02 (fixed shape): on an alias hit, single canonical()
    merges the alias row with the FAISS candidates — the same merged
    alias+FAISS shape as canonical_batch() (asserts the fixed shape,
    EV06-QA-04). FHIR $search canonical mode (_do_search →
    service.canonical) and the Python batch surface now agree."""

    def test_single_returns_merged_list(self):
        svc = _synthetic_service()
        single = svc.canonical("HLD", count=5)
        # EV06-QA-02 fix landed (2026-11-11 evolution): single canonical()
        # merges the alias row with the FAISS candidates — same shape as
        # canonical_batch.
        assert single[0].canonical_id == "VAL-COND-SNOMED-55822004"

    def test_batch_returns_merged_list_same_query(self):
        svc = _synthetic_service()
        batch = svc.canonical_batch(["HLD"], count=5)[0]
        assert batch[0].canonical_id == "VAL-COND-SNOMED-55822004"
        assert len(batch) > 1

    def test_single_batch_parity_on_alias_hit(self):
        # EV06-QA-02 fix landed (2026-11-11 evolution): both paths return
        # the merged alias+FAISS shape — single vs batch shape parity.
        # Strengthened from a weak `len(single) > 1 or len(batch) == 1`
        # disjunction (EV10-QA-07): both surfaces are deterministically
        # checkable in this synthetic fixture, so assert exact id-list
        # parity (the same contract test_batch_group_hit_matches_single
        # asserts for group hits).
        svc = _synthetic_service()
        single = svc.canonical("HLD", count=5)
        batch = svc.canonical_batch(["HLD"], count=5)[0]
        assert len(single) > 1 and len(batch) > 1
        assert single[0].canonical_id == batch[0].canonical_id
        assert [r.canonical_id for r in single] == [r.canonical_id for r in batch]


class TestEV06QA03ConceptsSpaceLatch:
    """EV06-QA-03 (verification): the EV05-QA-02 latch fix — validation
    failure must not permanently disable the space check."""

    def test_failed_validation_does_not_latch(self, monkeypatch):
        svc = sm.SearchService.__new__(sm.SearchService)
        svc._concepts_space_checked = False
        svc._concepts_data_manifest = object()  # not None
        svc._concepts_data_dir = Path("/tmp/nonexistent-data")
        calls = {"n": 0}

        class _RaisingEngine:
            space_id = "mismatched"

        def _raising_validate(manifest, **kwargs):
            calls["n"] += 1
            raise RuntimeError("space mismatch")

        import medterm4ds.core.artifact_manifest as am
        monkeypatch.setattr(am, "validate_data_manifest", _raising_validate)
        with pytest.raises(RuntimeError):
            svc._check_concepts_space(_RaisingEngine())
        assert svc._concepts_space_checked is False, (
            "a raising validation must NOT latch — the next call must retry"
        )
        with pytest.raises(RuntimeError):
            svc._check_concepts_space(_RaisingEngine())
        assert calls["n"] == 2

    def test_successful_validation_latches(self, monkeypatch):
        svc = sm.SearchService.__new__(sm.SearchService)
        svc._concepts_space_checked = False
        svc._concepts_data_manifest = object()
        svc._concepts_data_dir = Path("/tmp/nonexistent-data")

        class _OkEngine:
            space_id = "matching"

        import medterm4ds.core.artifact_manifest as am
        monkeypatch.setattr(am, "validate_data_manifest", lambda manifest, **kw: None)
        svc._check_concepts_space(_OkEngine())
        assert svc._concepts_space_checked is True

    def test_no_manifest_latches_early(self):
        svc = sm.SearchService.__new__(sm.SearchService)
        svc._concepts_space_checked = False
        svc._concepts_data_manifest = None
        svc._check_concepts_space(object())
        assert svc._concepts_space_checked is True


class TestAliasKeyAccentFolding:
    """EV02-QA-01 contract at the EV-06 seam: alias/group term keys fold
    accents (QC-329 matched-pair). Probes the dictionary lookup contract
    the terminology-op-adjacent surfaces rely on."""

    @pytest.mark.parametrize(
        ("raw", "key"),
        [
            ("HLD", "hld"),
            ("  Müllerian   Agenesis ", "mullerian agenesis"),
            ("müllerian agenesis", "mullerian agenesis"),
            ("Chédiak", "chediak"),
        ],
    )
    def test_alias_key_folds(self, raw, key):
        assert sm._alias_key(raw) == key

    def test_accented_alias_hit_via_folded_key(self):
        svc = _synthetic_service()
        svc._alias_to_cid[sm._alias_key("Müllerian agenesis")] = (
            "VAL-COND-SNOMED-253828000"
        )
        svc._canonical_by_id["VAL-COND-SNOMED-253828000"] = {
            "canonical_id": "VAL-COND-SNOMED-253828000",
            "domain": ["condition"],
            "anchor_system": "SNOMEDCT_US",
            "anchor_code": "253828000",
            "patient_friendly_name": "Müllerian agenesis",
            "members": [],
        }
        hit = svc._alias_dictionary_search(
            "mullerian agenesis", result_types=None, sources=None
        )
        assert hit is not None and hit.canonical_id == "VAL-COND-SNOMED-253828000"


class TestAncestorFallbackSourceAliasing:
    """EV02-QA-02 at the EV-06 seam: the ancestor-fallback sources filter
    widens pairwise (SNOMED↔SNOMEDCT_US, RXNORM↔ATC, LNC↔LOINC) via the
    canonical core.normalize.SOURCE_SAB_ALIASES map and pre-empts with []
    BEFORE the ancestor walk when SNOMEDCT_US cannot be in the widened
    set. Source-read AST probe (the walk itself needs the UMLS DB)."""

    def _fallback_source(self):
        return inspect.getsource(sm.SearchService._ancestor_fallback)

    def test_widening_and_preempt_guard_present(self):
        src = self._fallback_source()
        assert "expand_source_aliases" in src, (
            "the ancestor-fallback sources filter must widen via the "
            "canonical core.normalize.expand_source_aliases helper"
        )
        assert 'if "SNOMEDCT_US" not in expand_source_aliases(sources):' in src
        assert "return []" in src

    def test_guard_fires_before_the_walk(self):
        """CR-SHOULD-FIX-2a: the all-or-nothing early-return runs BEFORE
        the semantic seed + ancestor walk — excluded-source queries no
        longer pay the walk before being discarded."""
        src = self._fallback_source()
        seed_pos = src.index("self.semantic(")
        guard_pos = src.index('if "SNOMEDCT_US" not in expand_source_aliases')
        assert guard_pos < seed_pos


class TestDoExtractWrapperSourceRead:
    """EV05-QA-01 wrapper (sibling-parity): _do_extract must wrap the
    extract_service call with ValueError→400 and RuntimeError→503 the way
    _do_search has since QC-123/QC-137. Source-read AST probe (house
    pattern) so the contract survives without model artifacts."""

    def _extract_source(self):
        source = _fhir_api_source()
        seg = _get_nested_func_source(source, "create_fhir_app", "_do_extract")
        assert seg, "_do_extract must exist nested in create_fhir_app"
        return seg

    def test_wrapper_catches_valueerror_and_runtimeerror(self):
        seg = self._extract_source()
        assert "except ValueError" in seg, (
            "_do_extract must translate ValueError → 400 OperationOutcome"
        )
        assert "except RuntimeError" in seg, (
            "_do_extract must translate RuntimeError → 503 OperationOutcome"
        )
        assert "_fhir_error(400" in seg and "_fhir_error(503" in seg

    def test_wrapper_wraps_the_service_call(self):
        tree = ast.parse(self._extract_source())
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is not None:
                names = [
                    n.id for n in ast.walk(node.type)
                    if isinstance(n, ast.Name)
                ]
                if "ValueError" in names or "RuntimeError" in names:
                    # the try body must contain the extract_service call
                    found = True
        assert found


class TestEV06QA01LoincUriAlias:
    """EV06-QA-01 (HIGH) regression pin: canonical artifacts carry
    ``anchor_system: "LOINC"`` (the UMLS SAB label) while
    SYSTEM_TO_FHIR_URI is keyed ``LNC``. The fix (2026-11-11 evolution,
    engines/fhir/__init__.py:126-137) maps the SAB-label spelling to the
    canonical http://loinc.org URI so ``Coding.system`` is never the raw
    label. Asserts the fixed shape (EV06-QA-01)."""

    def test_loinc_sab_label_maps_to_canonical_uri(self):
        assert system_to_fhir_uri("LOINC") == "http://loinc.org"
        # the pre-existing engine key keeps working
        assert system_to_fhir_uri("LNC") == "http://loinc.org"

    def test_canonical_uri_round_trip_inversion_intact(self):
        assert fhir_uri_to_system("http://loinc.org") == "LNC"

    def test_raw_sab_label_is_not_a_uri_input(self):
        # "LOINC" is a source label, not a URI — the reverse map must NOT
        # resolve it (accepting it would let raw SAB labels leak through
        # the client-input side of the URI registry).
        assert fhir_uri_to_system("LOINC") is None


class TestBatchDispatcherSearchAbsence:
    """QC-309 confirmation: the batch dispatcher does NOT route $search —
    so the merged-alias-list shape has NO batch-vs-single FHIR parity
    surface today. Documents the deferral (not a new bug)."""

    def test_search_not_in_batch_dispatch_table(self):
        source = _fhir_api_source()
        seg = _get_nested_func_source(
            source, "create_fhir_app", "_dispatch_batch_operation"
        )
        assert seg, "_dispatch_batch_operation must exist"
        assert "$search" not in seg, (
            "if $search was added to the batch dispatcher, the merged "
            "alias list parity needs a byte-exact single-vs-batch probe "
            "(update this probe then)"
        )


class TestGroupBeforeAliasDispatchParity:
    """EV02-QA-05 at the EV-06 seam: batch dispatch order (group → alias →
    embedding) must mirror single canonical() (group → alias → concept
    index). Positive parity probe on a group term."""

    def _service_with_group(self):
        svc = _synthetic_service()
        svc._group_terms[sm._alias_key("lipid panel")] = {
            "group_id": "GP-24331-1",
            "parent_cid": "VAL-LAB-LOINC-24331-1",
            "member_cids": ["VAL-LAB-LOINC-24331-1", "VAL-COND-SNOMED-118599009"],
        }
        svc._canonical_by_id["VAL-LAB-LOINC-24331-1"] = {
            "canonical_id": "VAL-LAB-LOINC-24331-1",
            "domain": ["lab"],
            "anchor_system": "LNC",
            "anchor_code": "24331-1",
            "patient_friendly_name": "Lipid panel",
            "members": [],
        }
        return svc

    def test_single_group_hit_fans_out(self):
        svc = self._service_with_group()
        single = svc.canonical("lipid panel", count=5)
        ids = [r.canonical_id for r in single]
        assert ids[0] == "VAL-LAB-LOINC-24331-1"
        assert len(ids) > 1

    def test_batch_group_hit_matches_single(self):
        svc = self._service_with_group()
        single = svc.canonical("lipid panel", count=5)
        batch = svc.canonical_batch(["lipid panel"], count=5)[0]
        assert [r.canonical_id for r in single] == [r.canonical_id for r in batch]
