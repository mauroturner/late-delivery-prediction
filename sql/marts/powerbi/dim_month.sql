-- One month per row from the month of the first order to the month of the close; the block is the one of the first day of the month (from ref_block, the one table of the edges, loaded by the export). Parameters: :first_date :last_date.
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
    COALESCE(b.block_es, 'Fuera de los bloques') AS block_es,
    CASE WHEN month_start >= (SELECT first_day FROM ref_block WHERE block = 'post_break') THEN 'post_break' ELSE 'pre_break' END AS regime
FROM months
LEFT JOIN ref_block AS b ON months.month_start BETWEEN b.first_day AND b.last_day
ORDER BY month_start;
