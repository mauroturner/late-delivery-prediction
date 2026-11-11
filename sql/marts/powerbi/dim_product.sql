-- One row per product card of the order lines, with the name, the category and the department of one line. A card that appears with more than one combination takes the
-- first by name, then category, then department (then line), so the three attributes always appeared together in the source. The real file has no such card (D70). Parameters: none.
DROP TABLE IF EXISTS star_dim_product;

CREATE TABLE star_dim_product AS
WITH ranked AS (
    SELECT product_card_id, product_name, category_name, department_name,
           ROW_NUMBER() OVER (PARTITION BY product_card_id ORDER BY product_name, category_name, department_name, line_id) AS position
    FROM raw_order_lines
)
SELECT product_card_id AS product_key, product_name, category_name, department_name
FROM ranked
WHERE position = 1
ORDER BY product_card_id;
