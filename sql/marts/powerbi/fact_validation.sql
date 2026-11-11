-- One row per run and validation rule, with the status in Spanish: pasa, falla (a blocking rule that failed) or advierte (a warning that failed). Parameters: none.
DROP TABLE IF EXISTS star_fact_validation;

CREATE TABLE star_fact_validation AS
SELECT
    run_key, rule_id, name_es, severity, scope,
    CASE WHEN passed = 1 THEN 'pasa' WHEN severity = 'bloqueante' THEN 'falla' ELSE 'advierte' END AS status,
    rows_affected, orders_affected
FROM validation_results
ORDER BY run_key, rule_id;
