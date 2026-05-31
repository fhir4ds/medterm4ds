"""External evidence tool placeholders."""

from __future__ import annotations

from typing import Any


def external_evidence_unavailable(tool: str, **query: Any) -> dict[str, Any]:
    """Return a structured response for tools requiring external evidence data."""
    return {
        "query": tool,
        "status": "not_available",
        "reason": (
            "This tool requires an external evidence data adapter. "
            "The current medterm4ds core layer is limited to UMLS DuckDB/API terminology data."
        ),
        "parameters": query,
    }


def indication_search(indication: str, **kwargs: Any) -> dict[str, Any]:
    return external_evidence_unavailable("indication_search", indication=indication, **kwargs)


def fda_label_by_rxcui(rxcui: str, **kwargs: Any) -> dict[str, Any]:
    return external_evidence_unavailable("fda_label_by_rxcui", rxcui=rxcui, **kwargs)


def guideline_search(query: str, **kwargs: Any) -> dict[str, Any]:
    return external_evidence_unavailable("guideline_search", search=query, **kwargs)


def guideline_recommendations(topic: str, **kwargs: Any) -> dict[str, Any]:
    return external_evidence_unavailable("guideline_recommendations", topic=topic, **kwargs)


def guideline_fulltext(guideline_id: str, **kwargs: Any) -> dict[str, Any]:
    return external_evidence_unavailable("guideline_fulltext", guideline_id=guideline_id, **kwargs)


def guidelines_for_code(code: str, source: str, **kwargs: Any) -> dict[str, Any]:
    return external_evidence_unavailable(
        "guidelines_for_code",
        code=code,
        source=source,
        **kwargs,
    )
