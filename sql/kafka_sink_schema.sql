CREATE TABLE IF NOT EXISTS kafka_consumer_runs (
    run_id VARCHAR PRIMARY KEY,
    group_id VARCHAR NOT NULL,
    topic VARCHAR NOT NULL,
    status VARCHAR NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'interrupted')),
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    owner_pid BIGINT NOT NULL,
    error_message VARCHAR,
    recovered_at TIMESTAMPTZ,
    recovery_reason VARCHAR
);

CREATE TABLE IF NOT EXISTS kafka_events (
    event_id VARCHAR PRIMARY KEY,
    payload_sha256 VARCHAR NOT NULL,
    payload_json VARCHAR NOT NULL,
    product_code VARCHAR NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    observed_date DATE NOT NULL,
    quantity BIGINT NOT NULL,
    first_topic VARCHAR NOT NULL,
    first_partition INTEGER NOT NULL,
    first_offset BIGINT NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS kafka_delivery_attempts (
    run_id VARCHAR NOT NULL,
    topic VARCHAR NOT NULL,
    partition INTEGER NOT NULL,
    kafka_offset BIGINT NOT NULL,
    event_id VARCHAR NOT NULL,
    payload_sha256 VARCHAR NOT NULL,
    disposition VARCHAR NOT NULL CHECK (disposition IN ('accepted', 'redelivery', 'conflict')),
    observed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (run_id, topic, partition, kafka_offset)
);

CREATE TABLE IF NOT EXISTS kafka_event_conflicts (
    run_id VARCHAR NOT NULL,
    topic VARCHAR NOT NULL,
    partition INTEGER NOT NULL,
    kafka_offset BIGINT NOT NULL,
    event_id VARCHAR NOT NULL,
    existing_payload_sha256 VARCHAR NOT NULL,
    incoming_payload_sha256 VARCHAR NOT NULL,
    incoming_payload_json VARCHAR NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (run_id, topic, partition, kafka_offset)
);

CREATE TABLE IF NOT EXISTS kafka_offset_commits (
    run_id VARCHAR NOT NULL,
    topic VARCHAR NOT NULL,
    partition INTEGER NOT NULL,
    committed_next_offset BIGINT NOT NULL,
    committed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (run_id, topic, partition, committed_next_offset)
);

CREATE TABLE IF NOT EXISTS kafka_sink_checkpoint (
    checkpoint_id INTEGER PRIMARY KEY CHECK (checkpoint_id = 1),
    cluster_id VARCHAR NOT NULL,
    group_id VARCHAR NOT NULL,
    topic VARCHAR NOT NULL,
    partition INTEGER NOT NULL CHECK (partition = 0),
    durable_next_offset BIGINT NOT NULL CHECK (durable_next_offset >= 0),
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE OR REPLACE VIEW kafka_product_daily AS
SELECT
    observed_date,
    product_code,
    SUM(quantity) AS signed_quantity_sum,
    COUNT(*)::BIGINT AS unique_event_count
FROM kafka_events
GROUP BY observed_date, product_code;
