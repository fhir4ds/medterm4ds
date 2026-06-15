import duckdb
con = duckdb.connect('data/umls_current.duckdb', read_only=True)
query = """
    WITH input_codes(code, input_order) AS (
        VALUES ('56481-5', 0), ('10248-3', 1), ('51316-8', 2)
    ),
    lookup AS (
        SELECT i.input_order, i.code,
               a.aui, a.cui, a.tty, a.name AS technical_name
        FROM input_codes i
        LEFT JOIN mt4ds.best_atoms a
          ON a.source = 'LNC' AND a.code = i.code AND a.rank = 1
    ),
    components AS (
        SELECT l.input_order, c.aui AS comp_aui, c.cui AS comp_cui, c.name AS comp_name
        FROM lookup l
        JOIN main.mrrel r ON r.AUI2 = l.aui AND r.RELA = 'has_component'
        JOIN mt4ds.atoms c ON c.aui = r.AUI1 AND c.source = 'LNC'
    ),
    component_friendly AS (
        SELECT c.input_order, f.name, f.friendly_source, 'component' AS match_type, 1 AS depth
        FROM components c
        JOIN mt4ds.friendly_atoms f ON f.cui = c.comp_cui
        WHERE f.is_broad = false AND f.source IN ('MEDLINEPLUS', 'CHV')
    ),
    first_axis AS (
        SELECT c.input_order, c.comp_name AS name, 'LNC' AS friendly_source, 'first_axis' AS match_type, 2 AS depth
        FROM components c
    ),
    combined AS (
        SELECT * FROM component_friendly
        UNION ALL
        SELECT * FROM first_axis
    ),
    ranked AS (
        SELECT *, ROW_NUMBER() OVER(PARTITION BY input_order ORDER BY depth, CASE friendly_source WHEN 'MEDLINEPLUS' THEN 0 WHEN 'CHV' THEN 1 ELSE 2 END, length(name)) as rn
        FROM combined
    )
    SELECT * FROM ranked WHERE rn=1
"""
print(con.execute(query).fetchall())
