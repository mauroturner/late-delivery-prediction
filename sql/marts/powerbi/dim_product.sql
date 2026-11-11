-- One row per product card of the order lines. A card that appears with more than one name takes the first in alphabetical order. Parameters: none.
DROP TABLE IF EXISTS star_dim_product;

CREATE TABLE star_dim_product AS
SELECT
    product_card_id AS product_key,
    MIN(product_name) AS product_name,
    MIN(category_name) AS category_name,
    MIN(department_name) AS department_name
FROM raw_order_lines
GROUP BY product_card_id
ORDER BY product_card_id;
