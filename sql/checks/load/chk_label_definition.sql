-- Violations: a non-cancelled order whose label in the source is not "real days above scheduled days" (rule V08, checked again on what was loaded).
SELECT r.order_id, r.source_label, r.real_days, o.scheduled_days
FROM raw_outcomes AS r
JOIN raw_orders AS o ON o.order_id = r.order_id
WHERE r.is_cancelled = 0
  AND r.source_label <> CASE WHEN r.real_days > o.scheduled_days THEN 1 ELSE 0 END;
