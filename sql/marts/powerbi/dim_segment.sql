-- The customer segments that have orders, with their name in Spanish. Parameters: none.
DROP TABLE IF EXISTS star_dim_segment;

CREATE TABLE star_dim_segment AS
SELECT
    customer_segment AS segment_key,
    CASE customer_segment WHEN 'Consumer' THEN 'Consumidor' WHEN 'Corporate' THEN 'Corporativo' WHEN 'Home Office' THEN 'Oficina en casa' ELSE customer_segment END AS segment_es
FROM (SELECT DISTINCT customer_segment FROM raw_orders)
ORDER BY segment_key;
