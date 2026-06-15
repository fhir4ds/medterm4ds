import duckdb
import time
con = duckdb.connect('data/umls_current.duckdb', read_only=True)
query = """
    WITH RECURSIVE
    input_codes(source, code, input_order) AS (
        SELECT source, code, 1 FROM mt4ds.best_atoms WHERE source='ICD10CM' LIMIT 250
    ),
    lookup AS (
        SELECT i.input_order, i.source, i.code,
               a.aui, a.cui, a.tty, a.name AS technical_name
        FROM input_codes i
        LEFT JOIN mt4ds.best_atoms a
          ON a.source = i.source
         AND a.code = i.code
         AND a.rank = 1
    ),
    native_walk(input_order, source, input_code, walk_code, walk_aui, walk_cui, walk_tty, source_depth) AS (
        SELECT input_order, source, code, code, aui, cui, tty, 0
        FROM lookup
        WHERE aui IS NOT NULL
          AND source != 'SNOMEDCT_US'
        UNION ALL
        SELECT w.input_order, w.source, w.input_code, e.to_code, e.to_aui,
               e.to_cui, e.to_tty, w.source_depth + 1
        FROM native_walk w
        JOIN mt4ds.walk_edges e
          ON e.source = w.source
         AND e.from_aui = w.walk_aui
         AND e.direction = 'parent'
        WHERE w.source_depth < 5
    ),
    snomed_seed AS (
        SELECT DISTINCT l.input_order, l.source, l.code AS input_code,
               l.code AS source_walk_code, 0 AS source_depth,
               l.code AS snomed_code, l.aui AS snomed_aui, l.cui AS snomed_cui
        FROM lookup l
        WHERE l.source = 'SNOMEDCT_US'
          AND l.aui IS NOT NULL
        UNION ALL
        SELECT DISTINCT w.input_order, w.source, w.input_code,
               w.walk_code AS source_walk_code, w.source_depth,
               sce.target_code AS snomed_code, ba.aui AS snomed_aui,
               ba.cui AS snomed_cui
        FROM native_walk w
        JOIN mt4ds.crosswalk_edges sce
          ON sce.source = w.source
         AND sce.code = w.walk_code
         AND sce.target_source = 'SNOMEDCT_US'
         AND sce.match_type = 'same_cui'
        JOIN mt4ds.best_atoms ba
          ON ba.source = 'SNOMEDCT_US'
         AND ba.code = sce.target_code
         AND ba.rank = 1
    ),
    snomed_walk(
        input_order, source, input_code, source_walk_code, source_depth,
        snomed_code, walk_code, walk_aui, walk_cui, snomed_depth
    ) AS (
        SELECT input_order, source, input_code, source_walk_code, source_depth,
               snomed_code, snomed_code, snomed_aui, snomed_cui, 0
        FROM snomed_seed
        UNION ALL
        SELECT w.input_order, w.source, w.input_code, w.source_walk_code,
               w.source_depth, w.snomed_code, e.to_code, e.to_aui, e.to_cui,
               w.snomed_depth + 1
        FROM snomed_walk w
        JOIN mt4ds.walk_edges e
          ON e.source = 'SNOMEDCT_US'
         AND e.from_aui = w.walk_aui
         AND e.direction = 'parent'
        WHERE w.snomed_depth < 5
    ),
    guarded_walk AS (
        SELECT w.*
        FROM snomed_walk w
        LEFT JOIN mt4ds.snomed_top_level_depth tld
          ON tld.code = w.walk_code
        WHERE tld.code IS NULL OR tld.min_top_depth > 3
    ),
    friendly_hits AS (
        SELECT w.input_order, w.source, w.input_code, w.source_walk_code,
               w.source_depth, w.snomed_code, w.walk_code, w.walk_aui,
               w.walk_cui, w.snomed_depth, w.source_depth + w.snomed_depth AS match_depth,
               f.name, f.friendly_source, f.code AS friendly_code
        FROM guarded_walk w
        JOIN mt4ds.friendly_atoms f
          ON f.cui = w.walk_cui
        WHERE f.is_broad = false
          AND f.source IN ('MEDLINEPLUS', 'CHV')
    ),
    ranked AS (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY input_order, name, friendly_source
            ORDER BY match_depth
        ) AS rn
        FROM friendly_hits
    )
    SELECT count(*) FROM ranked WHERE rn = 1
"""
start = time.perf_counter()
print(con.execute(query).fetchone()[0])
print(f"Elapsed: {time.perf_counter() - start:.2f}s")
