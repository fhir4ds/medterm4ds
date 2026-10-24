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
            con.execute(
                f"""
                CREATE TABLE {staging} AS
                SELECT
                    CAST(ancestor_id AS BIGINT) AS ancestor_id,
                    CAST(descendant_id AS BIGINT) AS descendant_id,
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
