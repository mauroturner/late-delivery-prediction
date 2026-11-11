-- The payment types that have orders, with their name in Spanish. Parameters: none.
DROP TABLE IF EXISTS star_dim_payment;

CREATE TABLE star_dim_payment AS
SELECT
    payment_type AS payment_key,
    CASE payment_type WHEN 'DEBIT' THEN 'Débito' WHEN 'TRANSFER' THEN 'Transferencia' WHEN 'CASH' THEN 'Efectivo' WHEN 'PAYMENT' THEN 'Pago en línea' ELSE payment_type END AS payment_es
FROM (SELECT DISTINCT payment_type FROM raw_orders)
ORDER BY payment_key;
