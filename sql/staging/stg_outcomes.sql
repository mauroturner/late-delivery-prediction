-- Rebuilds stg_outcomes: the outcomes that are known on the calculation date, with the label of the task (docs/plan.md section 7).
-- is_late is "real days above scheduled days" and NULL for a cancelled order (decision D05). The scheduled days come from the order.
-- Parameters: :as_of (ISO date).

DELETE FROM stg_outcomes;

INSERT INTO stg_outcomes (
    order_id, outcome_known_date, delivery_status, order_status, real_days, shipping_ts, source_label, is_cancelled, benefit_total, profit_ratio_mean, is_late
)
SELECT
    r.order_id, r.outcome_known_date, r.delivery_status, r.order_status, r.real_days, r.shipping_ts, r.source_label, r.is_cancelled, r.benefit_total,
    r.profit_ratio_mean,
    CASE WHEN r.is_cancelled = 1 THEN NULL WHEN r.real_days > o.scheduled_days THEN 1 ELSE 0 END
FROM raw_outcomes AS r
JOIN raw_orders AS o ON o.order_id = r.order_id
WHERE r.outcome_known_date <= :as_of AND o.order_date <= :as_of
ORDER BY r.order_id;
