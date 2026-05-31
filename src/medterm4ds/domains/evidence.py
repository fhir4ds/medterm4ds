"""External evidence adapters for FDA labels and guideline literature."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from medterm4ds.core.models import CodeRef
from medterm4ds.services.lookup import get_code_infos

OPENFDA_LABEL_URL = "https://api.fda.gov/drug/label.json"
NCBI_EUTILS_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


@dataclass(frozen=True)
class HttpResponse:
    data: str
    status_code: int = 200

    def json(self) -> Mapping[str, Any]:
        payload = json.loads(self.data)
        if not isinstance(payload, Mapping):
            raise RuntimeError("External evidence response was not a JSON object.")
        return payload


class EvidenceHttpError(RuntimeError):
    """HTTP failure returned by an external evidence source."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class EvidenceHttpClient:
    """Small urllib-based HTTP client with injectable transport in tests."""

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        headers: Mapping[str, str] | None = None,
    ):
        self.timeout = timeout
        self.headers = {"user-agent": "medterm4ds/0.1"}
        self.headers.update(dict(headers or {}))

    def get(self, url: str, params: Mapping[str, Any]) -> HttpResponse:
        query = urlencode({key: value for key, value in params.items() if value is not None})
        request_url = f"{url}?{query}" if query else url
        request = Request(request_url, method="GET", headers=self.headers)
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return HttpResponse(
                    data=response.read().decode("utf-8"),
                    status_code=getattr(response, "status", 200),
                )
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise EvidenceHttpError(exc.code, detail) from exc
        except URLError as exc:
            raise RuntimeError(f"Evidence HTTP request failed: {exc.reason}") from exc


class OpenFDALabelClient:
    """openFDA drug label client."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        http: EvidenceHttpClient | None = None,
        label_url: str = OPENFDA_LABEL_URL,
    ):
        self.api_key = api_key or os.getenv("OPENFDA_API_KEY")
        self.http = http or EvidenceHttpClient()
        self.label_url = label_url

    def search_labels(self, search: str, *, limit: int = 10) -> list[dict[str, Any]]:
        try:
            response = self.http.get(
                self.label_url,
                {
                    "search": search,
                    "limit": max(1, min(limit, 100)),
                    "api_key": self.api_key,
                },
            ).json()
        except EvidenceHttpError as exc:
            if exc.status_code == 404:
                return []
            raise
        return [
            _fda_label_record(row)
            for row in response.get("results", [])
            if isinstance(row, Mapping)
        ]

    def labels_by_rxcui(self, rxcui: str, *, limit: int = 5) -> list[dict[str, Any]]:
        return self.search_labels(f'openfda.rxcui:"{_escape_openfda_value(rxcui)}"', limit=limit)

    def labels_for_indication(self, indication: str, *, limit: int = 10) -> list[dict[str, Any]]:
        return self.search_labels(f'indications_and_usage:"{_escape_openfda_value(indication)}"', limit=limit)


class PubMedGuidelineClient:
    """NCBI E-utilities client focused on PubMed guideline records."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        email: str | None = None,
        tool: str = "medterm4ds",
        http: EvidenceHttpClient | None = None,
        base_url: str = NCBI_EUTILS_URL,
    ):
        self.api_key = api_key or os.getenv("NCBI_API_KEY")
        self.email = email or os.getenv("NCBI_EMAIL")
        self.tool = tool
        self.http = http or EvidenceHttpClient()
        self.base_url = base_url.rstrip("/")

    def search_guidelines(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        ids = self._esearch(_guideline_query(query), limit=limit)
        if not ids:
            return []
        summaries = self._esummary(ids)
        return [_pubmed_summary_record(summary) for summary in summaries]

    def fetch_article(self, pmid: str) -> dict[str, Any]:
        response = self.http.get(
            f"{self.base_url}/efetch.fcgi",
            self._params(
                {
                    "db": "pubmed",
                    "id": pmid,
                    "retmode": "xml",
                }
            ),
        )
        return _parse_pubmed_article(response.data, pmid=pmid)

    def _esearch(self, term: str, *, limit: int) -> list[str]:
        response = self.http.get(
            f"{self.base_url}/esearch.fcgi",
            self._params(
                {
                    "db": "pubmed",
                    "term": term,
                    "retmode": "json",
                    "retmax": max(1, min(limit, 100)),
                    "sort": "relevance",
                }
            ),
        ).json()
        ids = response.get("esearchresult", {}).get("idlist", [])
        return [str(pmid) for pmid in ids]

    def _esummary(self, ids: list[str]) -> list[Mapping[str, Any]]:
        response = self.http.get(
            f"{self.base_url}/esummary.fcgi",
            self._params(
                {
                    "db": "pubmed",
                    "id": ",".join(ids),
                    "retmode": "json",
                }
            ),
        ).json()
        result = response.get("result", {})
        return [
            result[pmid]
            for pmid in ids
            if isinstance(result.get(pmid), Mapping)
        ]

    def _params(self, params: Mapping[str, Any]) -> dict[str, Any]:
        payload = {
            "tool": self.tool,
            "email": self.email,
            "api_key": self.api_key,
            **dict(params),
        }
        return {key: value for key, value in payload.items() if value}


def indication_search(
    indication: str,
    *,
    limit: int = 10,
    client: OpenFDALabelClient | None = None,
) -> dict[str, Any]:
    """Search openFDA drug labels by indications and usage text."""
    label_client = client or OpenFDALabelClient()
    try:
        labels = label_client.labels_for_indication(indication, limit=limit)
    except Exception as exc:
        return _external_error_response(
            "indication_search",
            "openFDA drug label",
            exc,
            indication=indication,
        )
    return {
        "query": "indication_search",
        "status": "ok",
        "indication": indication,
        "result_count": len(labels),
        "results": labels,
        "source": "openFDA drug label",
    }


def fda_label_by_rxcui(
    rxcui: str,
    *,
    limit: int = 5,
    client: OpenFDALabelClient | None = None,
) -> dict[str, Any]:
    """Search openFDA drug labels by RxCUI."""
    label_client = client or OpenFDALabelClient()
    try:
        labels = label_client.labels_by_rxcui(rxcui, limit=limit)
    except Exception as exc:
        return _external_error_response(
            "fda_label_by_rxcui",
            "openFDA drug label",
            exc,
            rxcui=str(rxcui),
        )
    return {
        "query": "fda_label_by_rxcui",
        "status": "ok",
        "rxcui": str(rxcui),
        "result_count": len(labels),
        "results": labels,
        "source": "openFDA drug label",
    }


def guideline_search(
    query: str,
    *,
    limit: int = 20,
    client: PubMedGuidelineClient | None = None,
) -> dict[str, Any]:
    """Search PubMed for guideline/practice-guideline records."""
    guideline_client = client or PubMedGuidelineClient()
    try:
        rows = guideline_client.search_guidelines(query, limit=limit)
    except Exception as exc:
        return _external_error_response(
            "guideline_search",
            "PubMed E-utilities",
            exc,
            search=query,
        )
    return {
        "query": "guideline_search",
        "status": "ok",
        "search": query,
        "result_count": len(rows),
        "results": rows,
        "source": "PubMed E-utilities",
    }


def guideline_recommendations(
    topic: str,
    *,
    limit: int = 10,
    client: PubMedGuidelineClient | None = None,
) -> dict[str, Any]:
    """Return guideline search context for a topic."""
    search = guideline_search(topic, limit=limit, client=client)
    return {
        "query": "guideline_recommendations",
        "status": search["status"],
        "topic": topic,
        "recommendation_source": "PubMed guideline abstracts and summaries",
        "result_count": search["result_count"],
        "results": search["results"],
        "error": search.get("error"),
    }


def guideline_fulltext(
    guideline_id: str,
    *,
    client: PubMedGuidelineClient | None = None,
) -> dict[str, Any]:
    """Fetch PubMed abstract/metadata for one guideline PMID."""
    guideline_client = client or PubMedGuidelineClient()
    try:
        article = guideline_client.fetch_article(str(guideline_id))
    except Exception as exc:
        return _external_error_response(
            "guideline_fulltext",
            "PubMed E-utilities",
            exc,
            guideline_id=str(guideline_id),
        )
    return {
        "query": "guideline_fulltext",
        "status": "ok",
        "guideline_id": str(guideline_id),
        "source": "PubMed E-utilities",
        "result": article,
    }


def guidelines_for_code(
    code: str,
    source: str,
    *,
    engine=None,
    limit: int = 10,
    client: PubMedGuidelineClient | None = None,
) -> dict[str, Any]:
    """Search PubMed guidelines using a code display name when an engine is provided."""
    ref = CodeRef(source=source, code=code)
    query = f"{ref.source} {ref.code}"
    display = None
    if engine is not None:
        info = get_code_infos([ref], engine=engine)[0]
        if info and info.name:
            display = info.name
            query = info.name
    search = guideline_search(query, limit=limit, client=client)
    return {
        "query": "guidelines_for_code",
        "status": search["status"],
        "code": ref.code,
        "source": ref.source,
        "display": display,
        "guideline_query": query,
        "result_count": search["result_count"],
        "results": search["results"],
        "error": search.get("error"),
    }


def external_evidence_unavailable(tool: str, **query: Any) -> dict[str, Any]:
    """Compatibility helper for callers that need an explicit unavailable response."""
    reason = query.pop("reason", None)
    return {
        "query": tool,
        "status": "not_available",
        "reason": reason or "External evidence source is not configured.",
        "results": [],
        **query,
    }


def _external_error_response(
    query: str,
    source: str,
    exc: Exception,
    **context: Any,
) -> dict[str, Any]:
    return {
        "query": query,
        "status": "error",
        "source": source,
        "error": f"{exc.__class__.__name__}: {exc}",
        "result_count": 0,
        "results": [],
        **context,
    }


def _fda_label_record(row: Mapping[str, Any]) -> dict[str, Any]:
    openfda = row.get("openfda") if isinstance(row.get("openfda"), Mapping) else {}
    return {
        "id": row.get("id"),
        "set_id": row.get("set_id"),
        "effective_time": row.get("effective_time"),
        "rxcui": _list_field(openfda, "rxcui"),
        "brand_name": _list_field(openfda, "brand_name"),
        "generic_name": _list_field(openfda, "generic_name"),
        "manufacturer_name": _list_field(openfda, "manufacturer_name"),
        "indications_and_usage": _list_field(row, "indications_and_usage"),
        "dosage_and_administration": _list_field(row, "dosage_and_administration"),
        "boxed_warning": _list_field(row, "boxed_warning"),
        "warnings": _list_field(row, "warnings"),
        "adverse_reactions": _list_field(row, "adverse_reactions"),
    }


def _pubmed_summary_record(row: Mapping[str, Any]) -> dict[str, Any]:
    authors = row.get("authors", [])
    return {
        "pmid": str(row.get("uid") or row.get("pmid") or ""),
        "title": row.get("title"),
        "journal": row.get("fulljournalname") or row.get("source"),
        "pubdate": row.get("pubdate"),
        "source": row.get("source"),
        "authors": [
            author.get("name")
            for author in authors
            if isinstance(author, Mapping) and author.get("name")
        ],
        "articleids": row.get("articleids", []),
    }


def _parse_pubmed_article(xml_text: str, *, pmid: str) -> dict[str, Any]:
    root = ElementTree.fromstring(xml_text)
    article = root.find(".//PubmedArticle")
    if article is None:
        return {"pmid": pmid, "title": None, "abstract": None}
    title = _text(article.find(".//ArticleTitle"))
    abstract_parts = [
        "".join(part.itertext()).strip()
        for part in article.findall(".//Abstract/AbstractText")
    ]
    journal = _text(article.find(".//Journal/Title"))
    pub_year = _text(article.find(".//PubDate/Year"))
    return {
        "pmid": pmid,
        "title": title,
        "journal": journal,
        "pub_year": pub_year,
        "abstract": "\n".join(part for part in abstract_parts if part) or None,
    }


def _text(element) -> str | None:
    if element is None:
        return None
    text = "".join(element.itertext()).strip()
    return text or None


def _list_field(row: Mapping[str, Any], key: str) -> list[str]:
    value = row.get(key)
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _escape_openfda_value(value: str) -> str:
    return str(value).replace('"', '\\"')


def _guideline_query(query: str) -> str:
    return f"({query}) AND (guideline[Publication Type] OR practice guideline[Publication Type])"
