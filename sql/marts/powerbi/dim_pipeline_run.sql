-- One row per run as SQLite has it. Rebuilt alone when a run closes or fails after the export, so that the Parquet file shows the same status as SQLite. Parameters: none.
DROP TABLE IF EXISTS star_dim_pipeline_run;

CREATE TABLE star_dim_pipeline_run AS
SELECT
    run_key, started_at, finished_at, status, delivery_id, as_of_date, source_sha256, orders_delivery, outcomes_delivery, orders_scored, alerts, peak_working_set_mb,
    peak_commit_mb, message,
    CASE WHEN as_of_date IS NULL THEN NULL ELSE CAST(replace(as_of_date, '-', '') AS INTEGER) END AS as_of_key
FROM pipeline_runs
ORDER BY run_key;
