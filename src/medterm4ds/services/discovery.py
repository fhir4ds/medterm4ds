"""Terminology inventory and name discovery services."""

from __future__ import annotations

from collections.abc import Sequence

from medterm4ds.core.display import join_limited
from medterm4ds.core.models import CodeInfo, CodeRef, NameSearchResult, SourceStats
from medterm4ds.engines.base import DiscoveryEngine
from medterm4ds.services.inventory import normalize_sources

# Upper bound for per_source/limit parameters (QC-217): unbounded integers
# reached the SQL layer and crashed with raw duckdb internals
# ('MIN/MAX: n value must be < 1000000', 'INT128 ... out of range for INT64').
MAX_DISCOVERY_LIMIT = 100_000
# Minimum search query length (QC-218/QC-235): 1-character queries matched
# most of the 15.5M active atoms, costing 38-46s per call and (for wildcard
# metacharacters) exhausting temp storage under concurrency.
MIN_SEARCH_QUERY_CHARS = 2


def _validate_source_presence(
    requested: Sequence[str], known: set[str]
) -> None:
    """Raise ValueError when a requested source has no codes in the database.

    QC-221: unknown/typo/URI-form sources previously returned empty success
    on every discovery surface — indistinguishable from a real query with no
    matches. Every source in the production DB has active codes, so absence
    from the result set means the source does not exist.
    """
    missing = [source for source in requested if source not in known]
    if missing:
        # QC-396 (LOW): cap the enumeration — a 10K-entry --sources filter
        # previously produced a single 109KB one-line error. First 10 + count.
        raise ValueError(
            "source(s) not found in this database: "
            + join_limited(missing, repr_values=True)
        )


def get_source_stats(
    engine: DiscoveryEngine,
    *,
    sources: Sequence[str] | str | None = None,
) -> list[SourceStats]:
    """Return code and atom counts by source."""
    normalized = normalize_sources(sources) if sources is not None else None
    if normalized is not None and not normalized:
        # QC-395 (LOW): an explicitly-supplied filter whose entries are all
        # empty (e.g. MCP source_stats(sources=['']) or CLI
        # ``sources --sources ''``) previously WIDENED scope — the caller
        # asked for one (empty) source and received stats for every source
        # in the database. Reject instead; ``sources=None`` still means all.
        raise ValueError(
            "sources must contain at least one non-empty vocabulary name "
            "(e.g. SNOMEDCT_US) when provided."
        )
    stats = engine.get_source_stats(normalized)
    if normalized:
        _validate_source_presence(normalized, {stat.source for stat in stats})
    return stats


def sample_source_codes(
    engine: DiscoveryEngine,
    *,
    sources: Sequence[str] | str | None = None,
    per_source: int = 10,
) -> list[CodeRef]:
    """Return sample active codes by source."""
    if per_source < 1:
        raise ValueError("per_source must be at least 1")
    if per_source > MAX_DISCOVERY_LIMIT:
        raise ValueError(
            f"per_source must be at most {MAX_DISCOVERY_LIMIT} (got {per_source})"
        )
    normalized = normalize_sources(sources)
    codes = engine.sample_source_codes(normalized, per_source=per_source)
    if normalized:
        _validate_source_presence(normalized, {code.source for code in codes})
    return codes


def get_code_ttys(
    codes: Sequence[CodeRef | tuple[str, str]],
    engine: DiscoveryEngine,
) -> list[CodeInfo]:
    """Return active atoms and TTYs for one or many codes.

    Note: an empty ``codes`` batch is accepted and returns [] — that is the
    documented client contract (QC-106: ``code_ttys_df([])`` returns the
    canonical empty DataFrame, not an error). The MCP wrapper additionally
    rejects empty batches at its boundary; harmonizing the two is QC-232,
    deferred as a design decision.
    """
    normalized = []
    for item in codes:
        ref = item if isinstance(item, CodeRef) else CodeRef.from_pair(item)
        # QC-222: empty string is never a valid code (min_length=1 promoted
        # rule) — previously echoed back as silent empty success.
        if not str(ref.code).strip():
            raise ValueError("code must be a non-empty string")
        # QC-227: URI/OID-form sources are wrong-surface inputs — the CLI
        # rejects them (QC-011); the service layer must too, not silently
        # return [] after uppercasing the URI.
        if "://" in ref.source or ref.source.lower().startswith("urn:oid:"):
            raise ValueError(
                f"source expects a UMLS SAB string (e.g. SNOMEDCT_US), got "
                f"{ref.source!r} (looks like a URI/OID)"
            )
        normalized.append(ref)
    return engine.get_code_ttys(normalized)


def search_names(
    query: str,
    engine: DiscoveryEngine,
    *,
    sources: Sequence[str] | str | None = None,
    tty_filters: Sequence[str] | str | None = None,
    limit: int = 25,
) -> list[NameSearchResult]:
    """Search active terminology names."""
    stripped = query.strip()
    if not stripped:
        raise ValueError("query must not be empty")
    if len(stripped) < MIN_SEARCH_QUERY_CHARS:
        raise ValueError(
            f"query must be at least {MIN_SEARCH_QUERY_CHARS} characters"
        )
    # Cap query length to prevent CPU-waste attacks via huge LIKE patterns.
    # 256 chars is generous for any realistic terminology search.
    if len(query) > 256:
        raise ValueError(f"query must be at most 256 characters (got {len(query)})")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if limit > MAX_DISCOVERY_LIMIT:
        raise ValueError(f"limit must be at most {MAX_DISCOVERY_LIMIT} (got {limit})")
    normalized_sources = normalize_sources(sources) if sources is not None else None
    if normalized_sources is not None and not normalized_sources:
        # QC-416/425 (MEDIUM): an explicitly-supplied filter whose entries
        # are all empty (e.g. search_names(sources=['']) or CLI
        # ``search-names --sources ''``) previously WIDENED to all sources —
        # the caller asked for one (empty) source and got unfiltered
        # multi-source results. Reject instead, matching get_source_stats'
        # QC-395 guard; ``sources=None`` still means all.
        raise ValueError(
            "sources must contain at least one non-empty vocabulary name "
            "(e.g. SNOMEDCT_US) when provided."
        )
    # QC-424 (MEDIUM): unknown-source presence is validated inside the
    # engine against the exact table this search reads (see
    # _DiscoveryOps.search_names) — the service layer cannot know whether
    # the deployment carries mt4ds.atoms, best_atoms, or raw mrconso.
    normalized_ttys = _normalize_ttys(tty_filters)
    return engine.search_names(
        query,
        sources=normalized_sources,
        tty_filters=normalized_ttys,
        limit=limit,
    )


def _normalize_ttys(values: Sequence[str] | str | None) -> tuple[str, ...] | None:
    if values is None:
        return None
    if isinstance(values, str):
        raw_values = [part.strip() for part in values.split(",")]
    else:
        raw_values = [str(value).strip() for value in values]
    normalized = [value.upper() for value in raw_values if value]
    return tuple(dict.fromkeys(normalized))
