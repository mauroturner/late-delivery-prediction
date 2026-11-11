-- The shipping modes that have orders, with the late rate of the training block (the rule B2) when a model is registered. Parameters: none.
DROP TABLE IF EXISTS star_dim_shipping_mode;

CREATE TABLE star_dim_shipping_mode AS
SELECT
    m.shipping_mode AS mode_key,
    CASE m.shipping_mode
        WHEN 'First Class' THEN 'Primera clase' WHEN 'Second Class' THEN 'Segunda clase' WHEN 'Same Day' THEN 'Mismo día' WHEN 'Standard Class' THEN 'Clase estándar'
        ELSE m.shipping_mode
    END AS mode_es,
    m.scheduled_days AS scheduled_days,
    b.rate AS train_late_rate,
    ROW_NUMBER() OVER (ORDER BY m.scheduled_days, m.shipping_mode) AS sort_order
FROM (SELECT shipping_mode, MIN(scheduled_days) AS scheduled_days FROM raw_orders GROUP BY shipping_mode) AS m
LEFT JOIN baseline_rates AS b ON b.baseline = 'b2_mode' AND b.cell = m.shipping_mode
ORDER BY sort_order;
