import csv, json, time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import duckdb
from medterm4ds import CodeRef
from medterm4ds.services.patient_friendly_materialized import materialize_patient_friendly_resolutions

benchmark = Path('/mnt/d/medterm/data/patient_friendly_benchmark.csv')
output = Path('reports/reviews/materialize_patient_friendly_benchmark_rows_2026-06-05.json')
chunk_size = 250
policy_version = '0.2'
max_depth = 5
rows = []
seen = set()
with benchmark.open(newline='', encoding='utf-8') as f:
    for row in csv.DictReader(f):
        key = (row['source'], row['code'])
        if key not in seen:
            seen.add(key)
            rows.append(CodeRef(source=key[0], code=key[1]))

by_source = defaultdict(list)
for code in rows:
    by_source[code.source].append(code)

started = time.perf_counter()
source_reports = []
with duckdb.connect('data/umls_current.duckdb', read_only=False) as con:
    for source, codes in by_source.items():
        source_started = time.perf_counter()
        totals = Counter()
        match_types = Counter()
        chunks = 0
        for offset in range(0, len(codes), chunk_size):
            chunk = codes[offset:offset+chunk_size]
            report = materialize_patient_friendly_resolutions(
                chunk,
                con,
                policy_version=policy_version,
                replace_existing=True,
                max_depth=max_depth,
            )
            for key in ['inputs','candidates','paths','resolutions','missing_resolutions','original_fallbacks','friendly_resolutions']:
                totals[key] += int(report.get(key, 0))
            match_types.update({str(k): int(v) for k,v in dict(report.get('match_types', {})).items()})
            chunks += 1
            print(f'{source}: {min(offset+chunk_size, len(codes))}/{len(codes)}', flush=True)
        elapsed = time.perf_counter() - source_started
        source_reports.append({
            'source': source,
            'chunks': chunks,
            **dict(totals),
            'match_types': dict(sorted(match_types.items())),
            'resolution_coverage': round(totals['resolutions']/totals['inputs'], 6) if totals['inputs'] else 0,
            'elapsed_seconds': round(elapsed, 3),
        })

report = {
    'generated_at': datetime.now(timezone.utc).isoformat(),
    'status': 'pass',
    'db': 'data/umls_current.duckdb',
    'benchmark': str(benchmark),
    'policy_version': policy_version,
    'max_depth': max_depth,
    'chunk_size': chunk_size,
    'source_count': len(source_reports),
    'sources': source_reports,
    'inputs': sum(r['inputs'] for r in source_reports),
    'candidates': sum(r['candidates'] for r in source_reports),
    'paths': sum(r['paths'] for r in source_reports),
    'resolutions': sum(r['resolutions'] for r in source_reports),
    'missing_resolutions': sum(r['missing_resolutions'] for r in source_reports),
    'elapsed_seconds': round(time.perf_counter()-started, 3),
}

output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding='utf-8')
print(json.dumps({k: report[k] for k in ['status','inputs','resolutions','missing_resolutions','elapsed_seconds']}, indent=2))
