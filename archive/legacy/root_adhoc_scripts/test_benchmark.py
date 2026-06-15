import csv, json, time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import duckdb
from medterm4ds import CodeRef
from medterm4ds.services.patient_friendly_materialized import materialize_patient_friendly_resolutions
benchmark=Path('/mnt/d/medterm4ds/data/patient_friendly_benchmark.csv')
if not benchmark.exists():
    # If the real one isn't there, create a dummy one
    benchmark.parent.mkdir(parents=True, exist_ok=True)
    with benchmark.open('w', encoding='utf-8') as f:
        f.write("source,code\n")
        # Write some random codes from best_atoms
        with duckdb.connect('data/umls_current.duckdb', read_only=True) as con:
            rows = con.execute("SELECT source, code FROM mt4ds.best_atoms LIMIT 250").fetchall()
            for r in rows:
                f.write(f"{r[0]},{r[1]}\n")

rows=[]
with benchmark.open(newline='', encoding='utf-8') as f:
    for row in csv.DictReader(f):
        rows.append(CodeRef(source=row['source'], code=row['code']))

with duckdb.connect('data/umls_current.duckdb', read_only=False) as con:
    start = time.perf_counter()
    report=materialize_patient_friendly_resolutions(
        rows[:250],
        con,
        policy_version='0.2',
        replace_existing=True,
        max_depth=5,
    )
    print(f"Elapsed for 250 rows: {time.perf_counter()-start:.2f}s")
    print(f"Resolutions: {report['resolutions']}, Candidates: {report['candidates']}")
