CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id VARCHAR PRIMARY KEY,
    source_file_id VARCHAR NOT NULL,
    input_path VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    error_message VARCHAR,
    owner_pid BIGINT,
    owner_token VARCHAR,
    recovered_at TIMESTAMPTZ,
    recovery_reason VARCHAR,
    recovered_artifact_path VARCHAR
);

ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS owner_pid BIGINT;
ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS owner_token VARCHAR;
ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS recovered_at TIMESTAMPTZ;
ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS recovery_reason VARCHAR;
ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS recovered_artifact_path VARCHAR;

CREATE TABLE IF NOT EXISTS source_files (
    source_file_id VARCHAR PRIMARY KEY,
    sha256 VARCHAR NOT NULL,
    original_file_name VARCHAR NOT NULL,
    source_url VARCHAR NOT NULL,
    stored_path VARCHAR NOT NULL,
    workbook_sheet VARCHAR NOT NULL,
    selected_excel_rows VARCHAR NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL
);

-- A source file is preserved once, while each run can select a different subset
-- of source locations. `source_files.selected_excel_rows` predates this split and
-- remains only as a registration snapshot for existing local databases.
CREATE TABLE IF NOT EXISTS run_input_scopes (
    run_id VARCHAR PRIMARY KEY,
    source_file_id VARCHAR NOT NULL,
    sheet_name VARCHAR NOT NULL,
    selected_excel_rows VARCHAR NOT NULL,
    input_scope_id VARCHAR NOT NULL,
    aggregation_rule_id VARCHAR NOT NULL,
    transformation_rule_id VARCHAR
);

ALTER TABLE run_input_scopes
ADD COLUMN IF NOT EXISTS transformation_rule_id VARCHAR;

CREATE TABLE IF NOT EXISTS run_input_rows (
    run_id VARCHAR NOT NULL,
    source_file_id VARCHAR NOT NULL,
    sheet_name VARCHAR NOT NULL,
    source_row_number BIGINT NOT NULL,
    PRIMARY KEY (run_id, sheet_name, source_row_number)
);

CREATE TABLE IF NOT EXISTS source_rows (
    source_file_id VARCHAR NOT NULL,
    sheet_name VARCHAR NOT NULL,
    source_row_number BIGINT NOT NULL,
    invoice_no_raw VARCHAR,
    invoice_no_source_kind VARCHAR,
    stock_code_raw VARCHAR,
    description_raw VARCHAR,
    quantity_raw VARCHAR,
    invoice_date_raw VARCHAR,
    unit_price_raw VARCHAR,
    customer_id_raw VARCHAR,
    country_raw VARCHAR,
    loaded_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source_file_id, sheet_name, source_row_number)
);

ALTER TABLE source_rows
ADD COLUMN IF NOT EXISTS invoice_no_source_kind VARCHAR;

CREATE TABLE IF NOT EXISTS published_results (
    run_id VARCHAR PRIMARY KEY,
    artifact_path VARCHAR NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    is_current BOOLEAN NOT NULL
);
