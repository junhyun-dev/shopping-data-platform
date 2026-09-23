from __future__ import annotations

import json
from datetime import timezone
from pathlib import Path

import duckdb
import pytest

from shopping_data.config import KAFKA_DELIVERIES, KAFKA_EVENTS
from shopping_data.kafka_lab import (
    BIGINT_MAX,
    BIGINT_MIN,
    EventIdCollision,
    KafkaCheckpointMismatch,
    KafkaLabFailure,
    _read_committed_offset,
    apply_delivery,
    bind_or_validate_sink,
    canonicalize_event,
    consumer_configuration,
    independent_python_batch,
    independent_sql_batch,
    initialize_sink,
    load_events,
    load_sink_snapshot,
    planned_payloads,
)

TEST_CLUSTER = "test-cluster"
TEST_GROUP = "test-group"
TEST_TOPIC = "test-topic"


def add_run(connection: duckdb.DuckDBPyConnection, run_id: str) -> None:
    connection.execute(
        """
        INSERT INTO kafka_consumer_runs
            (run_id, group_id, topic, status, started_at, completed_at, owner_pid)
        VALUES (?, 'test-group', 'test-topic', 'succeeded', now(), now(), 1)
        """,
        [run_id],
    )
    bind_or_validate_sink(
        connection,
        cluster_id=TEST_CLUSTER,
        group_id=TEST_GROUP,
        topic=TEST_TOPIC,
        partition=0,
        broker_committed_offset=-1001,
        low_offset=0,
        high_offset=100,
    )


def payload(event_id: str, occurred_at: str, quantity: int) -> str:
    return json.dumps(
        {
            "event_id": event_id,
            "product_code": "W-104",
            "occurred_at": occurred_at,
            "quantity": quantity,
        }
    )


def test_sink_redelivery_is_non_amplifying_and_collision_stops(tmp_path: Path) -> None:
    database = tmp_path / "sink.duckdb"
    connection = duckdb.connect(str(database))
    try:
        initialize_sink(connection)
        add_run(connection, "run-1")
        first = apply_delivery(
            connection,
            run_id="run-1",
            cluster_id=TEST_CLUSTER,
            group_id=TEST_GROUP,
            topic=TEST_TOPIC,
            partition=0,
            offset=0,
            payload=payload("evt-1", "2026-09-22T10:00:00Z", 3),
        )
        duplicate = apply_delivery(
            connection,
            run_id="run-1",
            cluster_id=TEST_CLUSTER,
            group_id=TEST_GROUP,
            topic=TEST_TOPIC,
            partition=0,
            offset=1,
            payload=payload("evt-1", "2026-09-22T10:00:00Z", 3),
        )
        with pytest.raises(EventIdCollision, match="different content"):
            apply_delivery(
                connection,
                run_id="run-1",
                cluster_id=TEST_CLUSTER,
                group_id=TEST_GROUP,
                topic=TEST_TOPIC,
                partition=0,
                offset=2,
                payload=payload("evt-1", "2026-09-22T10:00:00Z", 99),
            )
    finally:
        connection.close()

    snapshot = load_sink_snapshot(database)
    assert first.disposition == "accepted"
    assert duplicate.disposition == "redelivery"
    assert snapshot["event_count"] == 1
    assert snapshot["conflict_count"] == 1
    assert snapshot["aggregates"] == [
        {
            "observed_date": "2026-09-22",
            "product_code": "W-104",
            "signed_quantity_sum": 3,
            "unique_event_count": 1,
        }
    ]
    assert [item["disposition"] for item in snapshot["delivery_attempts"]] == [
        "accepted",
        "redelivery",
        "conflict",
    ]


def test_occurrence_time_not_delivery_order_controls_daily_group(tmp_path: Path) -> None:
    database = tmp_path / "sink.duckdb"
    connection = duckdb.connect(str(database))
    try:
        initialize_sink(connection)
        add_run(connection, "run-1")
        apply_delivery(
            connection,
            run_id="run-1",
            cluster_id=TEST_CLUSTER,
            group_id=TEST_GROUP,
            topic=TEST_TOPIC,
            partition=0,
            offset=0,
            payload=payload("newer-first", "2026-09-22T10:00:00Z", 3),
        )
        apply_delivery(
            connection,
            run_id="run-1",
            cluster_id=TEST_CLUSTER,
            group_id=TEST_GROUP,
            topic=TEST_TOPIC,
            partition=0,
            offset=1,
            payload=payload("older-second", "2026-09-22T09:00:00Z", 2),
        )
        apply_delivery(
            connection,
            run_id="run-1",
            cluster_id=TEST_CLUSTER,
            group_id=TEST_GROUP,
            topic=TEST_TOPIC,
            partition=0,
            offset=2,
            payload=payload("late-day", "2026-09-21T23:55:00Z", 4),
        )
    finally:
        connection.close()

    assert load_sink_snapshot(database)["aggregates"] == [
        {
            "observed_date": "2026-09-21",
            "product_code": "W-104",
            "signed_quantity_sum": 4,
            "unique_event_count": 1,
        },
        {
            "observed_date": "2026-09-22",
            "product_code": "W-104",
            "signed_quantity_sum": 5,
            "unique_event_count": 2,
        },
    ]


def test_fixture_independent_python_and_sql_batches_agree() -> None:
    events = load_events(KAFKA_EVENTS)
    delivered = [
        item["event"]
        for phase in ("initial", "late")
        for item in planned_payloads(KAFKA_EVENTS, KAFKA_DELIVERIES, phase=phase)
    ]
    expected = [
        {
            "observed_date": "2026-09-21",
            "product_code": "W-104",
            "signed_quantity_sum": 4,
            "unique_event_count": 1,
        },
        {
            "observed_date": "2026-09-22",
            "product_code": "B-208",
            "signed_quantity_sum": -1,
            "unique_event_count": 1,
        },
        {
            "observed_date": "2026-09-22",
            "product_code": "W-104",
            "signed_quantity_sum": 5,
            "unique_event_count": 2,
        },
    ]
    assert set(events) == {"evt-1001", "evt-1002", "evt-1003", "evt-2001"}
    assert independent_python_batch(delivered) == expected
    assert independent_sql_batch(delivered) == expected


def test_consumer_configuration_disables_both_automatic_offset_paths() -> None:
    config = consumer_configuration("127.0.0.1:19092", "test-group")
    assert config["enable.auto.commit"] is False
    assert config["enable.auto.offset.store"] is False
    assert config["auto.offset.reset"] == "earliest"


def test_event_content_fingerprint_is_canonical_and_utc_date_is_explicit() -> None:
    first = canonicalize_event(
        {
            "quantity": 3,
            "occurred_at": "2026-09-22T10:00:00Z",
            "product_code": "W-104",
            "event_id": "evt-1",
        }
    )
    second = canonicalize_event(
        {
            "event_id": "evt-1",
            "product_code": "W-104",
            "occurred_at": "2026-09-22T10:00:00Z",
            "quantity": 3,
        }
    )
    assert first.payload_sha256 == second.payload_sha256
    assert first.observed_date.isoformat() == "2026-09-22"
    assert first.occurred_datetime.tzinfo is not None
    assert first.occurred_datetime.utcoffset() == timezone.utc.utcoffset(first.occurred_datetime)


@pytest.mark.parametrize(
    "occurred_at",
    [
        "2026-09-22Z",
        "2026-09-22T10:00:00",
        "2026-09-22T25:00:00Z",
        "not-a-time",
    ],
)
def test_event_time_rejects_date_only_naive_and_invalid_values(occurred_at: str) -> None:
    with pytest.raises(KafkaLabFailure, match="occurred_at"):
        canonicalize_event(
            {
                "event_id": "evt-time",
                "product_code": "W-104",
                "occurred_at": occurred_at,
                "quantity": 1,
            }
        )


@pytest.mark.parametrize("quantity", [BIGINT_MIN - 1, BIGINT_MAX + 1])
def test_event_quantity_rejects_values_outside_signed_bigint(quantity: int) -> None:
    with pytest.raises(KafkaLabFailure, match="BIGINT"):
        canonicalize_event(
            {
                "event_id": "evt-quantity",
                "product_code": "W-104",
                "occurred_at": "2026-09-22T10:00:00Z",
                "quantity": quantity,
            }
        )


def test_bigint_boundary_events_keep_exact_hugeint_sum(tmp_path: Path) -> None:
    database = tmp_path / "wide-sum.duckdb"
    connection = duckdb.connect(str(database))
    try:
        initialize_sink(connection)
        add_run(connection, "run-wide")
        for offset, event_id in enumerate(("max-1", "max-2")):
            apply_delivery(
                connection,
                run_id="run-wide",
                cluster_id=TEST_CLUSTER,
                group_id=TEST_GROUP,
                topic=TEST_TOPIC,
                partition=0,
                offset=offset,
                payload=payload(
                    event_id,
                    "2026-09-22T10:00:00Z",
                    BIGINT_MAX,
                ),
            )
    finally:
        connection.close()

    expected_sum = BIGINT_MAX * 2
    assert load_sink_snapshot(database)["aggregates"] == [
        {
            "observed_date": "2026-09-22",
            "product_code": "W-104",
            "signed_quantity_sum": expected_sum,
            "unique_event_count": 2,
        }
    ]
    events = [
        canonicalize_event(
            {
                "event_id": event_id,
                "product_code": "W-104",
                "occurred_at": "2026-09-22T10:00:00Z",
                "quantity": BIGINT_MAX,
            }
        )
        for event_id in ("max-1", "max-2")
    ]
    assert independent_python_batch(events) == independent_sql_batch(events)


def test_empty_sink_rejects_an_already_advanced_consumer_group() -> None:
    connection = duckdb.connect(":memory:")
    try:
        initialize_sink(connection)
        with pytest.raises(KafkaCheckpointMismatch, match="empty.*advanced"):
            bind_or_validate_sink(
                connection,
                cluster_id=TEST_CLUSTER,
                group_id=TEST_GROUP,
                topic=TEST_TOPIC,
                partition=0,
                broker_committed_offset=1,
                low_offset=0,
                high_offset=1,
            )
        assert connection.execute(
            "SELECT COUNT(*) FROM kafka_sink_checkpoint"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_sink_binding_allows_one_uncommitted_redelivery_but_rejects_drift() -> None:
    connection = duckdb.connect(":memory:")
    try:
        initialize_sink(connection)
        add_run(connection, "run-binding")
        apply_delivery(
            connection,
            run_id="run-binding",
            cluster_id=TEST_CLUSTER,
            group_id=TEST_GROUP,
            topic=TEST_TOPIC,
            partition=0,
            offset=0,
            payload=payload("evt-1", "2026-09-22T10:00:00Z", 3),
        )
        assert bind_or_validate_sink(
            connection,
            cluster_id=TEST_CLUSTER,
            group_id=TEST_GROUP,
            topic=TEST_TOPIC,
            partition=0,
            broker_committed_offset=-1001,
            low_offset=0,
            high_offset=1,
        ) == 1
        with pytest.raises(KafkaCheckpointMismatch, match="ahead of the sink"):
            bind_or_validate_sink(
                connection,
                cluster_id=TEST_CLUSTER,
                group_id=TEST_GROUP,
                topic=TEST_TOPIC,
                partition=0,
                broker_committed_offset=2,
                low_offset=0,
                high_offset=2,
            )
        with pytest.raises(KafkaCheckpointMismatch, match="different Kafka stream"):
            bind_or_validate_sink(
                connection,
                cluster_id="other-cluster",
                group_id=TEST_GROUP,
                topic=TEST_TOPIC,
                partition=0,
                broker_committed_offset=0,
                low_offset=0,
                high_offset=1,
            )
        with pytest.raises(KafkaCheckpointMismatch, match="log begins after"):
            bind_or_validate_sink(
                connection,
                cluster_id=TEST_CLUSTER,
                group_id=TEST_GROUP,
                topic=TEST_TOPIC,
                partition=0,
                broker_committed_offset=0,
                low_offset=2,
                high_offset=2,
            )
    finally:
        connection.close()


def test_redelivery_requires_the_exact_prior_kafka_position() -> None:
    connection = duckdb.connect(":memory:")
    try:
        initialize_sink(connection)
        add_run(connection, "run-position")
        apply_delivery(
            connection,
            run_id="run-position",
            cluster_id=TEST_CLUSTER,
            group_id=TEST_GROUP,
            topic=TEST_TOPIC,
            partition=0,
            offset=0,
            payload=payload("evt-1", "2026-09-22T10:00:00Z", 3),
        )
        connection.execute(
            "DELETE FROM kafka_delivery_attempts WHERE kafka_offset = 0"
        )
        with pytest.raises(KafkaCheckpointMismatch, match="exact event and Kafka position"):
            apply_delivery(
                connection,
                run_id="run-position",
                cluster_id=TEST_CLUSTER,
                group_id=TEST_GROUP,
                topic=TEST_TOPIC,
                partition=0,
                offset=0,
                payload=payload("evt-1", "2026-09-22T10:00:00Z", 3),
            )
    finally:
        connection.close()


def test_committed_offset_partition_error_is_not_used_as_progress() -> None:
    class ReturnedPartition:
        topic = TEST_TOPIC
        partition = 0
        offset = 7
        error = RuntimeError("partition failure")

    class Consumer:
        def committed(self, partitions, timeout):
            return [ReturnedPartition()]

    with pytest.raises(KafkaLabFailure, match="partition failure"):
        _read_committed_offset(Consumer(), object(), timeout_seconds=0.01)
