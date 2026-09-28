CREATE TABLE IF NOT EXISTS platform_products (
    product_id VARCHAR PRIMARY KEY,
    product_name VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_expected_segments (
    target_date DATE NOT NULL,
    segment_id VARCHAR NOT NULL,
    from_time TIME NOT NULL,
    to_time TIME NOT NULL,
    source_relative_path VARCHAR NOT NULL,
    expected_sha256 VARCHAR NOT NULL,
    archive_arrived_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (target_date, segment_id)
);

CREATE TABLE IF NOT EXISTS platform_source_files (
    source_file_id VARCHAR PRIMARY KEY,
    target_date DATE NOT NULL,
    segment_id VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL,
    stored_path VARCHAR NOT NULL,
    archive_arrived_at TIMESTAMPTZ NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_orders (
    order_id VARCHAR PRIMARY KEY,
    target_date DATE NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    segment_id VARCHAR NOT NULL,
    source_file_id VARCHAR NOT NULL,
    source_order_index BIGINT NOT NULL,
    order_fingerprint VARCHAR NOT NULL,
    raw_json VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_order_lines (
    order_id VARCHAR NOT NULL,
    line_id VARCHAR NOT NULL,
    product_id VARCHAR NOT NULL,
    qty BIGINT NOT NULL,
    unit_price_krw BIGINT NOT NULL,
    source_file_id VARCHAR NOT NULL,
    segment_id VARCHAR NOT NULL,
    source_order_index BIGINT NOT NULL,
    source_line_index BIGINT NOT NULL,
    line_fingerprint VARCHAR NOT NULL,
    raw_json VARCHAR NOT NULL,
    PRIMARY KEY (order_id, line_id)
);

CREATE TABLE IF NOT EXISTS platform_runs (
    run_id VARCHAR PRIMARY KEY,
    target_date DATE NOT NULL,
    run_kind VARCHAR NOT NULL CHECK (run_kind IN ('initial', 'recovery')),
    status VARCHAR NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    selected_segments VARCHAR NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    error_message VARCHAR,
    owner_pid BIGINT NOT NULL,
    owner_token VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_run_segments (
    run_id VARCHAR NOT NULL,
    target_date DATE NOT NULL,
    segment_id VARCHAR NOT NULL,
    source_file_id VARCHAR NOT NULL,
    PRIMARY KEY (run_id, segment_id)
);

CREATE TABLE IF NOT EXISTS platform_result_artifacts (
    run_id VARCHAR PRIMARY KEY,
    artifact_path VARCHAR NOT NULL,
    result_state VARCHAR NOT NULL CHECK (result_state IN ('partial', 'complete')),
    published_at TIMESTAMPTZ,
    is_current BOOLEAN NOT NULL
);
