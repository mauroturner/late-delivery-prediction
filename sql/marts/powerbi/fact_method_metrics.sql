-- The metrics of each method in each evaluated block, one row per scope, method and seal state. ref_method (loaded by the export) gives the Spanish label.
DROP TABLE IF EXISTS star_fact_method_metrics;

CREATE TABLE star_fact_method_metrics AS
SELECT
    e.scope AS scope,
    e.method AS method,
    COALESCE(m.label_es, e.method) AS label_es,
    MAX(CASE WHEN e.metric = 'auc' THEN e.value END) AS auc,
    MAX(CASE WHEN e.metric = 'pr_auc' THEN e.value END) AS pr_auc,
    MAX(CASE WHEN e.metric = 'brier' THEN e.value END) AS brier,
    MAX(CASE WHEN e.metric = 'ece' THEN e.value END) AS ece,
    MAX(CASE WHEN e.metric = 'threshold' THEN e.value END) AS threshold,
    MAX(CASE WHEN e.metric = 'flagged_share' THEN e.value END) AS flagged_share,
    MAX(CASE WHEN e.metric = 'precision' THEN e.value END) AS precision,
    MAX(CASE WHEN e.metric = 'recall' THEN e.value END) AS recall,
    MAX(CASE WHEN e.metric = 'nb_total' THEN e.value END) AS nb_total,
    MAX(CASE WHEN e.metric = 'nb_per_1000' THEN e.value END) AS nb_per_1000,
    e.seal_state AS seal_state
FROM eval_metrics AS e
LEFT JOIN ref_method AS m ON m.method = e.method
GROUP BY e.scope, e.method, e.seal_state
ORDER BY e.scope, COALESCE(m.sort_order, 99), e.method, e.seal_state;
