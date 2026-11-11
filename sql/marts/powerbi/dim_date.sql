-- One day per row from the first order to the close, with the block of the split that the date belongs to (from ref_block, the one table of the edges, loaded by the export). Parameters: :first_date :last_date.
DROP TABLE IF EXISTS star_dim_date;

CREATE TABLE star_dim_date AS
WITH RECURSIVE days(day) AS (
    SELECT :first_date
    UNION ALL
    SELECT date(day, '+1 day') FROM days WHERE day < :last_date
)
SELECT
    CAST(replace(day, '-', '') AS INTEGER) AS date_key,
    day AS date,
    CAST(substr(day, 1, 4) AS INTEGER) AS year,
    CAST(substr(day, 6, 2) AS INTEGER) AS month,
    CAST(substr(day, 1, 4) || substr(day, 6, 2) AS INTEGER) AS month_key,
    CASE substr(day, 6, 2)
        WHEN '01' THEN 'enero' WHEN '02' THEN 'febrero' WHEN '03' THEN 'marzo' WHEN '04' THEN 'abril' WHEN '05' THEN 'mayo' WHEN '06' THEN 'junio'
        WHEN '07' THEN 'julio' WHEN '08' THEN 'agosto' WHEN '09' THEN 'septiembre' WHEN '10' THEN 'octubre' WHEN '11' THEN 'noviembre' ELSE 'diciembre'
    END AS month_name_es,
    (CAST(strftime('%w', day) AS INTEGER) + 6) % 7 AS weekday,      -- Monday is 0; the same formula as order_weekday in fact_order.sql
    CASE (CAST(strftime('%w', day) AS INTEGER) + 6) % 7
        WHEN 0 THEN 'lunes' WHEN 1 THEN 'martes' WHEN 2 THEN 'miércoles' WHEN 3 THEN 'jueves' WHEN 4 THEN 'viernes' WHEN 5 THEN 'sábado' ELSE 'domingo'
    END AS weekday_es,
    COALESCE(b.block_es, 'Fuera de los bloques') AS block_es,
    CASE WHEN b.block = 'test' THEN 1 ELSE 0 END AS is_test
FROM days
LEFT JOIN ref_block AS b ON days.day BETWEEN b.first_day AND b.last_day
ORDER BY day;
