-- The labelled orders for a calculation date: the orders of stg_orders whose outcome is in stg_outcomes (so it is known on the date), that are not
-- cancelled, with the label of the task and the block of the split (docs/plan.md sections 7 and 9). It is the only source of labels of the model;
-- the variables are never built from it. Rebuilt by every load and by every recalculation for a date.
-- Blocks, by the date of the order (requirements section 4): train, purge_1, valid_v1, valid_v2, purge_2, test, post_break, and outside for a date that
-- no block holds. The purges belong to no block of the model.
-- Parameters: :train_start :train_end :valid_start :valid_v2_start :valid_end :test_start :test_end :post_break_start :post_break_end.

DROP TABLE IF EXISTS mart_labelled_orders;

CREATE TABLE mart_labelled_orders AS
SELECT
    o.order_id,
    o.order_ts,
    o.order_date,
    o.regime,
    o.shipping_mode,
    o.scheduled_days,
    r.outcome_known_date,
    r.is_late,
    CASE
        WHEN o.order_date >= :train_start AND o.order_date <= :train_end THEN 'train'
        WHEN o.order_date > :train_end AND o.order_date < :valid_start THEN 'purge_1'
        WHEN o.order_date >= :valid_start AND o.order_date < :valid_v2_start THEN 'valid_v1'
        WHEN o.order_date >= :valid_v2_start AND o.order_date <= :valid_end THEN 'valid_v2'
        WHEN o.order_date > :valid_end AND o.order_date < :test_start THEN 'purge_2'
        WHEN o.order_date >= :test_start AND o.order_date <= :test_end THEN 'test'
        WHEN o.order_date >= :post_break_start AND o.order_date <= :post_break_end THEN 'post_break'
        ELSE 'outside'
    END AS block
FROM stg_orders AS o
JOIN stg_outcomes AS r ON r.order_id = o.order_id
WHERE r.is_cancelled = 0
ORDER BY o.order_id;
