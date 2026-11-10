-- Violations: an outcome known more than purge_days - 1 days after its order: the purge of the split would not cover it (rule V09, checked again on what was loaded).
-- Parameters: :purge_days.
SELECT r.order_id, o.order_date, r.outcome_known_date
FROM raw_outcomes AS r
JOIN raw_orders AS o ON o.order_id = r.order_id
WHERE julianday(r.outcome_known_date) - julianday(o.order_date) > :purge_days - 1;
