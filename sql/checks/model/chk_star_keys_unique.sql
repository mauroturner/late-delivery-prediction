-- Violations: a dimension key that appears more than once, or a grain of a fact that does.
SELECT 'dim_date' AS table_name, CAST(date_key AS TEXT) AS key_value FROM star_dim_date GROUP BY date_key HAVING COUNT(*) > 1
UNION ALL
SELECT 'dim_month', CAST(month_key AS TEXT) FROM star_dim_month GROUP BY month_key HAVING COUNT(*) > 1
UNION ALL
SELECT 'dim_shipping_mode', mode_key FROM star_dim_shipping_mode GROUP BY mode_key HAVING COUNT(*) > 1
UNION ALL
SELECT 'dim_geo', CAST(geo_key AS TEXT) FROM star_dim_geo GROUP BY geo_key HAVING COUNT(*) > 1
UNION ALL
SELECT 'dim_product', CAST(product_key AS TEXT) FROM star_dim_product GROUP BY product_key HAVING COUNT(*) > 1
UNION ALL
SELECT 'dim_segment', segment_key FROM star_dim_segment GROUP BY segment_key HAVING COUNT(*) > 1
UNION ALL
SELECT 'dim_payment', payment_key FROM star_dim_payment GROUP BY payment_key HAVING COUNT(*) > 1
UNION ALL
SELECT 'dim_model', model_version FROM star_dim_model GROUP BY model_version HAVING COUNT(*) > 1
UNION ALL
SELECT 'dim_pipeline_run', run_key FROM star_dim_pipeline_run GROUP BY run_key HAVING COUNT(*) > 1
UNION ALL
SELECT 'dim_as_of', CAST(as_of_key AS TEXT) FROM star_dim_as_of GROUP BY as_of_key HAVING COUNT(*) > 1
UNION ALL
SELECT 'fact_tracking_monthly', CAST(month_key AS TEXT) || '/' || mode_key FROM star_fact_tracking_monthly GROUP BY month_key, mode_key HAVING COUNT(*) > 1
UNION ALL
SELECT 'fact_method_metrics', scope || '/' || method || '/' || seal_state FROM star_fact_method_metrics GROUP BY scope, method, seal_state HAVING COUNT(*) > 1
UNION ALL
SELECT 'fact_evaluation_summary', criterion_id || '/' || seal_state FROM star_fact_evaluation_summary GROUP BY criterion_id, seal_state HAVING COUNT(*) > 1
UNION ALL
SELECT 'fact_roc', scope || '/' || method || '/' || seal_state || '/' || CAST(point AS TEXT) FROM star_fact_roc GROUP BY scope, method, seal_state, point HAVING COUNT(*) > 1
UNION ALL
SELECT 'fact_calibration', scope || '/' || method || '/' || seal_state || '/' || CAST(bin AS TEXT) FROM star_fact_calibration GROUP BY scope, method, seal_state, bin HAVING COUNT(*) > 1
UNION ALL
SELECT 'fact_cost_sensitivity', scope || '/' || CAST(break_even AS TEXT) || '/' || method || '/' || seal_state
FROM star_fact_cost_sensitivity GROUP BY scope, break_even, method, seal_state HAVING COUNT(*) > 1
UNION ALL
SELECT 'fact_leakage', source_column FROM star_fact_leakage GROUP BY source_column HAVING COUNT(*) > 1
UNION ALL
SELECT 'fact_importance', feature FROM star_fact_importance GROUP BY feature HAVING COUNT(*) > 1
UNION ALL
SELECT 'fact_validation', run_key || '/' || rule_id FROM star_fact_validation GROUP BY run_key, rule_id HAVING COUNT(*) > 1;
