import duckdb
import time
from medterm4ds import CodeRef
from medterm4ds.services.patient_friendly_materialized import _rxnorm_tty_candidate_rows
con = duckdb.connect('data/umls_current.duckdb', read_only=True)
query = """
    WITH RECURSIVE
    input_codes(code, input_order) AS (
        SELECT code, 1 FROM mt4ds.best_atoms WHERE source='RXNORM' LIMIT 250
    ),
    base AS (
        SELECT i.input_order, a.code, a.aui, a.tty, a.name AS technical_name
        FROM input_codes i
        JOIN mt4ds.best_atoms a
          ON a.source = 'RXNORM'
         AND a.code = i.code
         AND a.rank = 1
    ),
    strategy AS (
        SELECT *
        FROM mt4ds.patient_friendly_strategy
        WHERE source = 'RXNORM'
          AND walk_kind = 'tty_traversal'
    ),
    paths AS (
        SELECT b.input_order, b.code AS input_code, b.aui AS start_aui,
               b.tty AS start_tty, b.technical_name,
               p.path_id, p.target_tty, p.match_type, p.target_order,
               p.path_depth AS max_depth
        FROM base b
        JOIN mt4ds.rxnorm_tty_paths p
          ON p.start_tty = b.tty
        JOIN strategy s
          ON s.target_tty = p.target_tty
         AND s.match_type = p.match_type
    ),
    walk(input_order, input_code, technical_name, path_id, target_tty,
         match_type, target_order, step, aui) AS (
        SELECT input_order, input_code, technical_name, path_id, target_tty,
               match_type, target_order, 0, start_aui
        FROM paths
        UNION ALL
        SELECT w.input_order, w.input_code, w.technical_name, w.path_id,
               w.target_tty, w.match_type, w.target_order, w.step + 1,
               e.target_aui
        FROM walk w
        JOIN mt4ds.rxnorm_tty_path_steps ps
          ON ps.path_id = w.path_id
         AND ps.step = w.step + 1
        JOIN mt4ds.rxnorm_tty_edges e
          ON e.source_aui = w.aui
         AND e.target_tty = ps.tty
        WHERE w.step < (
            SELECT MAX(ps2.step)
            FROM mt4ds.rxnorm_tty_path_steps ps2
            WHERE ps2.path_id = w.path_id
        )
    ),
    hits AS (
        SELECT w.input_order, w.input_code, w.technical_name,
               w.target_tty, w.match_type, w.target_order,
               w.step AS match_depth,
               e.target_aui, e.target_code, e.target_name,
               e.target_suppress
        FROM walk w
        JOIN mt4ds.rxnorm_tty_edges e
          ON e.target_aui = w.aui
        WHERE w.step = (
            SELECT MAX(ps2.step)
            FROM mt4ds.rxnorm_tty_path_steps ps2
            WHERE ps2.path_id = w.path_id
        )
    )
    SELECT count(*) FROM hits
"""
start = time.perf_counter()
print(con.execute(query).fetchone()[0])
print(f"Elapsed: {time.perf_counter() - start:.2f}s")
