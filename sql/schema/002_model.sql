-- Tables of the model (docs/plan.md, section 5): the registry of the frozen model, the rates of the baselines and the scores. Safe to run repeatedly: nothing is dropped
-- or migrated. The tables of the follow-up and of the evaluation (tracking_monthly, eval_*) are created by the units that first use them.
-- Dates are ISO text (YYYY-MM-DD).

-- The model trained at the launch: where its artifact is, the SHA-256 that the artifact must have to be loaded (M03), what was chosen and with what costs.
-- A row is never replaced or deleted: the model is frozen (decision D17).
CREATE TABLE IF NOT EXISTS model_registry (
    model_version       TEXT PRIMARY KEY,
    trained_as_of       TEXT NOT NULL,
    artifact_path       TEXT NOT NULL,   -- relative to the project folder
    artifact_sha256     TEXT NOT NULL,
    feature_set         TEXT NOT NULL,
    params_json         TEXT NOT NULL,
    calibration         TEXT NOT NULL,
    threshold           REAL NOT NULL,
    costs_json          TEXT NOT NULL,
    config_sha256       TEXT NOT NULL,
    selection_json      TEXT NOT NULL,   -- the log loss on V1 of each candidate
    valid_v2_json       TEXT NOT NULL    -- the figures on V2, and the fingerprint of its scores
);

CREATE TRIGGER IF NOT EXISTS model_registry_no_update BEFORE UPDATE ON model_registry
BEGIN
    SELECT RAISE(ABORT, 'model_registry is insert-only');
END;

CREATE TRIGGER IF NOT EXISTS model_registry_no_delete BEFORE DELETE ON model_registry
BEGIN
    SELECT RAISE(ABORT, 'model_registry is insert-only');
END;

CREATE TRIGGER IF NOT EXISTS model_registry_no_replace BEFORE INSERT ON model_registry
WHEN EXISTS (SELECT 1 FROM model_registry WHERE model_version = NEW.model_version)
BEGIN
    SELECT RAISE(ABORT, 'model_registry is insert-only');
END;

-- The rates of B2 and B5 fitted on the training block at the launch (cell: the mode, or the mode and "before" or "from" the noon hour).
CREATE TABLE IF NOT EXISTS baseline_rates (
    baseline            TEXT NOT NULL,
    cell                TEXT NOT NULL,
    rate                REAL NOT NULL,
    train_orders        INTEGER NOT NULL,
    PRIMARY KEY (baseline, cell)
);

-- One score per new order, never changed: insert-only. Three triggers abort an update, a delete and an insert of an order that is already scored; the third exists
-- because INSERT OR REPLACE and REPLACE delete the old row without running the delete trigger while recursive_triggers is off (project 02, its D44).
CREATE TABLE IF NOT EXISTS model_scores (
    order_id            INTEGER PRIMARY KEY,
    as_of_date          TEXT NOT NULL,
    run_key             TEXT NOT NULL,
    model_version       TEXT NOT NULL,
    risk                REAL NOT NULL CHECK (risk >= 0 AND risk <= 1),
    threshold           REAL NOT NULL,
    is_alert            INTEGER NOT NULL CHECK (is_alert IN (0, 1))
);

CREATE TRIGGER IF NOT EXISTS model_scores_no_update BEFORE UPDATE ON model_scores
BEGIN
    SELECT RAISE(ABORT, 'model_scores is insert-only');
END;

CREATE TRIGGER IF NOT EXISTS model_scores_no_delete BEFORE DELETE ON model_scores
BEGIN
    SELECT RAISE(ABORT, 'model_scores is insert-only');
END;

CREATE TRIGGER IF NOT EXISTS model_scores_no_replace BEFORE INSERT ON model_scores
WHEN EXISTS (SELECT 1 FROM model_scores WHERE order_id = NEW.order_id)
BEGIN
    SELECT RAISE(ABORT, 'model_scores is insert-only');
END;
