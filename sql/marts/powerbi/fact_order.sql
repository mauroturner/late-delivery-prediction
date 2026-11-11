-- One row per order: its variables of the moment, the block (from ref_block, the one table of the edges of the split, loaded by the export), the score the frozen model gave it when it was new and the outcome once it is known. The score and the
-- outcome are NULL where they do not exist. Reads dim_geo, which is built first. Parameters: the edges of the split.
DROP TABLE IF EXISTS star_fact_order;

CREATE TABLE star_fact_order AS
WITH ranked AS (
    SELECT order_id, product_card_id, ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY sales DESC, line_id ASC) AS position
    FROM raw_order_lines
),
main_line AS (
    SELECT order_id, product_card_id FROM ranked WHERE position = 1
),
totals AS (
    SELECT order_id, COUNT(*) AS n_lines, SUM(quantity) AS units, SUM(sales) AS gross_sales, SUM(item_total) AS item_total, SUM(discount) AS discount
    FROM raw_order_lines
    GROUP BY order_id
)
SELECT
    o.order_id AS order_id,
    CAST(replace(o.order_date, '-', '') AS INTEGER) AS order_date_key,
    o.shipping_mode AS mode_key,
    g.geo_key AS geo_key,
    m.product_card_id AS main_product_key,
    o.customer_segment AS segment_key,
    o.payment_type AS payment_key,
    o.order_ts AS order_ts,
    CAST(substr(o.order_ts, 12, 2) AS INTEGER) AS order_hour,
    (CAST(strftime('%w', o.order_date) AS INTEGER) + 6) % 7 AS order_weekday,      -- Monday is 0; the same formula as weekday in dim_date.sql
    t.n_lines AS n_lines,
    t.units AS units,
    t.gross_sales AS gross_sales,
    t.item_total AS item_total,
    t.discount AS discount,
    COALESCE(b.block, 'outside') AS block,
    o.regime AS regime,
    CASE WHEN r.order_id IS NULL THEN 'open' WHEN r.is_cancelled = 1 THEN 'cancelled' ELSE 'closed' END AS outcome_status,
    r.outcome_known_date AS outcome_known_date,
    CASE WHEN r.order_id IS NOT NULL AND r.is_cancelled = 0 THEN r.real_days END AS real_days,
    CASE WHEN r.order_id IS NOT NULL AND r.is_cancelled = 0 THEN CASE WHEN r.real_days > o.scheduled_days THEN 1 ELSE 0 END END AS is_late,
    s.risk AS risk,
    s.is_alert AS is_alert,
    s.threshold AS threshold,
    s.as_of_date AS scored_as_of_date,
    s.model_version AS model_version,
    s.run_key AS run_key
FROM raw_orders AS o
JOIN star_dim_geo AS g ON g.market = o.market AND g.order_region = o.order_region AND g.order_country = o.order_country
JOIN main_line AS m ON m.order_id = o.order_id
JOIN totals AS t ON t.order_id = o.order_id
LEFT JOIN ref_block AS b ON o.order_date BETWEEN b.first_day AND b.last_day
LEFT JOIN raw_outcomes AS r ON r.order_id = o.order_id
LEFT JOIN model_scores AS s ON s.order_id = o.order_id
ORDER BY o.order_id;
