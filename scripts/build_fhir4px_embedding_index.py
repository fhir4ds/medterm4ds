#!/usr/bin/env python3
"""Step 2: Build embedding index JSONL files (5 categories) from UMLS.

Reads patient_friendly_names.csv (from step 1) and produces:
  reports/fhir4px/embedding_index_{condition,lab,medication,procedure,vaccine}.jsonl

Each record carries 4 vector texts (technical, synonyms, friendly, hierarchy)
plus metadata: category, tty, semantic_types, atc (meds), component (labs),
icd10_code (conditions), ingredient_codes (medications).

Usage:
  PYTHONPATH=src python3 scripts/build_fhir4px_embedding_index.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import duckdb

DEFAULT_DB = "/mnt/d/medterm4ds/data/umls_current.duckdb"
DEFAULT_INPUT = Path("reports/fhir4px/patient_friendly_names.csv")
DEFAULT_OUTPUT_DIR = Path("reports/fhir4px")

# ── SNOMED TUI sets ─────────────────────────────────────────────────────────
_CONDITION_TUIS = ("T019","T020","T037","T046","T047","T048","T049","T190","T191")
_LAB_TUIS = ("T034","T059")
_PROCEDURE_TUIS = ("T058","T060","T061","T062","T063")
_MEDICATION_TUIS = ("T121","T123","T200")
_BODY_TUIS = ("T023","T024","T025","T026","T029","T030","T031")
_ALL_SNOMED_TUIS = _CONDITION_TUIS + _LAB_TUIS + _PROCEDURE_TUIS + _MEDICATION_TUIS + _BODY_TUIS

# ── Synonym source priority ────────────────────────────────────────────────
_SYNONYM_PRIORITY = {
    "LNC_COMPONENT": -1, "RXNORM_ING": -1,
    "MSH": 0, "MEDLINEPLUS": 1, "CHV": 2, "SNOMEDCT_US": 3,
    "ICD10CM": 4, "RXNORM": 5, "LNC": 6, "CPT": 7, "HCPCS": 7,
    "CVX": 7, "MTH": 8, "ATC": 9,
}
_SYNONYM_K = 8

# ── LOINC CLASS readable names ──────────────────────────────────────────────
_LNC_CLASS_READABLE: dict[str, str] = {
    "MICRO":"Microbiology","CHEM":"Chemistry","DRUG/TOX":"Drug and Toxicology",
    "RAD":"Radiology","ALLERGY":"Allergy","CHAL":"Challenge Tests",
    "DOC.ONTOLOGY":"Document Ontology","PHENX":"PHENX Surveys","SERO":"Serology",
    "LABORDERS.ONTOLOGY":"Lab Order Ontology","SURVEY.PROMIS":"PROMIS Survey",
    "HEM/BC":"Hematology","ABXBACT":"Antibacterial Susceptibility",
    "CELLMARK":"Cell Marker","SURVEY.GNHLTH":"General Health Survey",
    "MOLPATH.MUT":"Molecular Pathology Mutations","BLDBK":"Blood Bank",
    "COAG":"Coagulation","SURVEY.CMS":"CMS Survey","H&P.HX":"History and Physical",
    "PULM":"Pulmonary","PANEL.SURVEY.CMS":"CMS Survey Panel","SURVEY.MDS":"MDS Survey",
    "CARD.US":"Cardiac Ultrasound","OB.US":"Obstetric Ultrasound","PATH":"Pathology",
    "PANEL.PHENX":"PHENX Panel","MOLPATH":"Molecular Pathology","UA":"Urinalysis",
    "HLA":"HLA Typing","BD":"Blood Gas","BP":"Blood Pressure",
    "BDYWGT.ATOM":"Body Weight","BDYTMP.ATOM":"Body Temperature",
    "HRTRATE.ATOM":"Heart Rate","RESP.ATOM":"Respiratory Rate",
    "BDYOBS.ATOM":"Body Observation","PAIN.ATOM":"Pain","VISION.ATOM":"Vision",
}

_ICD10PCS_SECTIONS = (
    "Medical and Surgical","Medical","Surgical","Imaging","Mental Health",
    "Radiation Therapy","Nuclear Medicine","Physical Rehabilitation and Diagnostic Audiology",
    "Chiropractic","Administration of Medicine","Measurement",
    "Extracorporeal Assistance and Performance","Osteopathic","Other Procedures",
)


def _codes_sql(csv_path: str) -> str:
    """Select addressable codes from patient_friendly_names.csv with filters."""
    return f"""
        WITH pf AS (
            SELECT CAST(source AS VARCHAR) AS source, CAST(code AS VARCHAR) AS code,
                   CAST(name AS VARCHAR) AS name, CAST(technical_name AS VARCHAR) AS technical_name,
                   CAST(source_tty AS VARCHAR) AS source_tty,
                   CAST(cui AS VARCHAR) AS cui, CAST(aui AS VARCHAR) AS aui
            FROM read_csv_auto('{csv_path}', HEADER=true)
        ),
        target AS (
            SELECT pf.* FROM pf WHERE
                pf.source = 'ICD10CM'
                OR (pf.source = 'ICD10PCS' AND NOT EXISTS (
                    SELECT 1 FROM mrrel r2
                    JOIN mrconso ch ON ch.AUI = r2.AUI1 AND ch.SAB = 'ICD10PCS' AND ch.SUPPRESS = 'N'
                    WHERE r2.AUI2 = pf.aui AND r2.REL IN ('PAR','RB')
                ))
                OR (pf.source = 'SNOMEDCT_US' AND (
                    EXISTS (SELECT 1 FROM mrsty m WHERE m.cui = pf.cui AND m.tui IN ({','.join(f"'{t}'" for t in _ALL_SNOMED_TUIS)}))
                    OR EXISTS (SELECT 1 FROM mrconso cvx WHERE cvx.CUI = pf.cui AND cvx.SAB = 'CVX' AND cvx.SUPPRESS = 'N')
                ))
                OR (pf.source = 'LNC' AND pf.source_tty = 'LN')
                OR (pf.source = 'RXNORM' AND pf.source_tty IN ('IN','MIN','SCDG','SCD','SBD','BN','PIN','SCDC','SBDC','SBDF','BPCK','GPCK'))
                OR pf.source IN ('CPT','HCPCS','CVX')
        )
        SELECT t.*,
            CASE
                WHEN t.source = 'ICD10CM' THEN 'condition'
                WHEN t.source = 'LNC' THEN 'lab'
                WHEN t.source = 'RXNORM' THEN 'medication'
                WHEN t.source = 'CVX' THEN 'vaccine'
                WHEN t.source IN ('ICD10PCS','CPT','HCPCS') THEN 'procedure'
                WHEN t.source = 'SNOMEDCT_US' THEN
                    CASE
                        WHEN EXISTS (SELECT 1 FROM mrsty m WHERE m.cui = t.cui AND m.tui IN ({','.join(f"'{t}'" for t in _CONDITION_TUIS)})) THEN 'condition'
                        WHEN EXISTS (SELECT 1 FROM mrsty m WHERE m.cui = t.cui AND m.tui IN ({','.join(f"'{t}'" for t in _LAB_TUIS)})) THEN 'lab'
                        WHEN EXISTS (SELECT 1 FROM mrsty m WHERE m.cui = t.cui AND m.tui IN ({','.join(f"'{t}'" for t in _PROCEDURE_TUIS)})) THEN 'procedure'
                        WHEN EXISTS (SELECT 1 FROM mrsty m WHERE m.cui = t.cui AND m.tui IN ({','.join(f"'{t}'" for t in _MEDICATION_TUIS)})) THEN 'medication'
                        WHEN EXISTS (SELECT 1 FROM mrconso cvx WHERE cvx.CUI = t.cui AND cvx.SAB = 'CVX' AND cvx.SUPPRESS = 'N') THEN 'vaccine'
                        WHEN EXISTS (SELECT 1 FROM mrsty m WHERE m.cui = t.cui AND m.tui IN ({','.join(f"'{t}'" for t in _BODY_TUIS)})) THEN 'body_structure'
                    END
            END AS category
        FROM target t
    """


def _hierarchy_sql(csv_path: str, source: str) -> str:
    """Recursive PAR/RB walk for ICD10CM, ICD10PCS, or SNOMEDCT_US."""
    return f"""
        WITH RECURSIVE target AS (
            SELECT code, aui FROM ({_codes_sql(csv_path)}) AS t WHERE source = '{source}'
        ),
        walk AS (
            SELECT code, aui, 0 AS depth FROM target
            UNION ALL
            SELECT w.code, parent.AUI, w.depth + 1
            FROM walk w
            JOIN mrrel r ON r.AUI1 = w.aui AND r.REL IN ('PAR','RB')
            JOIN mrconso parent ON parent.AUI = r.AUI2 AND parent.SAB = '{source}' AND parent.SUPPRESS = 'N'
            WHERE w.depth < 2
        ),
        ranked AS (
            SELECT w.code, w.depth, m.CODE AS anc_code, m.STR,
                   ROW_NUMBER() OVER (PARTITION BY w.code, w.depth ORDER BY m.AUI) AS rn
            FROM walk w JOIN mrconso m ON m.AUI = w.aui AND m.SAB = '{source}'
            WHERE w.depth > 0
        )
        SELECT code, depth, anc_code, STR FROM ranked WHERE rn = 1
    """


def _clean_icd10pcs_template(text: str | None) -> tuple[list[str], str | None]:
    if not text or "@" not in text:
        return [], None
    nodes = [n.strip() for n in text.split("@")]
    segs = [n for n in nodes if n and n != "None"]
    root = segs[0] if segs and segs[0] in _ICD10PCS_SECTIONS else None
    return segs, root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_arg = str(input_path)
    db_path = Path(args.db)

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        t0 = time.perf_counter()
        print("[1/6] Loading target codes...")
        targets = con.execute(_codes_sql(csv_arg)).fetchall()
        by_key: dict[tuple[str,str], dict] = {}
        for source, code, name, tech, tty, cui, aui, category in targets:
            by_key[(source, code)] = {"source": source, "code": code, "name": name,
                "technical_name": tech, "source_tty": tty, "cui": cui, "aui": aui,
                "category": category}
        for k, n in sorted(Counter(r[7] for r in targets if r[7]).items()):
            print(f"  {k}: {n:,}")
        print(f"  Total: {len(targets):,}")

        print("[2/6] Loading synonyms (English, K=8)...")
        syn_rows = con.execute(f"""
            WITH target AS (
                SELECT source, code, cui, source_tty FROM ({_codes_sql(csv_arg)}) AS t
                WHERE cui IS NOT NULL AND cui != ''
            ),
            cui_syn AS (
                SELECT DISTINCT t.source, t.code, m.STR AS syn, m.SAB AS sab, m.TTY AS tty
                FROM target t JOIN mrconso m ON m.CUI = t.cui
                WHERE m.SUPPRESS='N' AND m.lat='ENG' AND m.STR IS NOT NULL AND m.STR != ''
            ),
            lnc_comp AS (
                SELECT DISTINCT t.source, t.code, s.ATV AS syn, 'LNC_COMPONENT' AS sab, 'COMP' AS tty
                FROM target t JOIN mrsat s ON s.SAB='LNC' AND s.CODE=t.code AND s.ATN='LOINC_COMPONENT'
                WHERE t.source='LNC' AND s.ATV IS NOT NULL
            ),
            combo_ing AS (
                SELECT DISTINCT 'RXNORM' AS source, t.code, ing.STR AS syn,
                       'RXNORM_ING' AS sab, 'IN' AS tty
                FROM target t
                JOIN mrconso prod ON prod.CODE = t.code AND prod.SAB = 'RXNORM' AND prod.SUPPRESS = 'N'
                JOIN mrrel r ON r.AUI2 = prod.AUI AND r.RELA IN ('has_ingredient','has_part')
                JOIN mrconso ing ON ing.AUI = r.AUI1 AND ing.SAB = 'RXNORM'
                               AND ing.SUPPRESS = 'N' AND ing.TTY = 'IN'
                WHERE t.source = 'RXNORM' AND t.source_tty IN ('MIN','SCD','SBD','SCDG','SCDC','SBDC','SBDF','BPCK','GPCK')
            ),
            all_syn AS (
                SELECT * FROM cui_syn UNION ALL SELECT * FROM lnc_comp UNION ALL SELECT * FROM combo_ing
            )
            SELECT source, code, syn, sab, tty FROM all_syn
        """).fetchall()
        syn_rows.sort(key=lambda r: (r[0], r[1], _SYNONYM_PRIORITY.get(r[3] or "", 99), r[4] or "", r[2]))
        syn_by_key: dict[tuple[str,str], list[str]] = defaultdict(list)
        syn_seen: dict[tuple[str,str], set[str]] = defaultdict(set)
        for source, code, syn, _sab, _tty in syn_rows:
            key = (source, code)
            tech = by_key.get(key, {}).get("technical_name")
            norm = syn.lower().strip()
            if not norm or norm in syn_seen[key]:
                continue
            if tech and norm == tech.lower():
                continue
            syn_seen[key].add(norm)
            if len(syn_by_key[key]) < _SYNONYM_K:
                syn_by_key[key].append(syn)
        print(f"  {len(syn_by_key):,} codes with synonyms")

        print("[3/6] Loading hierarchies...")
        hier: dict[str, dict[str, list]] = {s: defaultdict(list) for s in ("ICD10CM","ICD10PCS","SNOMEDCT_US")}
        for src in hier:
            for code, depth, anc_code, name in con.execute(_hierarchy_sql(csv_arg, src)).fetchall():
                hier[src][code].append({"depth": depth, "code": anc_code, "name": name})
            print(f"  {src}: {len(hier[src]):,} codes")

        # LNC CLASS
        lnc_class: dict[str, str] = {}
        for code, cls in con.execute(f"""
            WITH target AS (SELECT code FROM ({_codes_sql(csv_arg)}) AS t WHERE source='LNC')
            SELECT t.code, s.ATV FROM target t
            LEFT JOIN mrsat s ON s.SAB='LNC' AND s.CODE=t.code AND s.ATN='LCL'
        """).fetchall():
            if cls:
                lnc_class[code] = cls
        print(f"  LNC CLASS: {len(lnc_class):,}")

        # Pre-load all ATC code → name mappings in one query (levels 1-5)
        print("  Loading ATC code names...")
        atc_names: dict[str, str] = {}
        for code, name in con.execute("""
            SELECT CODE, MIN(STR) FROM mrconso
            WHERE SAB='ATC' AND SUPPRESS='N' AND CODE IS NOT NULL AND CODE != ''
            GROUP BY CODE
        """).fetchall():
            atc_names[code] = name

        # RXNORM ATC — all 5 levels with code+name per level. Uses multi-hop
        # ingredient traversal so SCD/SBD products resolve ATC via ingredients.
        atc_by_code: dict[str, dict] = {}
        for row in con.execute(f"""
            WITH target AS (SELECT code, cui, source_tty FROM ({_codes_sql(csv_arg)}) AS t WHERE source='RXNORM'),
            atc_direct AS (
                SELECT DISTINCT t.code, a.CODE AS atc_code, a.STR AS atc_name
                FROM target t JOIN mrconso a ON a.CUI=t.cui AND a.SAB='ATC' AND a.SUPPRESS='N' AND length(a.CODE)=7
            ),
            atc_via_ing AS (
                SELECT DISTINCT t.code, a.CODE AS atc_code, a.STR AS atc_name
                FROM target t
                JOIN mrconso prod ON prod.CODE=t.code AND prod.SAB='RXNORM' AND prod.SUPPRESS='N'
                JOIN mrrel r ON r.AUI2=prod.AUI AND r.RELA IN ('has_ingredient','has_part')
                JOIN mrconso ing ON ing.AUI=r.AUI1 AND ing.SAB='RXNORM' AND ing.SUPPRESS='N' AND ing.TTY='IN'
                JOIN mrconso a ON a.CUI=ing.CUI AND a.SAB='ATC' AND a.SUPPRESS='N' AND length(a.CODE)=7
                WHERE t.source_tty IN ('SCD','SBD','SCDG','SCDC','MIN','SBDC','SBDF')
            ),
            atc_scd_via_scdc AS (
                SELECT DISTINCT t.code, a.CODE AS atc_code, a.STR AS atc_name
                FROM target t
                JOIN mrconso prod ON prod.CODE=t.code AND prod.SAB='RXNORM' AND prod.SUPPRESS='N'
                JOIN mrrel r1 ON r1.AUI2=prod.AUI AND r1.RELA='consists_of'
                JOIN mrconso scdc ON scdc.AUI=r1.AUI1 AND scdc.SAB='RXNORM' AND scdc.SUPPRESS='N' AND scdc.TTY='SCDC'
                JOIN mrrel r2 ON r2.AUI2=scdc.AUI AND r2.RELA='has_ingredient'
                JOIN mrconso ing ON ing.AUI=r2.AUI1 AND ing.SAB='RXNORM' AND ing.SUPPRESS='N' AND ing.TTY='IN'
                JOIN mrconso a ON a.CUI=ing.CUI AND a.SAB='ATC' AND a.SUPPRESS='N' AND length(a.CODE)=7
                WHERE t.source_tty='SCD'
            ),
            atc_sbd_via_scd AS (
                SELECT DISTINCT t.code, a.CODE AS atc_code, a.STR AS atc_name
                FROM target t
                JOIN mrconso prod ON prod.CODE=t.code AND prod.SAB='RXNORM' AND prod.SUPPRESS='N'
                JOIN mrrel r1 ON r1.AUI1=prod.AUI AND r1.RELA='has_tradename'
                JOIN mrconso scd ON scd.AUI=r1.AUI2 AND scd.SAB='RXNORM' AND scd.SUPPRESS='N' AND scd.TTY='SCD'
                JOIN mrrel r2 ON r2.AUI2=scd.AUI AND r2.RELA='consists_of'
                JOIN mrconso scdc ON scdc.AUI=r2.AUI1 AND scdc.SAB='RXNORM' AND scdc.SUPPRESS='N' AND scdc.TTY='SCDC'
                JOIN mrrel r3 ON r3.AUI2=scdc.AUI AND r3.RELA='has_ingredient'
                JOIN mrconso ing ON ing.AUI=r3.AUI1 AND ing.SAB='RXNORM' AND ing.SUPPRESS='N' AND ing.TTY='IN'
                JOIN mrconso a ON a.CUI=ing.CUI AND a.SAB='ATC' AND a.SUPPRESS='N' AND length(a.CODE)=7
                WHERE t.source_tty='SBD'
            ),
            all_atc AS (
                SELECT * FROM atc_direct
                UNION SELECT * FROM atc_via_ing
                UNION SELECT * FROM atc_scd_via_scdc
                UNION SELECT * FROM atc_sbd_via_scd
            )
            SELECT code, atc_code, atc_name,
                   ROW_NUMBER() OVER (PARTITION BY code ORDER BY atc_code) AS rn FROM all_atc
        """).fetchall():
            if row[3] == 1 and row[0] not in atc_by_code:
                ac = row[1]
                l1, l2, l3, l4, l5 = ac[:1], ac[:3], ac[:4], ac[:5], ac
                atc_by_code[row[0]] = {
                    "atc_code": ac,
                    "atc_name": row[2],
                    "atc_level1": {"code": l1, "name": atc_names.get(l1)},
                    "atc_level2": {"code": l2, "name": atc_names.get(l2)},
                    "atc_level3": {"code": l3, "name": atc_names.get(l3)},
                    "atc_level4": {"code": l4, "name": atc_names.get(l4)},
                    "atc_level5": {"code": l5, "name": atc_names.get(l5)},
                }

        print(f"  RXNORM ATC: {len(atc_by_code):,}")

        print("[4/6] Loading semantic types...")
        sem_by_key: dict[tuple[str,str], list[str]] = {}
        for source, code, tuis in con.execute(f"""
            WITH target AS (SELECT source, code, cui FROM ({_codes_sql(csv_arg)}) AS t WHERE cui IS NOT NULL)
            SELECT t.source, t.code, string_agg(DISTINCT m.tui, ',' ORDER BY m.tui)
            FROM target t JOIN mrsty m ON m.cui = t.cui GROUP BY t.source, t.code
        """).fetchall():
            sem_by_key[(source, code)] = [t for t in tuis.split(",") if t]
        print(f"  {len(sem_by_key):,} codes")

        print("[5/6] Loading icd10_code (conditions) and ingredient_codes (medications)...")
        icd10_by_key: dict[tuple[str,str], str | None] = {}
        for source, code, icd10 in con.execute(f"""
            WITH target AS (
                SELECT source, code, cui FROM ({_codes_sql(csv_arg)}) AS t
                WHERE source IN ('ICD10CM','SNOMEDCT_US')
            )
            SELECT t.source, t.code,
                   CASE WHEN t.source = 'ICD10CM' THEN t.code
                        ELSE (SELECT MIN(i.CODE) FROM mrconso i
                              WHERE i.CUI = t.cui AND i.SAB = 'ICD10CM' AND i.SUPPRESS = 'N'
                              AND i.CODE IS NOT NULL AND i.CODE != '')
                   END AS icd10
            FROM target t
        """).fetchall():
            icd10_by_key[(source, code)] = icd10 if icd10 else None
        print(f"  icd10_code: {sum(1 for v in icd10_by_key.values() if v):,} non-null / {len(icd10_by_key):,} total")

        ing_by_key: dict[tuple[str,str], list[str]] = {}
        for _source, code, ings in con.execute(f"""
            WITH target AS (
                SELECT source, code, source_tty FROM ({_codes_sql(csv_arg)}) AS t WHERE source='RXNORM'
            ),
            -- Direct: has_ingredient or has_part (covers SCDC, SCDG, MIN)
            direct AS (
                SELECT t.code, ing.CODE AS ing_code
                FROM target t
                JOIN mrconso prod ON prod.CODE = t.code AND prod.SAB='RXNORM' AND prod.SUPPRESS='N'
                JOIN mrrel r ON r.AUI2 = prod.AUI AND r.RELA IN ('has_ingredient','has_part')
                JOIN mrconso ing ON ing.AUI = r.AUI1 AND ing.SAB='RXNORM' AND ing.SUPPRESS='N' AND ing.TTY='IN'
                WHERE t.source_tty IN ('SCD','SBD','SCDG','SCDC','MIN','SBDC','SBDF')
            ),
            -- SCD via SCDC: SCD consists_of SCDC → has_ingredient IN
            scd_via_scdc AS (
                SELECT t.code, ing.CODE AS ing_code
                FROM target t
                JOIN mrconso prod ON prod.CODE = t.code AND prod.SAB='RXNORM' AND prod.SUPPRESS='N'
                JOIN mrrel r1 ON r1.AUI2 = prod.AUI AND r1.RELA = 'consists_of'
                JOIN mrconso scdc ON scdc.AUI = r1.AUI1 AND scdc.SAB='RXNORM' AND scdc.SUPPRESS='N' AND scdc.TTY='SCDC'
                JOIN mrrel r2 ON r2.AUI2 = scdc.AUI AND r2.RELA = 'has_ingredient'
                JOIN mrconso ing ON ing.AUI = r2.AUI1 AND ing.SAB='RXNORM' AND ing.SUPPRESS='N' AND ing.TTY='IN'
                WHERE t.source_tty = 'SCD'
            ),
            -- SBD via SCD: SBD has_tradename SCD → consists_of SCDC → has_ingredient IN
            sbd_via_scd AS (
                SELECT t.code, ing.CODE AS ing_code
                FROM target t
                JOIN mrconso prod ON prod.CODE = t.code AND prod.SAB='RXNORM' AND prod.SUPPRESS='N'
                JOIN mrrel r1 ON r1.AUI1 = prod.AUI AND r1.RELA = 'has_tradename'
                JOIN mrconso scd ON scd.AUI = r1.AUI2 AND scd.SAB='RXNORM' AND scd.SUPPRESS='N' AND scd.TTY='SCD'
                JOIN mrrel r2 ON r2.AUI2 = scd.AUI AND r2.RELA = 'consists_of'
                JOIN mrconso scdc ON scdc.AUI = r2.AUI1 AND scdc.SAB='RXNORM' AND scdc.SUPPRESS='N' AND scdc.TTY='SCDC'
                JOIN mrrel r3 ON r3.AUI2 = scdc.AUI AND r3.RELA = 'has_ingredient'
                JOIN mrconso ing ON ing.AUI = r3.AUI1 AND ing.SAB='RXNORM' AND ing.SUPPRESS='N' AND ing.TTY='IN'
                WHERE t.source_tty = 'SBD'
            ),
            all_pairs AS (
                SELECT * FROM direct
                UNION SELECT * FROM scd_via_scdc
                UNION SELECT * FROM sbd_via_scd
            )
            SELECT 'RXNORM' AS source, code, string_agg(DISTINCT ing_code, ',' ORDER BY ing_code) AS ings
            FROM all_pairs WHERE ing_code IS NOT NULL GROUP BY source, code
        """).fetchall():
            ing_by_key[("RXNORM", code)] = [c for c in ings.split(",") if c]
        print(f"  ingredient_codes: {len(ing_by_key):,} products with ingredients")

        print("[6/6] Building records and writing JSONL...")
        category_files: dict[str, object] = {
            cat: (output_dir / f"embedding_index_{cat}.jsonl").open("w", encoding="utf-8")
            for cat in ("condition","lab","medication","procedure","vaccine","body_structure")
        }
        counts = dict.fromkeys(category_files, 0)

        for key, t in by_key.items():
            if not t["category"]:
                continue
            source = t["source"]
            code = t["code"]
            cat = t["category"]
            tech = t["technical_name"] or t["name"]
            syns = syn_by_key.get(key, [])
            friendly = t["name"]
            sem = sem_by_key.get(key, [])

            # Build hierarchy
            h: list[str] = []
            if source in ("ICD10CM", "ICD10PCS"):
                levels = sorted(hier[source].get(code, []), key=lambda x: x["depth"])
                for lv in reversed(levels):
                    if source == "ICD10PCS" and "@" in lv["name"]:
                        segs, _ = _clean_icd10pcs_template(lv["name"])
                        h.append(" - ".join(segs) if segs else f"{lv['name']} ({lv['code']})")
                    else:
                        h.append(f"{lv['name']} ({lv['code']})")
            elif source == "SNOMEDCT_US":
                levels = sorted(hier[source].get(code, []), key=lambda x: x["depth"])
                h = [lv["name"] for lv in reversed(levels)]
            elif source == "LNC":
                cls = lnc_class.get(code)
                if cls:
                    readable = _LNC_CLASS_READABLE.get(cls, cls)
                    h = [f"{readable} ({cls})" if readable != cls else cls]
            elif source == "RXNORM":
                atc = atc_by_code.get(code)
                if atc:
                    l2 = atc["atc_level2"]
                    l4 = atc["atc_level4"]
                    l5 = atc["atc_level5"]
                    h = [f"{l2['code']} — {l2['name'] or ''}",
                         f"{l4['code']} — {l4['name'] or ''}",
                         f"{l5['code']} — {l5['name'] or ''}"]

            # Priority synonyms (LOINC CLASS readable, ICD10PCS root section)
            priority: list[str] = []
            if source == "LNC":
                cls = lnc_class.get(code)
                if cls:
                    readable = _LNC_CLASS_READABLE.get(cls, cls)
                    if readable != cls:
                        priority.append(readable)
            if source == "ICD10PCS" and h:
                root_seg = h[0].split(" - ")[0].strip()
                if root_seg in _ICD10PCS_SECTIONS:
                    priority.append(root_seg)

            # Clean ICD10PCS @ synonyms
            if source == "ICD10PCS":
                cleaned = []
                for s in syns:
                    if "@" in s:
                        segs, _ = _clean_icd10pcs_template(s)
                        if segs:
                            c = " ".join(segs)
                            if c.lower() not in {x.lower() for x in cleaned} and c.lower() != (tech or "").lower():
                                cleaned.append(c)
                    else:
                        cleaned.append(s)
                syns = cleaned

            # Dedupe + prepend priority
            existing = {s.lower() for s in syns}
            existing.add((tech or "").lower())
            extra = [s for s in priority if s.lower() not in existing]
            syns = (extra + syns)[:_SYNONYM_K]

            # Component for LNC
            component = tech.split(":")[0].strip() if (source == "LNC" and tech and ":" in tech) else None

            record = {
                "category": cat,
                "tty": t["source_tty"],
                "friendly_name": friendly,
                "code": {"source": source, "code": code, "tty": t["source_tty"],
                         "cui": t["cui"], "name": tech},
                "rule": "addressable",
                "semantic_types": sem,
                "atc": atc_by_code.get(code) if source == "RXNORM" else None,
                "component": component,
                "icd10_code": icd10_by_key.get(key) if cat in ("condition","body_structure") else None,
                "ingredient_codes": ing_by_key.get(key, [code] if source == "RXNORM" and t["source_tty"] == "IN" else []) if source == "RXNORM" else None,
                "vectors": {"technical": tech, "synonyms": syns, "friendly": friendly, "hierarchy": h},
            }
            line = json.dumps(record, ensure_ascii=False) + "\n"
            if cat in category_files:
                category_files[cat].write(line)
                counts[cat] += 1

        # Standalone ATC entries at every level (L1–L5). Each ATC code becomes
        # its own medication-category record so BM25 can match class names
        # directly ("biguanides", "antineoplastic agents", etc.). ATC is also
        # kept as an attribute on RXNORM entries — these standalone entries
        # are additive.
        print("  Adding standalone ATC entries...")
        atc_atoms = con.execute("""
            SELECT CODE, MIN(STR) AS name, MIN(CUI) AS cui
            FROM mrconso
            WHERE SAB = 'ATC' AND SUPPRESS = 'N'
              AND CODE IS NOT NULL AND CODE != ''
            GROUP BY CODE
            ORDER BY CODE
        """).fetchall()
        atc_levels = {1: "L1", 3: "L2", 4: "L3", 5: "L4", 7: "L5"}
        for atc_code, atc_name, atc_cui in atc_atoms:
            code_len = len(atc_code)
            level_label = atc_levels.get(code_len)
            if not level_label:
                continue
            # Build hierarchy from parent levels (shorter prefixes)
            atc_hier: list[str] = []
            for plen in (7, 5, 4, 3, 1):
                if plen >= code_len:
                    continue
                pcode = atc_code[:plen]
                pname = atc_names.get(pcode, "")
                atc_hier.append(f"{pcode} — {pname}")
            # Synonyms: the ATC code itself + alternate names from other TTYs
            atc_syns = [atc_code]
            alt_names = con.execute(
                "SELECT DISTINCT STR FROM mrconso WHERE SAB='ATC' AND CODE=? AND SUPPRESS='N' AND STR != ?",
                [atc_code, atc_name],
            ).fetchall()
            for (alt,) in alt_names:
                if alt and alt.lower() not in {s.lower() for s in atc_syns} and len(atc_syns) < _SYNONYM_K:
                    atc_syns.append(alt)

            atc_record = {
                "category": "medication",
                "tty": level_label,
                "friendly_name": atc_name or atc_code,
                "code": {"source": "ATC", "code": atc_code, "tty": level_label,
                         "cui": atc_cui, "name": atc_name},
                "rule": "atc_standalone",
                "semantic_types": [],
                "atc": {
                    "atc_code": atc_code,
                    "atc_name": atc_name,
                    "atc_level1": {"code": atc_code[:1], "name": atc_names.get(atc_code[:1])},
                    "atc_level2": {"code": atc_code[:3], "name": atc_names.get(atc_code[:3])} if code_len >= 3 else None,
                    "atc_level3": {"code": atc_code[:4], "name": atc_names.get(atc_code[:4])} if code_len >= 4 else None,
                    "atc_level4": {"code": atc_code[:5], "name": atc_names.get(atc_code[:5])} if code_len >= 5 else None,
                    "atc_level5": {"code": atc_code[:7], "name": atc_names.get(atc_code[:7])} if code_len >= 7 else None,
                },
                "component": None,
                "icd10_code": None,
                "ingredient_codes": None,
                "vectors": {
                    "technical": atc_name or atc_code,
                    "synonyms": atc_syns,
                    "friendly": atc_name or atc_code,
                    "hierarchy": atc_hier,
                },
            }
            category_files["medication"].write(
                json.dumps(atc_record, ensure_ascii=False) + "\n"
            )
            counts["medication"] += 1

        for cat, fh in category_files.items():
            fh.close()
            p = output_dir / f"embedding_index_{cat}.jsonl"
            size_mb = p.stat().st_size / (1024 * 1024)
            print(f"  embedding_index_{cat}.jsonl: {counts[cat]:,} records ({size_mb:.1f} MB)")
    finally:
        con.close()

    elapsed = time.perf_counter() - t0
    total = sum(counts.values())
    print(f"\nTotal: {total:,} records in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
