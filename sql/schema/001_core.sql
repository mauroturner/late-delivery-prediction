-- Core tables of the warehouse (docs/plan.md, section 5). Safe to run repeatedly: nothing is dropped or migrated.
-- The model tables (002_model.sql) are created by the units that first use them.
-- Dates are ISO text (YYYY-MM-DD); timestamps are ISO text without a zone (YYYY-MM-DDTHH:MM), as in the files of the deliveries.

-- What is known when an order is created, exactly as delivered (typed, English names, no corrections). There is no outcome column in these two tables.
CREATE TABLE IF NOT EXISTS raw_orders (
    order_id            INTEGER PRIMARY KEY,
    delivery_id         TEXT NOT NULL,
    order_ts            TEXT NOT NULL,
    order_date          TEXT NOT NULL,
    regime              TEXT NOT NULL CHECK (regime IN ('pre_break', 'post_break')),
    shipping_mode       TEXT NOT NULL,
    scheduled_days      INTEGER NOT NULL,
    payment_type        TEXT NOT NULL,
    market              TEXT NOT NULL,
    order_region        TEXT NOT NULL,
    order_country       TEXT NOT NULL,
    customer_segment    TEXT NOT NULL,
    customer_country    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_raw_orders_delivery ON raw_orders (delivery_id);
CREATE INDEX IF NOT EXISTS ix_raw_orders_date ON raw_orders (order_date);

CREATE TABLE IF NOT EXISTS raw_order_lines (
    line_id             INTEGER PRIMARY KEY,
    order_id            INTEGER NOT NULL,
    delivery_id         TEXT NOT NULL,
    department_name     TEXT NOT NULL,
    category_name       TEXT NOT NULL,
    product_card_id     INTEGER NOT NULL,
    product_name        TEXT NOT NULL,   -- without trailing spaces
    quantity            INTEGER NOT NULL,
    product_price       REAL NOT NULL,
    sales               REAL NOT NULL,
    discount            REAL NOT NULL,
    discount_rate       REAL NOT NULL,
    item_total          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_raw_order_lines_order ON raw_order_lines (order_id);
CREATE INDEX IF NOT EXISTS ix_raw_order_lines_delivery ON raw_order_lines (delivery_id);

-- What is known afterwards, from the date it becomes known. Nothing that builds the variables of the model reads this table.
CREATE TABLE IF NOT EXISTS raw_outcomes (
    order_id            INTEGER PRIMARY KEY,
    delivery_id         TEXT NOT NULL,
    outcome_known_date  TEXT NOT NULL,
    delivery_status     TEXT NOT NULL,
    order_status        TEXT NOT NULL,
    real_days           INTEGER NOT NULL,
    shipping_ts         TEXT NOT NULL,
    source_label        INTEGER NOT NULL,
    is_cancelled        INTEGER NOT NULL CHECK (is_cancelled IN (0, 1)),
    benefit_total       REAL NOT NULL,
    profit_ratio_mean   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_raw_outcomes_known ON raw_outcomes (outcome_known_date);
CREATE INDEX IF NOT EXISTS ix_raw_outcomes_delivery ON raw_outcomes (delivery_id);

CREATE TABLE IF NOT EXISTS deliveries (
    delivery_id         TEXT PRIMARY KEY,
    kind                TEXT NOT NULL CHECK (kind IN ('launch', 'monthly', 'close')),
    orders_from         TEXT,            -- NULL for the close, which brings no orders
    orders_to           TEXT,
    outcomes_from       TEXT NOT NULL,
    outcomes_to         TEXT NOT NULL,
    as_of_date          TEXT NOT NULL,
    orders              INTEGER NOT NULL,
    lines               INTEGER NOT NULL,
    outcomes            INTEGER NOT NULL,
    sha256_json         TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ('published', 'loaded', 'rejected')),
    run_key             TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_key             TEXT PRIMARY KEY,
    started_at          TEXT,
    finished_at         TEXT,
    status              TEXT NOT NULL CHECK (status IN ('running', 'ok', 'failed', 'rejected', 'no_delivery')),
    delivery_id         TEXT,
    as_of_date          TEXT,
    source_sha256       TEXT,
    orders_delivery     INTEGER,
    outcomes_delivery   INTEGER,
    orders_scored       INTEGER,
    alerts              INTEGER,
    peak_working_set_mb REAL,
    peak_commit_mb      REAL,            -- NULL outside Windows
    message             TEXT
);

CREATE TABLE IF NOT EXISTS validation_results (
    run_key             TEXT NOT NULL,
    rule_id             TEXT NOT NULL,
    name_es             TEXT NOT NULL,
    severity            TEXT NOT NULL,   -- bloqueante, advertencia, informativa
    scope               TEXT NOT NULL,   -- source, delivery, model
    passed              INTEGER NOT NULL,
    rows_affected       INTEGER NOT NULL,
    orders_affected     INTEGER NOT NULL,
    examples_json       TEXT NOT NULL,   -- ids, dates and reasons only: no personal column exists at this point
    PRIMARY KEY (run_key, rule_id)
);

-- The same tables up to the calculation date (sql/staging), rebuilt by every load. The two order tables have no outcome column on purpose.
CREATE TABLE IF NOT EXISTS stg_orders (
    order_id            INTEGER PRIMARY KEY,
    order_ts            TEXT NOT NULL,
    order_date          TEXT NOT NULL,
    regime              TEXT NOT NULL,
    shipping_mode       TEXT NOT NULL,
    scheduled_days      INTEGER NOT NULL,
    payment_type        TEXT NOT NULL,
    market              TEXT NOT NULL,
    order_region        TEXT NOT NULL,
    order_country       TEXT NOT NULL,
    customer_segment    TEXT NOT NULL,
    customer_country    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stg_order_lines (
    line_id             INTEGER PRIMARY KEY,
    order_id            INTEGER NOT NULL,
    department_name     TEXT NOT NULL,
    category_name       TEXT NOT NULL,
    product_card_id     INTEGER NOT NULL,
    product_name        TEXT NOT NULL,
    quantity            INTEGER NOT NULL,
    product_price       REAL NOT NULL,
    sales               REAL NOT NULL,
    discount            REAL NOT NULL,
    discount_rate       REAL NOT NULL,
    item_total          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_stg_order_lines_order ON stg_order_lines (order_id);

-- Only the outcomes known on the calculation date. is_late is NULL for a cancelled order, which has no label of the task (decision D05).
CREATE TABLE IF NOT EXISTS stg_outcomes (
    order_id            INTEGER PRIMARY KEY,
    outcome_known_date  TEXT NOT NULL,
    delivery_status     TEXT NOT NULL,
    order_status        TEXT NOT NULL,
    real_days           INTEGER NOT NULL,
    shipping_ts         TEXT NOT NULL,
    source_label        INTEGER NOT NULL,
    is_cancelled        INTEGER NOT NULL,
    benefit_total       REAL NOT NULL,
    profit_ratio_mean   REAL NOT NULL,
    is_late             INTEGER CHECK (is_late IN (0, 1))
);
