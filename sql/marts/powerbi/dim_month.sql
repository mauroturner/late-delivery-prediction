-- One month per row from the month of the first order to the month of the close. Parameters: :first_date :last_date and the edges of the split.
DROP TABLE IF EXISTS star_dim_month;

CREATE TABLE star_dim_month AS
WITH RECURSIVE months(month_start) AS (
    SELECT substr(:first_date, 1, 7) || '-01'
    UNION ALL
    SELECT date(month_start, '+1 month') FROM months WHERE month_start < substr(:last_date, 1, 7) || '-01'
)
SELECT
    CAST(substr(month_start, 1, 4) || substr(month_start, 6, 2) AS INTEGER) AS month_key,
    month_start,
    substr(month_start, 1, 7) AS year_month_label,
    CASE
        WHEN month_start >= :train_start AND month_start <= :train_end THEN 'Entrenamiento'
        WHEN month_start > :train_end AND month_start < :valid_start THEN 'Purga 1'
        WHEN month_start >= :valid_start AND month_start < :valid_v2_start THEN 'Validación V1'
        WHEN month_start >= :valid_v2_start AND month_start <= :valid_end THEN 'Validación V2'
        WHEN month_start > :valid_end AND month_start < :test_start THEN 'Purga 2'
        WHEN month_start >= :test_start AND month_start <= :test_end THEN 'Prueba'
        WHEN month_start >= :post_break_start AND month_start <= :post_break_end THEN 'Posterior al cambio de forma'
        ELSE 'Fuera de los bloques'
    END AS block_es,
    CASE WHEN month_start >= :post_break_start THEN 'post_break' ELSE 'pre_break' END AS regime
FROM months
ORDER BY month_start;
