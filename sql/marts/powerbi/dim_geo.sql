-- One row per combination of market, region and country that has orders; the key is its position in alphabetical order. Parameters: none.
DROP TABLE IF EXISTS star_dim_geo;

CREATE TABLE star_dim_geo AS
SELECT
    ROW_NUMBER() OVER (ORDER BY market, order_region, order_country) AS geo_key,
    market,
    order_region,
    order_country
FROM (SELECT DISTINCT market, order_region, order_country FROM raw_orders)
ORDER BY geo_key;
