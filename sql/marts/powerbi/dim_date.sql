-- One day per row from the first order to the close, with the block of the split that the date belongs to. Parameters: :first_date :last_date and the edges of the split.
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
    (CAST(strftime('%w', day) AS INTEGER) + 6) % 7 AS weekday,
    CASE (CAST(strftime('%w', day) AS INTEGER) + 6) % 7
        WHEN 0 THEN 'lunes' WHEN 1 THEN 'martes' WHEN 2 THEN 'miércoles' WHEN 3 THEN 'jueves' WHEN 4 THEN 'viernes' WHEN 5 THEN 'sábado' ELSE 'domingo'
    END AS weekday_es,
    CASE
        WHEN day >= :train_start AND day <= :train_end THEN 'Entrenamiento'
        WHEN day > :train_end AND day < :valid_start THEN 'Purga 1'
        WHEN day >= :valid_start AND day < :valid_v2_start THEN 'Validación V1'
        WHEN day >= :valid_v2_start AND day <= :valid_end THEN 'Validación V2'
        WHEN day > :valid_end AND day < :test_start THEN 'Purga 2'
        WHEN day >= :test_start AND day <= :test_end THEN 'Prueba'
        WHEN day >= :post_break_start AND day <= :post_break_end THEN 'Posterior al cambio de forma'
        ELSE 'Fuera de los bloques'
    END AS block_es,
    CASE WHEN day >= :test_start AND day <= :test_end THEN 1 ELSE 0 END AS is_test
FROM days
ORDER BY day;
