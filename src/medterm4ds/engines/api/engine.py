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
    FriendlyNameResult,
    NameSearchResult,
    Provenance,
    ProvenanceStep,
    SourceStats,
)
from medterm4ds.core.normalize import normalize_source

ApiTransport = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]


class RemoteApiEngine:
    """Terminology engine backed by a medterm4ds FastAPI process."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        headers: Mapping[str, str] | None = None,
        transport: ApiTransport | None = None,
    ):
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
    ) -> list[CodeRelation]:
        payload = {
            "codes": [_code_payload(code) for code in codes],
            "direction": direction,
            "max_depth": max_depth,
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


def _open_json(request: Request, timeout: float) -> Mapping[str, Any]:
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Remote API request failed with HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Remote API request failed: {exc.reason}") from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError("Remote API response was not a JSON object.")
    return payload


def _code_payload(code: CodeRef) -> dict[str, str]:
    return {"source": code.source, "code": code.code}


def _code_ref(row: Mapping[str, Any], *, source_key: str = "source", code_key: str = "code") -> CodeRef:
    return CodeRef(source=str(row[source_key]), code=str(row[code_key]))


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
        name=str(row["name"]),
        cui=row.get("cui"),
        aui=row.get("aui"),
        tty=row.get("tty"),
        match_type=str(row.get("match_type") or "contains"),
    )


def _code_mapping(row: Mapping[str, Any]) -> CodeMapping:
    return CodeMapping(
        source=_code_ref(row),
        target=_code_ref(row, source_key="target_source", code_key="target_code"),
        relationship=str(row["relationship"]),
        match_type=str(row["match_type"]),
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
        relationship=str(row["relationship"]),
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
    return FriendlyNameResult(
        code=_code_ref(row),
        name=str(row["name"]),
        friendly_source=str(row["friendly_source"]),
        match_type=str(row["match_type"]),
        match_depth=int(row.get("match_depth") or 0),
        technical_name=row.get("technical_name"),
        matched_via=_provenance(row.get("matched_via")),
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
