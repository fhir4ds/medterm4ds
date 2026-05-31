"""Code inventory services for DuckDB-backed terminology stores."""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from medterm4ds.core.models import CodeRef
from medterm4ds.core.normalize import normalize_source

DEFAULT_INVENTORY_SOURCES = (
    "ICD10CM",
    "ICD10PCS",
    "HCPCS",
    "SNOMEDCT_US",
    "RXNORM",
    "LNC",
    "CVX",
    "CPT",
)


def normalize_sources(sources: Sequence[str] | str | None) -> tuple[str, ...]:
    """Normalize a source list or comma-separated source string."""
    if sources is None:
        return DEFAULT_INVENTORY_SOURCES
    if isinstance(sources, str):
        raw_sources = [part.strip() for part in sources.split(",")]
    else:
        raw_sources = [str(source).strip() for source in sources]
    normalized = [normalize_source(source) for source in raw_sources if source]
    return tuple(dict.fromkeys(normalized))


def count_source_codes(con, sources: Sequence[str] | str | None = None) -> dict[str, int]:
    """Count distinct active codes by source in a DuckDB UMLS schema."""
    normalized_sources = normalize_sources(sources)
    if not normalized_sources:
        return {}

    placeholders = ",".join(["?"] * len(normalized_sources))
    rows = con.execute(
        f"""
        SELECT SAB, COUNT(DISTINCT CODE)
        FROM mrconso
        WHERE SUPPRESS = 'N'
          AND CODE IS NOT NULL
          AND CODE != ''
          AND SAB IN ({placeholders})
        GROUP BY SAB
        ORDER BY SAB
        """,
        list(normalized_sources),
    ).fetchall()
    return {source: int(count) for source, count in rows}


def iter_source_codes(
    con,
    sources: Sequence[str] | str | None = None,
    *,
    fetch_size: int = 10_000,
    limit: int | None = None,
    resume_after: CodeRef | None = None,
) -> Iterator[CodeRef]:
    """Yield distinct active source codes from a DuckDB UMLS schema.

    Sources are streamed one at a time with stable code ordering. `limit`
    applies to the total number of yielded codes, not per source. `resume_after`
    skips every code through the provided source/code in the same source order.
    """
    if fetch_size < 1:
        raise ValueError("fetch_size must be at least 1")

    normalized_sources = normalize_sources(sources)
    resume_source = resume_after.source if resume_after else None
    if resume_source and resume_source not in normalized_sources:
        raise ValueError(f"Resume source {resume_source!r} is not in the requested sources.")

    yielded = 0
    skipping_sources = bool(resume_source)
    for source in normalized_sources:
        if skipping_sources and source != resume_source:
            continue
        if skipping_sources and source == resume_source:
            skipping_sources = False

        remaining = None if limit is None else limit - yielded
        if remaining is not None and remaining <= 0:
            return

        sql = """
            SELECT CODE
            FROM mrconso
            WHERE SUPPRESS = 'N'
              AND CODE IS NOT NULL
              AND CODE != ''
              AND SAB = ?
        """
        params: list[object] = [source]
        if resume_after and source == resume_after.source:
            sql += " AND CODE > ?"
            params.append(resume_after.code)
        sql += """
            GROUP BY CODE
            ORDER BY CODE
        """
        if remaining is not None:
            sql += " LIMIT ?"
            params.append(remaining)

        cursor = con.execute(sql, params)
        while True:
            rows = cursor.fetchmany(fetch_size)
            if not rows:
                break
            for (code,) in rows:
                yield CodeRef(source=source, code=code)
                yielded += 1
