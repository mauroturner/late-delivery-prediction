-- The main line of each order: the line with the most sales; on a tie, the lowest line_id (docs/plan.md section 7). Rebuilt by every load from
-- stg_order_lines. The products of the variables of the model (main department, category and product) come from here. Parameters: none.

DROP TABLE IF EXISTS mart_main_line;

CREATE TABLE mart_main_line AS
WITH ranked AS (
    SELECT
        line_id, order_id, department_name, category_name, product_card_id, product_name, quantity, product_price, sales, discount, discount_rate, item_total,
        ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY sales DESC, line_id ASC) AS position
    FROM stg_order_lines
)
SELECT
    order_id, line_id, department_name, category_name, product_card_id, product_name, quantity, product_price, sales, discount, discount_rate, item_total
FROM ranked
WHERE position = 1
ORDER BY order_id;
