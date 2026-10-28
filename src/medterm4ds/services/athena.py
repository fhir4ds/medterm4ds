"""OHDSI Athena vocabulary ingestion and concept bridge.

Implements the Athena vocabulary support design note
(docs/plans/athena-vocabulary-support-design.md, approved 2026-10-23):

- :func:`ingest_athena` — load CONCEPT / CONCEPT_ANCESTOR /
  CONCEPT_RELATIONSHIP / VOCABULARY CSVs from an Athena download
  directory into ``athena_*`` tables in an existing UMLS DuckDB
  (same-DB namespaced storage; staging-swap idempotence; provenance
  block in ``athena_meta``).
- :func:`build_concept_map` — the fhir4ds seam: convert the CONCEPT
  table into a FHIR ConceptMap resource (FHIR system+code → OMOP
  concept_id groups per vocabulary).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# vocabulary_id -> FHIR system URI correspondence. Seeded from the
# omoponfhir OmopCodeableConceptMapping enum (the real-consumer seam
# precedent) with medterm4ds canonical-systems spellings as
# tiebreaker; see the design note §3. Vocabs WITHOUT a FHIR system
# (SPL, OSM, OMOP Genomic, Type Concept, ...) are excluded unless
# include_internal.
ATHENA_VOCAB_TO_FHIR: dict[str, str] = {
    "SNOMED": "http://snomed.info/sct",
    "RxNorm": "http://www.nlm.nih.gov/research/umls/rxnorm",
    "RxNorm Extension": "http://www.nlm.nih.gov/research/umls/rxnorm",
    "LOINC": "http://loinc.org",
    "ICD10CM": "http://hl7.org/fhir/sid/icd-10-cm",
    "ICD10PCS": "http://hl7.org/fhir/sid/icd-10-pcs",
    "CPT4": "http://www.ama-assn.org/go/cpt",
    "HCPCS": "urn:oid:2.16.840.1.113883.6.14",
    "ATC": "http://www.whocc.no/atc",
    "NDC": "http://hl7.org/fhir/sid/ndc",
}
# RxNorm Extension is a distinct OMOP vocabulary carrying its own
# concept_ids; it maps to the SAME FHIR system as RxNorm but with
# equivalence=relatedto (non-standard source codes).

# AJ1 fix (TS-88): vocabularies whose concept_code IS the native
# coding — code → own-concept_id is an IDENTITY map, not a
# crosswalk. The real download carries no standard_concept='S' rows
# for these (ICD10CM all-NULL; ATC C-hierarchy + NULL leaves), so
# the S-based rule mapped every such row to relatedto and VD
# consumers filtering equivalence=equal silently dropped the entire
# vocabulary. Identity rows are equal regardless of the S flag.
NATIVE_IDENTITY_VOCABS = frozenset({
    "ICD10CM", "ICD10PCS", "ATC", "HCPCS", "NDC", "ICD10", "ICD9CM",
})

DEFAULT_VOCABULARIES = (
    "SNOMED", "RxNorm", "RxNorm Extension", "LOINC",
    "ICD10CM", "ICD10PCS", "CPT4", "HCPCS", "ATC",
)

ATHENA_TABLES = ("concept", "concept_ancestor",
                 "concept_relationship", "vocabulary")

LICENSE_FAMILIES: dict[str, str] = {
    "SNOMED": (
        "SNOMED CT — Apache-2.0 distribution in Athena; USE governed "
        "by the SNOMED Affiliates license (member/non-member terms)"
    ),
    "RxNorm": "NLM RxNorm — permissive with attribution (UMLS license family)",
    "RxNorm Extension": "NLM RxNorm — permissive with attribution (UMLS license family)",
    "LOINC": "Regenstrief LOINC — free use with attribution (see loinc.org/terms)",
    "ICD10CM": "NLM ICD-10-CM — public domain (US)",
    "ICD10PCS": "NLM ICD-10-PCS — public domain (US)",
    "CPT4": "AMA CPT — requires UMLS/CPT agreement EVEN via Athena",
    "HCPCS": "CMS HCPCS — free use (US government work)",
    "ATC": "WHO ATC — free for research/attribution use",
    "NDC": "FDA NDC — public domain",
}


@dataclass(frozen=True)
class AthenaIngestResult:
    row_counts: dict[str, int]
    vocabularies: tuple[str, ...]
    accepted_licenses: bool

    @property
    def total_rows(self) -> int:
        return sum(self.row_counts.values())


def ingest_athena(
    con,
    athena_dir: str | Path,
    *,
    vocabularies: tuple[str, ...] = DEFAULT_VOCABULARIES,
    accept_licenses: bool = False,
    licenses_accepted_at: str | None = None,
) -> AthenaIngestResult:
    """Load Athena CSVs into ``athena_*`` tables (staging swap).

    ``accept_licenses`` must be True (the CLI gates this on
    ``--accept-licenses``; the licensing notice table prints first).
    Raises ``PermissionError`` otherwise — fail loud, never an
    implicit acceptance.
    """
    if not accept_licenses:
        raise PermissionError(
            "Athena ingestion requires --accept-licenses (see the "
            "licensing notice in the design note §5 / the CLI output)."
        )
    athena_dir = Path(athena_dir)
    csv_map = _locate_csvs(athena_dir)
    vocab_filter = set(vocabularies)

    counts: dict[str, int] = {}
    for table in ATHENA_TABLES:
        staging = f"athena_{table}_staging"
        target = f"athena_{table}"
        con.execute(f"DROP TABLE IF EXISTS {staging}")
        src = csv_map[table]
        if table == "concept":
            con.execute(
                f"""
                CREATE TABLE {staging} AS
                SELECT
                    CAST(concept_id AS BIGINT) AS concept_id,
                    concept_name,
                    domain_id,
                    vocabulary_id,
                    concept_class_id,
                    standard_concept,
                    concept_code,
                    CAST(strptime(valid_start_date, '%Y%m%d') AS DATE)
                        AS valid_start_date,
                    CAST(strptime(valid_end_date, '%Y%m%d') AS DATE)
                        AS valid_end_date,
                    NULLIF(invalid_reason, '') AS invalid_reason,
                FROM read_csv(
                    '{src}', header=true, delim='\t',
                    quote='', all_varchar=true)
                WHERE vocabulary_id IN (
                    {', '.join(f"'{v}'" for v in sorted(vocab_filter))})
                """
            )
        elif table == "concept_ancestor":
            # AR1 fix (TS-87): the REAL Athena download ships
            # ancestor_concept_id / descendant_concept_id (the
            # official OMOP CDM v5 names); the original loader
            # guessed ancestor_id/descendant_id and could not ingest
            # a real download at all (the mini fixture shared the
            # wrong assumption). Detect the header shape and select
            # the actual names; athena_* output columns keep the
            # CDM-official names either way.
            with open(src, encoding="utf-8") as fh:
                header = fh.readline().strip().split("\t")
            anc_col = (
                "ancestor_concept_id"
                if "ancestor_concept_id" in header
                else "ancestor_id"
            )
            desc_col = (
                "descendant_concept_id"
                if "descendant_concept_id" in header
                else "descendant_id"
            )
            con.execute(
                f"""
                CREATE TABLE {staging} AS
                SELECT
                    CAST({anc_col} AS BIGINT)
                        AS ancestor_concept_id,
                    CAST({desc_col} AS BIGINT)
                        AS descendant_concept_id,
                    CAST(min_levels_of_separation AS INTEGER)
                        AS min_levels_of_separation,
                    CAST(max_levels_of_separation AS INTEGER)
                        AS max_levels_of_separation
                FROM read_csv(
                    '{src}', header=true, delim='\t',
                    quote='', all_varchar=true)
                """
            )
        elif table == "concept_relationship":
            con.execute(
                f"""
                CREATE TABLE {staging} AS
                SELECT
                    CAST(concept_id_1 AS BIGINT) AS concept_id_1,
                    CAST(concept_id_2 AS BIGINT) AS concept_id_2,
                    relationship_id,
                    CAST(strptime(valid_start_date, '%Y%m%d') AS DATE)
                        AS valid_start_date,
                    CAST(strptime(valid_end_date, '%Y%m%d') AS DATE)
                        AS valid_end_date,
                    NULLIF(invalid_reason, '') AS invalid_reason
                FROM read_csv(
                    '{src}', header=true, delim='\t',
                    quote='', all_varchar=true)
                """
            )
        else:  # vocabulary
            con.execute(
                f"""
                CREATE TABLE {staging} AS
                SELECT
                    vocabulary_id,
                    vocabulary_name,
                    vocabulary_reference,
                    vocabulary_version,
                    CAST(vocabulary_concept_id AS BIGINT)
                        AS vocabulary_concept_id
                FROM read_csv(
                    '{src}', header=true, delim='\t',
                    quote='', all_varchar=true)
                """
            )
        con.execute(f"DROP TABLE IF EXISTS {target}")
        con.execute(f"ALTER TABLE {staging} RENAME TO {target}")
        counts[table] = con.execute(
            f"SELECT COUNT(*) FROM {target}"
        ).fetchone()[0]

    # AG1 fix (TS-84): the vocabulary filter previously applied only
    # to CONCEPT — ancestry and relationship tables loaded in FULL,
    # leaving orphan rows referencing absent concept_ids (millions
    # on real downloads: bloat + silent join-integrity gaps). Semi-
    # join both endpoints against the loaded concept set; the counts
    # below reflect the FILTERED tables.
    for edge_sql in (
        """
        DELETE FROM athena_concept_ancestor
        WHERE ancestor_concept_id NOT IN
              (SELECT concept_id FROM athena_concept)
           OR descendant_concept_id NOT IN
              (SELECT concept_id FROM athena_concept)
        """,
        """
        DELETE FROM athena_concept_relationship
        WHERE concept_id_1 NOT IN
              (SELECT concept_id FROM athena_concept)
           OR concept_id_2 NOT IN
              (SELECT concept_id FROM athena_concept)
        """,
    ):
        con.execute(edge_sql)
    counts["concept_ancestor"] = con.execute(
        "SELECT COUNT(*) FROM athena_concept_ancestor"
    ).fetchone()[0]
    counts["concept_relationship"] = con.execute(
        "SELECT COUNT(*) FROM athena_concept_relationship"
    ).fetchone()[0]

    versions = con.execute(
        "SELECT vocabulary_id, vocabulary_version FROM athena_vocabulary"
    ).fetchall()
    con.execute("DROP TABLE IF EXISTS athena_meta")
    con.execute(
        """
        CREATE TABLE athena_meta AS
        SELECT ? AS loader_version,
               ? AS source_dir,
               ? AS loaded_at,
               ? AS accepted_licenses_at,
               ? AS vocabularies,
               ? AS vocabulary_versions_json
        """,
        [
            "1",
            str(athena_dir),
            datetime.now(timezone.utc).isoformat(),
            licenses_accepted_at,
            ",".join(sorted(vocab_filter)),
            json.dumps(
                dict(versions), ensure_ascii=False
            ),
        ],
    )
    loaded_vocabs = tuple(
        r[0]
        for r in con.execute(
            "SELECT DISTINCT vocabulary_id FROM athena_concept "
            "ORDER BY vocabulary_id"
        ).fetchall()
    )
    return AthenaIngestResult(
        row_counts=counts,
        vocabularies=loaded_vocabs,
        accepted_licenses=True,
    )


def _locate_csvs(athena_dir: Path) -> dict[str, Path]:
    """Map table names to their CSVs (exact names first, then a
    single-dir glob for versioned download layouts)."""
    out: dict[str, Path] = {}
    for table in ATHENA_TABLES:
        exact = athena_dir / f"{table.upper()}.csv"
        if exact.exists():
            out[table] = exact
            continue
        candidates = sorted(athena_dir.glob(f"**/{table.upper()}.csv"))
        if not candidates:
            raise FileNotFoundError(
                f"{table.upper()}.csv not found under {athena_dir}"
            )
        out[table] = candidates[0]
    return out


CONCEPT_MAP_URL = "urn:medterm4ds:athena-conceptmap"
CONCEPT_ID_SYSTEM = "urn:athena:concept-id"


def build_concept_map(
    con,
    *,
    vocabularies: tuple[str, ...] = DEFAULT_VOCABULARIES,
    include_internal: bool = False,
) -> dict[str, Any]:
    """Convert the ingested CONCEPT table into a FHIR ConceptMap
    resource (the fhir4ds OMOP VD join source; design note §3)."""
    vocab_set = set(vocabularies)
    groups: list[dict[str, Any]] = []
    vocab_versions = dict(
        con.execute(
            "SELECT vocabulary_id, vocabulary_version FROM athena_vocabulary"
        ).fetchall()
    )
    ordered = con.execute(
        "SELECT vocabulary_id, concept_id, concept_code, concept_name, "
        "concept_class_id, domain_id, standard_concept, invalid_reason "
        "FROM athena_concept ORDER BY vocabulary_id, concept_code"
    ).fetchall()
    by_vocab: dict[str, list[tuple]] = {}
    for row in ordered:
        by_vocab.setdefault(row[0], []).append(row)

    for vocab in sorted(by_vocab):
        if vocab not in vocab_set and not include_internal:
            continue
        source_uri = ATHENA_VOCAB_TO_FHIR.get(vocab)
        if source_uri is None:
            if not include_internal:
                continue
            source_uri = f"urn:athena:{vocab.lower().replace(' ', '-')}"
        elements: list[dict[str, Any]] = []
        for (
            _v, concept_id, code, name, klass, domain,
            standard, invalid,
        ) in by_vocab[vocab]:
            if invalid and standard == "S":
                equivalence = "unmatched"
            elif vocab in NATIVE_IDENTITY_VOCABS:
                equivalence = "equal"
            elif standard == "S" and vocab != "RxNorm Extension":
                equivalence = "equal"
            else:
                equivalence = "relatedto"
            comment = (
                f"vocabulary_id={vocab}; concept_class={klass}; "
                f"domain={domain}; standard_concept={standard or 'N'}"
            )
            if invalid:
                comment += f"; invalid_reason={invalid}"
            target: dict[str, Any] = {
                "code": str(concept_id),
                "equivalence": equivalence,
                "comment": comment,
            }
            elements.append({
                "code": code,
                "display": name,
                "target": [target],
            })
        if not elements:
            continue
        group: dict[str, Any] = {
            "source": source_uri,
            "target": CONCEPT_ID_SYSTEM,
            "element": elements,
        }
        version = vocab_versions.get(vocab)
        if version:
            group["extension"] = [{
                "url": "urn:medterm4ds:athena:vocabulary-version",
                "valueString": version,
            }]
        groups.append(group)

    return {
        "resourceType": "ConceptMap",
        "url": CONCEPT_MAP_URL,
        "version": max(
            (v for v in vocab_versions.values() if v), default="unknown"
        ),
        "status": "active",
        "name": "AthenaConceptMap",
        "title": "OHDSI Athena CONCEPT to OMOP concept_id",
        "experimental": False,
        "date": datetime.now(timezone.utc)
        .date()
        .isoformat(),
        "group": groups,
    }


def licensing_notice(
    vocabularies: tuple[str, ...] = DEFAULT_VOCABULARIES,
) -> str:
    lines = ["Athena vocabulary licensing notice:"]
    for v in vocabularies:
        family = LICENSE_FAMILIES.get(
            v, "UNKNOWN — verify before loading"
        )
        lines.append(f"  {v}: {family}")
    lines.append(
        "Loading requires --accept-licenses; the acceptance is "
        "recorded in athena_meta."
    )
    return "\n".join(lines)

def iter_thin_concept_map_rows(
    con,
    *,
    vocabularies: tuple[str, ...] = DEFAULT_VOCABULARIES,
    include_internal: bool = False,
):
    """Yield element-per-resource ConceptMap rows (NDJSON form).

    The fhir4ds OMOP VD join contract (coordination note
    2026-10-23): one THIN ConceptMap resource per mapping row —
    group.source = the FHIR system URI, exactly one element with
    exactly one target — so a SQL join can bind scalars
    (code, system URI) in an ON clause. Nested elements inside a
    monolithic resource are unreachable by that join; this form is
    the reference shape their curated supplements already use.
    """
    vocab_set = set(vocabularies)
    vocab_versions = dict(
        con.execute(
            "SELECT vocabulary_id, vocabulary_version "
            "FROM athena_vocabulary"
        ).fetchall()
    )
    rows = con.execute(
        "SELECT vocabulary_id, concept_id, concept_code, concept_name, "
        "concept_class_id, domain_id, standard_concept, invalid_reason "
        "FROM athena_concept ORDER BY vocabulary_id, concept_code"
    ).fetchall()
    for (
        vocab, concept_id, code, name, klass, domain,
        standard, invalid,
    ) in rows:
        if vocab not in vocab_set and not include_internal:
            continue
        source_uri = ATHENA_VOCAB_TO_FHIR.get(vocab)
        if source_uri is None:
            if not include_internal:
                continue
            source_uri = f"urn:athena:{vocab.lower().replace(' ', '-')}"
        if invalid and standard == "S":
            equivalence = "unmatched"
        elif vocab in NATIVE_IDENTITY_VOCABS:
            equivalence = "equal"
        elif standard == "S" and vocab != "RxNorm Extension":
            equivalence = "equal"
        else:
            equivalence = "relatedto"
        comment = (
            f"vocabulary_id={vocab}; concept_class={klass}; "
            f"domain={domain}; standard_concept={standard or 'N'}"
        )
        if invalid:
            comment += f"; invalid_reason={invalid}"
        version = vocab_versions.get(vocab)
        yield {
            "resourceType": "ConceptMap",
            "url": CONCEPT_MAP_URL,
            "version": version or "unknown",
            "status": "active",
            "group": [{
                "source": source_uri,
                "target": CONCEPT_ID_SYSTEM,
                "element": [{
                    "code": code,
                    "display": name,
                    "target": [{
                        "code": str(concept_id),
                        "equivalence": equivalence,
                        "comment": comment,
                    }],
                }],
            }],
        }


# -- Stage 2: query surfaces (design note §2) -------------------------------


def get_athena_ancestors(
    con, concept_id: int, *, max_depth: int | None = None
) -> list[dict[str, object]]:
    """Exact ancestry via the precomputed closure (design §2).

    Returns ancestor rows (concept_id, name, domain, vocabulary,
    min/max separation) ordered farthest-first. `max_depth` filters
    on min_levels_of_separation. The closure answers the VS02-01
    family exactly (TS-90/AN2): one indexed scan, no BFS caps.
    """
    _check_athena_schema(con)
    sql = """
        SELECT a.ancestor_concept_id, c.concept_name, c.domain_id,
               c.vocabulary_id,
               CAST(a.min_levels_of_separation AS INTEGER) AS min_d,
               CAST(a.max_levels_of_separation AS INTEGER) AS max_d
        FROM athena_concept_ancestor a
        JOIN athena_concept c
          ON c.concept_id = a.ancestor_concept_id
        WHERE a.descendant_concept_id = ?
    """
    params: list[object] = [concept_id]
    if max_depth is not None:
        sql += " AND CAST(a.min_levels_of_separation AS INTEGER) <= ?"
        params.append(max_depth)
    sql += " ORDER BY min_d DESC, a.ancestor_concept_id"
    return [
        {
            "concept_id": r[0], "name": r[1], "domain": r[2],
            "vocabulary": r[3], "min_levels": r[4], "max_levels": r[5],
        }
        for r in con.execute(sql, params).fetchall()
    ]


def get_athena_descendants(
    con, concept_id: int, *, max_depth: int | None = None,
    limit: int = 20,
) -> list[dict[str, object]]:
    """Exact descendants via the closure, nearest-first."""
    _check_athena_schema(con)
    sql = """
        SELECT a.descendant_concept_id, c.concept_name, c.domain_id,
               c.vocabulary_id,
               CAST(a.min_levels_of_separation AS INTEGER) AS min_d,
               CAST(a.max_levels_of_separation AS INTEGER) AS max_d
        FROM athena_concept_ancestor a
        JOIN athena_concept c
          ON c.concept_id = a.descendant_concept_id
        WHERE a.ancestor_concept_id = ?
    """
    params: list[object] = [concept_id]
    if max_depth is not None:
        sql += " AND CAST(a.min_levels_of_separation AS INTEGER) <= ?"
        params.append(max_depth)
    sql += " ORDER BY min_d ASC, a.descendant_concept_id LIMIT ?"
    params.append(limit)
    return [
        {
            "concept_id": r[0], "name": r[1], "domain": r[2],
            "vocabulary": r[3], "min_levels": r[4], "max_levels": r[5],
        }
        for r in con.execute(sql, params).fetchall()
    ]


def count_athena_ancestors(con, concept_id: int) -> int:
    """EXACT ancestor count — the VS02-01 family's answer (AN2)."""
    return con.execute(
        "SELECT COUNT(*) FROM athena_concept_ancestor "
        "WHERE descendant_concept_id = ?",
        [concept_id],
    ).fetchone()[0]


def athena_subsumes(
    con, ancestor_id: int, descendant_id: int
) -> str:
    """$subsumes-style outcome via the closure (design §2's flagged
    opt-in; exact — no caps).

    Returns 'equivalent' | 'subsumes' | 'subsumed-by' |
    'not-subsumed'.
    """
    if ancestor_id == descendant_id:
        return "equivalent"
    row = con.execute(
        "SELECT 1 FROM athena_concept_ancestor "
        "WHERE ancestor_concept_id = ? AND descendant_concept_id = ?",
        [ancestor_id, descendant_id],
    ).fetchone()
    if row:
        return "subsumes"
    row = con.execute(
        "SELECT 1 FROM athena_concept_ancestor "
        "WHERE ancestor_concept_id = ? AND descendant_concept_id = ?",
        [descendant_id, ancestor_id],
    ).fetchone()
    if row:
        return "subsumed-by"
    return "not-subsumed"


def search_athena_concepts(
    con,
    query: str,
    *,
    domain_id: str | None = None,
    vocabulary_id: str | None = None,
    include_deprecated: bool = False,
    limit: int = 20,
) -> list[dict[str, object]]:
    """Search athena_concept names with Athena-native filters
    (design §2): domain_id, vocabulary_id, invalid_reason."""
    if not str(query).strip():
        raise ValueError("query must be a non-empty string")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    # AS2 fix (TS-93): unknown vocabularies were silently dropped
    # (results [] indistinguishable from a legitimate empty search;
    # AF3's sibling gap). Validate against the ingested vocabulary
    # table when a filter is supplied.
    if vocabulary_id is not None:
        known = {
            r[0]
            for r in con.execute(
                "SELECT vocabulary_id FROM athena_vocabulary"
            ).fetchall()
        }
        if vocabulary_id not in known:
            raise ValueError(
                f"unknown vocabulary_id: {vocabulary_id!r} — this "
                f"database carries: {', '.join(sorted(known))}"
            )
    sql = """
        SELECT concept_id, concept_name, domain_id, vocabulary_id,
               concept_class_id, standard_concept, concept_code,
               invalid_reason
        FROM athena_concept
        WHERE concept_name ILIKE ?
    """
    params: list[object] = [f"%{query}%"]
    if domain_id is not None:
        sql += " AND domain_id = ?"
        params.append(domain_id)
    if vocabulary_id is not None:
        sql += " AND vocabulary_id = ?"
        params.append(vocabulary_id)
    if not include_deprecated:
        sql += " AND (invalid_reason IS NULL OR invalid_reason = '')"
    sql += " ORDER BY concept_id LIMIT ?"
    params.append(limit)
    return [
        {
            "concept_id": r[0], "name": r[1], "domain": r[2],
            "vocabulary": r[3], "concept_class": r[4],
            "standard": r[5], "code": r[6],
            "invalid_reason": r[7] or None,
        }
        for r in con.execute(sql, params).fetchall()
    ]


def athena_health(con) -> dict[str, object]:
    """Health block for the athena store (design §2): live-table
    facts, NOT configured values (the HH1 doctrine)."""
    if not _athena_tables_present(con):
        return {"present": False}
    vocabs = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT vocabulary_id FROM athena_concept "
            "ORDER BY vocabulary_id"
        ).fetchall()
    ]
    meta = con.execute(
        "SELECT loaded_at, vocabularies FROM athena_meta"
    ).fetchone()
    return {
        "present": True,
        "vocabularies": vocabs,
        "loaded_at": meta[0] if meta else None,
    }


def _athena_tables_present(con) -> bool:
    names = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    return {"athena_concept", "athena_concept_ancestor"} <= names


def _check_athena_schema(con) -> None:
    """AS1 fix (TS-93): DBs ingested before athena-batch1 carry the
    old ancestor column names; the closure queries join on the
    CDM-official names and die with a raw BinderException. Detect
    the old schema at the service boundary and raise the actionable
    re-ingest remedy — chosen over athena_health detection because
    the error should fire at the moment of use, where the remedy is
    actionable; health is advisory-only."""
    if not _athena_tables_present(con):
        raise RuntimeError(
            "no athena_* tables — run 'medterm4ds data "
            "ingest-athena' first."
        )
    cols = {
        r[0]
        for r in con.execute(
            "DESCRIBE athena_concept_ancestor"
        ).fetchall()
    }
    if "ancestor_concept_id" not in cols:
        raise RuntimeError(
            "athena tables predate athena-batch1 (old ancestor "
            "column schema) — re-run 'medterm4ds data ingest-athena' "
            "to migrate to the CDM-official columns."
        )
