"""Synthetic DuckDB UMLS fixture used by notebook and install smokes."""

from __future__ import annotations

from pathlib import Path


def create_synthetic_umls_db(path: str | Path) -> Path:
    """Create a tiny DuckDB database with enough UMLS shape for examples."""
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError("DuckDB is required to create the synthetic UMLS fixture.") from exc

    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    con = duckdb.connect(str(db_path))
    try:
        con.execute(
            """
            CREATE TABLE mrconso (
                CODE VARCHAR,
                TTY VARCHAR,
                STR VARCHAR,
                AUI VARCHAR,
                SUPPRESS VARCHAR,
                SAB VARCHAR,
                CUI VARCHAR
            )
            """
        )
        con.execute(
            """
            CREATE TABLE mrrel (
                AUI1 VARCHAR,
                AUI2 VARCHAR,
                RELA VARCHAR,
                REL VARCHAR
            )
            """
        )
        con.execute(
            """
            CREATE TABLE mrsat (
                CODE VARCHAR,
                SAB VARCHAR,
                ATN VARCHAR,
                ATV VARCHAR
            )
            """
        )
        con.executemany(
            "INSERT INTO mrconso VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("OLD", "PT", "Old diabetes code", "A_OLD", "O", "ICD10CM", "C_OLD"),
                ("NEW", "PT", "New diabetes code", "A_NEW", "N", "ICD10CM", "C_NEW"),
                ("E11", "PT", "Type 2 diabetes mellitus", "A_E11", "N", "ICD10CM", "C_E11"),
                ("E11.9", "PT", "Type 2 diabetes mellitus", "A_E119", "N", "ICD10CM", "C_DIAB"),
                (
                    "E11.40",
                    "PT",
                    "Type 2 diabetes with neuropathy",
                    "A_E1140",
                    "N",
                    "ICD10CM",
                    "C_E1140",
                ),
                (
                    "E11.41",
                    "PT",
                    "Type 2 diabetes with mononeuropathy",
                    "A_E1141",
                    "N",
                    "ICD10CM",
                    "C_E1141",
                ),
                (
                    "E11.42",
                    "PT",
                    "Type 2 diabetes with polyneuropathy",
                    "A_E1142",
                    "N",
                    "ICD10CM",
                    "C_E1142",
                ),
                (
                    "E11.43",
                    "PT",
                    "Type 2 diabetes with autonomic neuropathy",
                    "A_E1143",
                    "N",
                    "ICD10CM",
                    "C_E1143",
                ),
                (
                    "E11.44",
                    "PT",
                    "Type 2 diabetes with amyotrophy",
                    "A_E1144",
                    "N",
                    "ICD10CM",
                    "C_E1144",
                ),
                (
                    "E11.49",
                    "PT",
                    "Type 2 diabetes with other neuropathy",
                    "A_E1149",
                    "N",
                    "ICD10CM",
                    "C_E1149",
                ),
                (
                    "44054006",
                    "PT",
                    "Diabetes mellitus type 2",
                    "A_SNOMED_DIAB",
                    "N",
                    "SNOMEDCT_US",
                    "C_DIAB",
                ),
                ("D_DIAB", "MH", "Diabetes", "A_MEDLINE_DIAB", "N", "MEDLINEPLUS", "C_DIAB"),
                ("12345", "SCD", "Insulin 100 UNT/ML Injection", "A_RX", "N", "RXNORM", "C_RX"),
                ("208", "PT", "COVID-19 vaccine", "A_CVX_208", "N", "CVX", "C_CVX"),
                (
                    "4548-4",
                    "LN",
                    "Hemoglobin A1c/Hemoglobin.total in Blood",
                    "A_LNC_A1C",
                    "N",
                    "LNC",
                    "C_A1C",
                ),
            ],
        )
        con.executemany(
            "INSERT INTO mrrel VALUES (?, ?, ?, ?)",
            [
                ("A_NEW", "A_OLD", "replaced_by", "RO"),
                ("A_E1140", "A_E11", "isa", "PAR"),
                ("A_E1141", "A_E11", "isa", "PAR"),
                ("A_E1142", "A_E11", "isa", "PAR"),
                ("A_E1143", "A_E11", "isa", "PAR"),
                ("A_E1144", "A_E11", "isa", "PAR"),
                ("A_E1149", "A_E11", "isa", "PAR"),
            ],
        )
        con.execute("INSERT INTO mrsat VALUES ('12345', 'RXNORM', 'NDC', '00002082101')")
    finally:
        con.close()
    return db_path


__all__ = ["create_synthetic_umls_db"]
