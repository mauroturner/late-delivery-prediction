-- Violations: a fact row whose foreign key is empty or not in its dimension (relationships R01 to R09 of the star schema).
SELECT 'R01' AS relationship, CAST(order_date_key AS TEXT) AS key_value FROM star_fact_order
WHERE order_date_key IS NULL OR order_date_key NOT IN (SELECT date_key FROM star_dim_date)
UNION ALL
SELECT 'R02', mode_key FROM star_fact_order
WHERE mode_key IS NULL OR mode_key NOT IN (SELECT mode_key FROM star_dim_shipping_mode)
UNION ALL
SELECT 'R03', mode_key FROM star_fact_tracking_monthly
WHERE mode_key IS NULL OR mode_key NOT IN (SELECT mode_key FROM star_dim_shipping_mode)
UNION ALL
SELECT 'R04', CAST(geo_key AS TEXT) FROM star_fact_order
WHERE geo_key IS NULL OR geo_key NOT IN (SELECT geo_key FROM star_dim_geo)
UNION ALL
SELECT 'R05', CAST(main_product_key AS TEXT) FROM star_fact_order
WHERE main_product_key IS NULL OR main_product_key NOT IN (SELECT product_key FROM star_dim_product)
UNION ALL
SELECT 'R06', segment_key FROM star_fact_order
WHERE segment_key IS NULL OR segment_key NOT IN (SELECT segment_key FROM star_dim_segment)
UNION ALL
SELECT 'R07', payment_key FROM star_fact_order
WHERE payment_key IS NULL OR payment_key NOT IN (SELECT payment_key FROM star_dim_payment)
UNION ALL
SELECT 'R08', CAST(month_key AS TEXT) FROM star_fact_tracking_monthly
WHERE month_key IS NULL OR month_key NOT IN (SELECT month_key FROM star_dim_month)
UNION ALL
SELECT 'R09', run_key FROM star_fact_validation
WHERE run_key IS NULL OR run_key NOT IN (SELECT run_key FROM star_dim_pipeline_run);
