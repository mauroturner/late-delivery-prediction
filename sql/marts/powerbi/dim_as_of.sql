-- The calculation dates of the loaded deliveries: a parameter table with no relationships. Parameters: none.
DROP TABLE IF EXISTS star_dim_as_of;

CREATE TABLE star_dim_as_of AS
SELECT
    CAST(replace(as_of_date, '-', '') AS INTEGER) AS as_of_key,
    as_of_date,
    as_of_date || ' (' || delivery_id || ')' AS as_of_label
FROM deliveries
WHERE status = 'loaded'
ORDER BY as_of_date;
