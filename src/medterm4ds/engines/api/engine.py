"""Remote API-backed terminology engine."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from medterm4ds.core.models import (
    CodeInfo,
    CodeMapping,
    CodeRef,
    CodeRelation,
    CodeResolution,
    FriendlyNameResult,
    NameSearchResult,
    OptimizeResult,
    OptimizeRule,
    Provenance,
    ProvenanceStep,
    SourceStats,
)
from medterm4ds.core.normalize import normalize_source

ApiTransport = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]

# QC-485 (HIGH): default read timeout. 30s broke facade calls the local
# engine completes — /optimize over two SNOMED codes measured 55-82s, and a
# 10,000-code /patient-friendly batch (exactly the server's documented
# MAX_CODES_PER_REQUEST cap) measured ~415s. The client timeout also counts
# server-side queue wait behind heavier requests (single-worker executor),
# so 300s is the floor that makes the documented workload domain reachable;
# bulk patient-friendly/map batches may still need an explicit
# ``timeout=600``. Raising the default cannot break a call that previously
# succeeded — it only lets slower-but-valid work complete.
DEFAULT_REMOTE_TIMEOUT = 300.0


class RemoteApiEngine:
    """Terminology engine backed by a medterm4ds FastAPI process."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = DEFAULT_REMOTE_TIMEOUT,
        headers: Mapping[str, str] | None = None,
        transport: ApiTransport | None = None,
    ):
        # QC-481 (LOW): constructor-time validation. Pre-fix, garbage inputs
        # surfaced at FIRST CALL as raw non-enveloped exceptions (None ->
        # AttributeError 'NoneType' rstrip; '' -> raw ValueError "unknown
        # url type"; timeout='abc' -> TypeError; timeout=-1 -> ValueError
        # "Timeout value out of range"). Named-parameter ValueErrors at
        # construction match the envelope every transport failure gets.
        if not isinstance(base_url, str) or not base_url.strip().lower().startswith(
            ("http://", "https://")
        ):
            raise ValueError(f"base_url must be an http(s) URL string, got {base_url!r}")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError(f"timeout must be a positive number of seconds, got {timeout!r}")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.headers = dict(headers or {})
        self.transport = transport

    def get_code_infos(self, codes: Sequence[CodeRef]) -> list[CodeInfo | None]:
        payload = {"codes": [_code_payload(code) for code in codes]}
        rows = self._results("/lookup", payload)
        return [_code_info(row) if row else None for row in rows]

    def get_code_mappings(
        self,
        codes: Sequence[CodeRef],
        *,
        target_sources: Sequence[str],
        max_results_per_code: int = 50,
        max_depth: int = 0,
        include_target_ancestors: bool = False,
        include_target_descendants: bool = False,
    ) -> list[CodeMapping]:
        payload = {
            "codes": [_code_payload(code) for code in codes],
            "target_sources": [normalize_source(source) for source in target_sources],
            "max_results_per_code": max_results_per_code,
            "max_depth": max_depth,
            "include_target_ancestors": include_target_ancestors,
            "include_target_descendants": include_target_descendants,
        }
        return [_code_mapping(row) for row in self._results("/map", payload)]

    def get_code_relations(
        self,
        codes: Sequence[CodeRef],
        *,
        direction: str,
        max_depth: int = 1,
        limit: int | None = None,
    ) -> list[CodeRelation]:
        payload = {
            "codes": [_code_payload(code) for code in codes],
            "direction": direction,
            "max_depth": max_depth,
            "limit": limit,
        }
        return [_code_relation(row) for row in self._results("/hierarchy", payload)]

    def get_source_stats(self, sources: Sequence[str] | None = None) -> list[SourceStats]:
        payload = {
            "sources": [normalize_source(source) for source in sources] if sources is not None else None
        }
        return [_source_stats(row) for row in self._results("/sources", payload)]

    def sample_source_codes(
        self,
        sources: Sequence[str],
        *,
        per_source: int = 10,
    ) -> list[CodeRef]:
        payload = {
            "sources": [normalize_source(source) for source in sources],
            "per_source": per_source,
        }
        return [CodeRef(source=row["source"], code=row["code"]) for row in self._results("/sample-codes", payload)]

    def get_code_ttys(self, codes: Sequence[CodeRef]) -> list[CodeInfo]:
        payload = {"codes": [_code_payload(code) for code in codes]}
        return [_code_info(row) for row in self._results("/code-ttys", payload)]

    def search_names(
        self,
        query: str,
        *,
        sources: Sequence[str] | None = None,
        tty_filters: Sequence[str] | None = None,
        limit: int = 25,
    ) -> list[NameSearchResult]:
        payload = {
            "query": query,
            "sources": [normalize_source(source) for source in sources] if sources is not None else None,
            "tty_filters": list(tty_filters) if tty_filters is not None else None,
            "limit": limit,
        }
        return [_name_search_result(row) for row in self._results("/search-names", payload)]

    def get_patient_friendly_names(
        self,
        codes: Sequence[CodeRef],
        max_depth: int = 5,
    ) -> list[FriendlyNameResult]:
        payload = {
            "codes": [_code_payload(code) for code in codes],
            "max_depth": max_depth,
        }
        return [_friendly_name_result(row) for row in self._results("/patient-friendly", payload)]

    def resolve_codes(self, codes: Sequence[CodeRef]) -> list[CodeResolution]:
        payload = {"codes": [_code_payload(code) for code in codes]}
        return [_code_resolution(row) for row in self._results("/resolve", payload)]

    def optimize_codes(
        self,
        codes: Sequence[CodeRef],
        *,
        relationship: str | None = None,
        output_format: str = "compact",
        include_codes: bool = False,
    ) -> OptimizeResult:
        payload = {
            "codes": [_code_payload(code) for code in codes],
            "relationship": relationship,
            "output_format": output_format,
            "include_codes": include_codes,
        }
        response = self._post("/optimize", payload)
        # QC-490 (LOW): /optimize now uses the shared 'results' envelope like
        # every other endpoint. The legacy singular 'result' key is still
        # accepted so this engine keeps working against pre-fix servers.
        rows = response.get("results")
        result = rows[0] if isinstance(rows, list) and rows else response.get("result")
        if not isinstance(result, Mapping):
            raise RuntimeError("Remote API response did not include an optimize result.")
        return _optimize_result(result)

    def health(self) -> Mapping[str, Any]:
        """Return remote API health payload."""
        return self._get("/health")

    def _results(self, path: str, payload: Mapping[str, Any]) -> list[Any]:
        response = self._post(path, payload)
        rows = response.get("results")
        if not isinstance(rows, list):
            raise RuntimeError(f"Remote API response did not include a results list for {path}.")
        return rows

    def _get(self, path: str) -> Mapping[str, Any]:
        if self.transport is not None:
            return self.transport(path, {})
        request = Request(
            f"{self.base_url}{path}",
            method="GET",
            headers={"accept": "application/json", **self.headers},
        )
        return _open_json(request, self.timeout)

    def _post(self, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if self.transport is not None:
            return self.transport(path, payload)
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            method="POST",
            headers={
                "accept": "application/json",
                "content-type": "application/json",
                **self.headers,
            },
        )
        return _open_json(request, self.timeout)


# Cap response body size so a compromised remote terminology server cannot
# OOM this client by streaming an infinitely large response.
MAX_RESPONSE_BYTES = 50 * 1024 * 1024  # re-exported for back-compat; canonical value in core.http


def _read_capped(response) -> bytes:
    """Read at most MAX_RESPONSE_BYTES from `response` using streaming reads.

    Delegated to medterm4ds.core.http.read_capped so the cap lives in one
    place across RemoteApiEngine, domains.evidence, and services.data_setup.
    """
    from medterm4ds.core.http import read_capped
    return read_capped(response, source_label="Remote API response")


def _open_json(request: Request, timeout: float) -> Mapping[str, Any]:
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(_read_capped(response).decode("utf-8"))
    except HTTPError as exc:
        # QC-479 (MEDIUM): pydantic 422 bodies echo the full request payload
        # (a 10,001-code batch produced a 430,291-char exception string, and
        # the bound was only the 50MiB read cap). Truncate the embedded
        # detail so the exception stays human-readable; 2KB is far above any
        # legitimate service error message.
        detail = _truncate_detail(_read_capped(exc).decode("utf-8", errors="replace"))
        raise RuntimeError(f"Remote API request failed with HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Remote API request failed: {exc.reason}") from exc
    except TimeoutError as exc:
        # QC-486 (LOW): socket read-timeout is NOT a URLError subclass and
        # previously leaked as a raw TimeoutError outside the RuntimeError
        # envelope — no URL, no timeout context.
        raise RuntimeError(
            f"Remote API request timed out after {timeout}s: {request.full_url}"
        ) from exc
    except json.JSONDecodeError as exc:
        # QC-482 (LOW): HTTP 200 with a non-JSON body (wrong service on the
        # port / captive portal / truncated proxy response) previously leaked
        # a raw JSONDecodeError with zero server context.
        raise RuntimeError(
            f"Remote API response from {request.full_url} was not valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError("Remote API response was not a JSON object.")
    return payload


# QC-479: cap on the error-body text embedded in the RuntimeError message.
_MAX_ERROR_DETAIL_CHARS = 2_048


def _truncate_detail(detail: str) -> str:
    if len(detail) <= _MAX_ERROR_DETAIL_CHARS:
        return detail
    return detail[:_MAX_ERROR_DETAIL_CHARS] + f"... [{len(detail) - _MAX_ERROR_DETAIL_CHARS} chars truncated]"


def _code_payload(code: CodeRef) -> dict[str, str]:
    return {"source": code.source, "code": code.code}


def _code_ref(row: Mapping[str, Any], *, source_key: str = "source", code_key: str = "code") -> CodeRef:
    return CodeRef(
        source=str(row.get(source_key) or ""),
        code=str(row.get(code_key) or ""),
    )


def _code_info(row: Mapping[str, Any]) -> CodeInfo:
    return CodeInfo(
        code=_code_ref(row),
        name=row.get("name"),
        cui=row.get("cui"),
        aui=row.get("aui"),
        tty=row.get("tty"),
        suppress=row.get("suppress"),
    )


def _source_stats(row: Mapping[str, Any]) -> SourceStats:
    return SourceStats(
        source=str(row["source"]),
        code_count=int(row["code_count"]),
        atom_count=int(row["atom_count"]),
    )


def _name_search_result(row: Mapping[str, Any]) -> NameSearchResult:
    return NameSearchResult(
        code=_code_ref(row),
        name=str(row.get("name") or ""),
        cui=row.get("cui"),
        aui=row.get("aui"),
        tty=row.get("tty"),
        match_type=str(row.get("match_type") or "contains"),
    )


def _code_mapping(row: Mapping[str, Any]) -> CodeMapping:
    return CodeMapping(
        source=_code_ref(row),
        target=_code_ref(row, source_key="target_source", code_key="target_code"),
        relationship=str(row.get("relationship") or "related-to"),
        match_type=str(row.get("match_type") or "unknown"),
        match_depth=int(row.get("match_depth") or 0),
        source_display=row.get("source_display"),
        target_display=row.get("target_display"),
        source_cui=row.get("source_cui"),
        target_cui=row.get("target_cui"),
        source_aui=row.get("source_aui"),
        target_aui=row.get("target_aui"),
        target_tty=row.get("target_tty"),
        matched_via=_provenance(row.get("matched_via")),
    )


def _code_relation(row: Mapping[str, Any]) -> CodeRelation:
    return CodeRelation(
        source=_code_ref(row),
        target=_code_ref(row, source_key="target_source", code_key="target_code"),
        relationship=str(row.get("relationship") or "related-to"),
        depth=int(row.get("depth") or 1),
        source_display=row.get("source_display"),
        target_display=row.get("target_display"),
        rel=row.get("rel"),
        rela=row.get("rela"),
        source_cui=row.get("source_cui"),
        target_cui=row.get("target_cui"),
        source_aui=row.get("source_aui"),
        target_aui=row.get("target_aui"),
    )


def _friendly_name_result(row: Mapping[str, Any]) -> FriendlyNameResult:
    code = _code_ref(row)
    return FriendlyNameResult(
        code=code,
        name=str(row.get("name") or code.code),
        friendly_source=str(row.get("friendly_source") or code.source),
        match_type=str(row.get("match_type") or "unknown"),
        match_depth=int(row.get("match_depth") or 0),
        technical_name=row.get("technical_name"),
        matched_via=_provenance(row.get("matched_via")),
    )


def _code_resolution(row: Mapping[str, Any]) -> CodeResolution:
    resolved = (
        CodeRef(source=str(row["resolved_source"]), code=str(row["resolved_code"]))
        if row.get("resolved_source") and row.get("resolved_code")
        else None
    )
    return CodeResolution(
        input=_code_ref(row),
        resolved=resolved,
        status=str(row["status"]),
        match_type=str(row["match_type"]),
        input_display=row.get("input_display"),
        resolved_display=row.get("resolved_display"),
        input_cui=row.get("input_cui"),
        resolved_cui=row.get("resolved_cui"),
        input_aui=row.get("input_aui"),
        resolved_aui=row.get("resolved_aui"),
        input_suppress=row.get("input_suppress"),
        resolved_suppress=row.get("resolved_suppress"),
        replacement_relationship=row.get("replacement_relationship"),
        normalized_code=row.get("normalized_code"),
        candidates=tuple(
            CodeRef(source=str(candidate["source"]), code=str(candidate["code"]))
            for candidate in row.get("candidates", [])
            if isinstance(candidate, Mapping)
        ),
        matched_via=_provenance(row.get("matched_via")),
    )


def _optimize_result(row: Mapping[str, Any]) -> OptimizeResult:
    rules = []
    for rule in row.get("rules", []):
        if not isinstance(rule, Mapping):
            continue
        source = str(rule.get("include_source") or row.get("source") or "")
        rules.append(
            OptimizeRule(
                include=CodeRef(source, str(rule["include"])),
                exclude=tuple(CodeRef(source, str(code)) for code in rule.get("exclude", [])),
                covered_codes=tuple(
                    CodeRef(source=str(code["source"]), code=str(code["code"]))
                    for code in rule.get("covered_codes", [])
                    if isinstance(code, Mapping)
                ),
                excluded_codes=tuple(
                    CodeRef(source=str(code["source"]), code=str(code["code"]))
                    for code in rule.get("excluded_codes", [])
                    if isinstance(code, Mapping)
                ),
            )
        )
    return OptimizeResult(
        source=str(row.get("source") or ""),
        relationship=str(row.get("relationship") or "isa"),
        rules=tuple(rules),
        original_count=int(row.get("original_count") or 0),
        optimized_count=int(row.get("optimized_count") or len(rules)),
        reduction=float(row.get("reduction") or 0.0),
        strategy=str(row.get("strategy") or "greedy_hierarchy"),
    )


def _provenance(row: Any) -> Provenance | None:
    if not isinstance(row, Mapping):
        return None
    steps = [
        _provenance_step(step)
        for step in row.get("steps", [])
        if isinstance(step, Mapping)
    ]
    return Provenance.from_steps(str(row.get("strategy") or "remote_api"), steps)


def _provenance_step(row: Mapping[str, Any]) -> ProvenanceStep:
    known_keys = {
        "op",
        "source",
        "code",
        "target_source",
        "target_code",
        "cui",
        "aui",
        "tty",
        "depth",
        "mode",
        "name",
        "metadata",
    }
    metadata = dict(row.get("metadata") or {})
    metadata.update({key: value for key, value in row.items() if key not in known_keys})
    return ProvenanceStep(
        op=str(row["op"]),
        source=row.get("source"),
        code=row.get("code"),
        target_source=row.get("target_source"),
        target_code=row.get("target_code"),
        cui=row.get("cui"),
        aui=row.get("aui"),
        tty=row.get("tty"),
        depth=int(row["depth"]) if row.get("depth") is not None else None,
        mode=row.get("mode"),
        name=row.get("name"),
        metadata=metadata,
    )
