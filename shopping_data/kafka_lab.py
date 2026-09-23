from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, TextIO

import duckdb

from .config import KAFKA_FIXTURE_BATCH_SQL, KAFKA_SINK_SCHEMA_SQL


class KafkaLabFailure(RuntimeError):
    """The isolated Kafka experiment could not produce trustworthy evidence."""


class KafkaSinkBusy(KafkaLabFailure):
    """Another local Kafka sink writer still owns the sink lock."""


class EventIdCollision(KafkaLabFailure):
    """One immutable event id was observed with different canonical content."""


class KafkaCheckpointMismatch(KafkaLabFailure):
    """Broker progress and the sink's durable stream position are incompatible."""


KafkaCheckpoint = Callable[[str, str, dict[str, Any]], None]

BIGINT_MIN = -(2**63)
BIGINT_MAX = 2**63 - 1
UTC_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)


@dataclass(frozen=True)
class CanonicalEvent:
    event_id: str
    product_code: str
    occurred_at: str
    occurred_datetime: datetime
    observed_date: date
    quantity: int
    payload_json: str
    payload_sha256: str


@dataclass(frozen=True)
class DeliveryOutcome:
    topic: str
    partition: int
    offset: int
    event_id: str
    payload_sha256: str
    disposition: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def canonicalize_event(value: dict[str, Any]) -> CanonicalEvent:
    required = {"event_id", "product_code", "occurred_at", "quantity"}
    if set(value) != required:
        missing = sorted(required - set(value))
        extra = sorted(set(value) - required)
        raise KafkaLabFailure(
            f"event fields must be exactly {sorted(required)}; missing={missing}, extra={extra}"
        )

    event_id = value["event_id"]
    product_code = value["product_code"]
    occurred_at = value["occurred_at"]
    quantity = value["quantity"]
    if not isinstance(event_id, str) or not event_id.strip():
        raise KafkaLabFailure("event_id must be a non-empty string")
    if not isinstance(product_code, str) or not product_code.strip():
        raise KafkaLabFailure("product_code must be a non-empty string")
    if not isinstance(occurred_at, str) or not UTC_TIMESTAMP_PATTERN.fullmatch(
        occurred_at
    ):
        raise KafkaLabFailure(
            "occurred_at must be a full UTC timestamp like 2026-09-22T10:00:00Z"
        )
    if not isinstance(quantity, int) or isinstance(quantity, bool):
        raise KafkaLabFailure("quantity must be a signed integer")
    if not BIGINT_MIN <= quantity <= BIGINT_MAX:
        raise KafkaLabFailure("quantity must fit the signed BIGINT range")
    try:
        occurred_datetime = datetime.fromisoformat(
            occurred_at.removesuffix("Z") + "+00:00"
        )
    except ValueError as exc:
        raise KafkaLabFailure(f"occurred_at is invalid: {occurred_at}") from exc
    if occurred_datetime.tzinfo is None or occurred_datetime.utcoffset() is None:
        raise KafkaLabFailure("occurred_at must resolve to a timezone-aware UTC value")

    canonical_payload = {
        "event_id": event_id,
        "occurred_at": occurred_at,
        "product_code": product_code,
        "quantity": quantity,
    }
    payload_json = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    return CanonicalEvent(
        event_id=event_id,
        product_code=product_code,
        occurred_at=occurred_at,
        occurred_datetime=occurred_datetime,
        observed_date=occurred_datetime.date(),
        quantity=quantity,
        payload_json=payload_json,
        payload_sha256=payload_sha256,
    )


def decode_event(payload: bytes | str) -> CanonicalEvent:
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise KafkaLabFailure("event payload is not UTF-8") from exc
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise KafkaLabFailure(f"event payload is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise KafkaLabFailure("event payload must be a JSON object")
    return canonicalize_event(value)


def _read_json_lines(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise KafkaLabFailure(f"{path.name}:{line_number} is invalid JSON") from exc
        if not isinstance(value, dict):
            raise KafkaLabFailure(f"{path.name}:{line_number} must be a JSON object")
        rows.append(value)
    return rows


def load_events(path: Path) -> dict[str, CanonicalEvent]:
    events: dict[str, CanonicalEvent] = {}
    for raw in _read_json_lines(path):
        event = canonicalize_event(raw)
        previous = events.get(event.event_id)
        if previous is not None:
            if previous.payload_sha256 != event.payload_sha256:
                raise EventIdCollision(
                    f"fixture event_id has conflicting content: {event.event_id}"
                )
            raise KafkaLabFailure(f"fixture repeats event_id: {event.event_id}")
        events[event.event_id] = event
    if not events:
        raise KafkaLabFailure("event fixture is empty")
    return events


def load_delivery_plan(path: Path) -> list[dict[str, str]]:
    deliveries: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in _read_json_lines(path):
        required = {"delivery_id", "phase", "event_id"}
        if set(raw) != required or not all(
            isinstance(raw[key], str) and raw[key] for key in required
        ):
            raise KafkaLabFailure(
                f"delivery fields must be non-empty strings: {sorted(required)}"
            )
        if raw["delivery_id"] in seen:
            raise KafkaLabFailure(f"delivery_id repeats: {raw['delivery_id']}")
        seen.add(raw["delivery_id"])
        deliveries.append({key: raw[key] for key in sorted(required)})
    return deliveries


def planned_payloads(
    events_path: Path,
    deliveries_path: Path,
    *,
    phase: str,
) -> list[dict[str, Any]]:
    events = load_events(events_path)
    selected: list[dict[str, Any]] = []
    for delivery in load_delivery_plan(deliveries_path):
        if delivery["phase"] != phase:
            continue
        event_id = delivery["event_id"]
        if event_id not in events:
            raise KafkaLabFailure(
                f"delivery {delivery['delivery_id']} references unknown event {event_id}"
            )
        selected.append(
            {
                "delivery_id": delivery["delivery_id"],
                "event": events[event_id],
            }
        )
    if not selected:
        raise KafkaLabFailure(f"no deliveries found for phase: {phase}")
    return selected


def initialize_sink(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(KAFKA_SINK_SCHEMA_SQL.read_text(encoding="utf-8"))


def _lock_path(database_path: Path) -> Path:
    return database_path.with_name(f"{database_path.name}.consumer.lock")


def _acquire_sink_lock(database_path: Path, owner_token: str) -> TextIO:
    path = _lock_path(database_path)
    lock_file = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_file.close()
        raise KafkaSinkBusy(f"another local Kafka sink writer holds {path}") from exc
    lock_file.seek(0)
    lock_file.truncate()
    json.dump(
        {
            "owner_pid": os.getpid(),
            "owner_token": owner_token,
            "acquired_at": utc_now().isoformat(timespec="seconds"),
        },
        lock_file,
    )
    lock_file.write("\n")
    lock_file.flush()
    os.fsync(lock_file.fileno())
    return lock_file


def _release_sink_lock(lock_file: TextIO) -> None:
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()


def _recover_interrupted_runs(connection: duckdb.DuckDBPyConnection) -> list[str]:
    run_ids = [
        row[0]
        for row in connection.execute(
            "SELECT run_id FROM kafka_consumer_runs WHERE status = 'running' ORDER BY run_id"
        ).fetchall()
    ]
    if not run_ids:
        return []
    recovered_at = utc_now()
    reason = (
        "previous consumer process ended after acquiring the exclusive sink lock; "
        "recovery compares the durable sink checkpoint with the Kafka group position"
    )
    connection.execute("BEGIN TRANSACTION")
    try:
        connection.executemany(
            """
            UPDATE kafka_consumer_runs
            SET status = 'interrupted', completed_at = ?, error_message = ?,
                recovered_at = ?, recovery_reason = ?
            WHERE run_id = ? AND status = 'running'
            """,
            [
                [recovered_at, reason, recovered_at, reason, run_id]
                for run_id in run_ids
            ],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return run_ids


def bind_or_validate_sink(
    connection: duckdb.DuckDBPyConnection,
    *,
    cluster_id: str,
    group_id: str,
    topic: str,
    partition: int,
    broker_committed_offset: int,
    low_offset: int,
    high_offset: int,
) -> int:
    """Bind an empty sink once, then fail closed on incompatible progress.

    The lab owns one cluster/topic/partition/group stream. A new sink may bind
    only before that group has advanced and while the complete partition still
    begins at offset zero. Existing sinks may be one position ahead of Kafka
    only for the deliberate sink-commit-before-offset-commit crash window.
    No offset reset or sink rebuild is performed here.
    """

    if not cluster_id:
        raise KafkaCheckpointMismatch("broker metadata did not provide a cluster id")
    if partition != 0:
        raise KafkaCheckpointMismatch("the current lab supports partition 0 only")
    if low_offset < 0 or high_offset < low_offset:
        raise KafkaCheckpointMismatch(
            f"invalid broker watermark range: low={low_offset}, high={high_offset}"
        )

    row = connection.execute(
        """
        SELECT cluster_id, group_id, topic, partition, durable_next_offset
        FROM kafka_sink_checkpoint
        WHERE checkpoint_id = 1
        """
    ).fetchone()
    if row is None:
        materialized_rows = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM kafka_events)
              + (SELECT COUNT(*) FROM kafka_delivery_attempts)
              + (SELECT COUNT(*) FROM kafka_event_conflicts)
              + (SELECT COUNT(*) FROM kafka_offset_commits)
            """
        ).fetchone()[0]
        if materialized_rows:
            raise KafkaCheckpointMismatch(
                "sink contains Kafka state but has no durable stream checkpoint"
            )
        if low_offset != 0:
            raise KafkaCheckpointMismatch(
                f"new sink cannot infer missing offsets before broker low watermark {low_offset}"
            )
        if broker_committed_offset > 0:
            raise KafkaCheckpointMismatch(
                "new sink is empty but the consumer group has already advanced to "
                f"offset {broker_committed_offset}"
            )
        connection.execute(
            """
            INSERT INTO kafka_sink_checkpoint
                (checkpoint_id, cluster_id, group_id, topic, partition,
                 durable_next_offset, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?)
            """,
            [cluster_id, group_id, topic, partition, low_offset, utc_now()],
        )
        return low_offset

    bound_cluster, bound_group, bound_topic, bound_partition, durable_next = row
    expected = (cluster_id, group_id, topic, partition)
    actual = (bound_cluster, bound_group, bound_topic, int(bound_partition))
    if actual != expected:
        raise KafkaCheckpointMismatch(
            "sink is bound to a different Kafka stream: "
            f"bound={actual}, requested={expected}"
        )

    durable_next = int(durable_next)
    if low_offset > durable_next:
        raise KafkaCheckpointMismatch(
            "broker log begins after the sink checkpoint: "
            f"low={low_offset}, sink={durable_next}"
        )
    if broker_committed_offset > durable_next:
        raise KafkaCheckpointMismatch(
            "broker consumer group is ahead of the sink: "
            f"broker={broker_committed_offset}, sink={durable_next}"
        )
    if high_offset < durable_next:
        raise KafkaCheckpointMismatch(
            "broker log ends before the sink checkpoint: "
            f"high={high_offset}, sink={durable_next}"
        )
    if broker_committed_offset < 0:
        safe_uncommitted_next = 1
        if durable_next > safe_uncommitted_next:
            raise KafkaCheckpointMismatch(
                "consumer group has no committed position but the sink is more than "
                "one event ahead; refusing to infer an offset reset"
            )
    elif durable_next - broker_committed_offset > 1:
        raise KafkaCheckpointMismatch(
            "sink is more than one event ahead of the broker group; refusing to "
            "infer a group reset"
        )
    return durable_next


def apply_delivery(
    connection: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    cluster_id: str,
    group_id: str,
    topic: str,
    partition: int,
    offset: int,
    payload: bytes | str,
) -> DeliveryOutcome:
    event = decode_event(payload)
    now = utc_now()
    committed = False
    connection.execute("BEGIN TRANSACTION")
    try:
        checkpoint = connection.execute(
            """
            SELECT cluster_id, group_id, topic, partition, durable_next_offset
            FROM kafka_sink_checkpoint
            WHERE checkpoint_id = 1
            """
        ).fetchone()
        if checkpoint is None:
            raise KafkaCheckpointMismatch("sink stream is not bound")
        bound = (
            checkpoint[0],
            checkpoint[1],
            checkpoint[2],
            int(checkpoint[3]),
        )
        requested = (cluster_id, group_id, topic, partition)
        if bound != requested:
            raise KafkaCheckpointMismatch(
                f"delivery stream does not match sink binding: {requested} != {bound}"
            )
        durable_next = int(checkpoint[4])
        if offset > durable_next:
            raise KafkaCheckpointMismatch(
                f"delivery gap: received offset {offset}, sink expects {durable_next}"
            )

        existing = connection.execute(
            """
            SELECT payload_sha256, first_topic, first_partition, first_offset
            FROM kafka_events WHERE event_id = ?
            """,
            [event.event_id],
        ).fetchone()
        if offset < durable_next:
            prior_delivery = connection.execute(
                """
                SELECT 1
                FROM kafka_delivery_attempts
                WHERE topic = ? AND partition = ? AND kafka_offset = ?
                  AND event_id = ? AND payload_sha256 = ?
                  AND disposition IN ('accepted', 'redelivery')
                LIMIT 1
                """,
                [
                    topic,
                    partition,
                    offset,
                    event.event_id,
                    event.payload_sha256,
                ],
            ).fetchone()
            if existing is None or prior_delivery is None:
                raise KafkaCheckpointMismatch(
                    "sink checkpoint says the delivery was processed but the exact "
                    "event and Kafka position are missing: "
                    f"offset={offset}, sink={durable_next}"
                )
        if existing is None:
            disposition = "accepted"
            connection.execute(
                """
                INSERT INTO kafka_events
                    (event_id, payload_sha256, payload_json, product_code,
                     occurred_at, observed_date, quantity, first_topic,
                     first_partition, first_offset, registered_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    event.event_id,
                    event.payload_sha256,
                    event.payload_json,
                    event.product_code,
                    event.occurred_datetime,
                    event.observed_date,
                    event.quantity,
                    topic,
                    partition,
                    offset,
                    now,
                ],
            )
        elif existing[0] == event.payload_sha256:
            disposition = "redelivery"
        else:
            disposition = "conflict"

        connection.execute(
            """
            INSERT INTO kafka_delivery_attempts
                (run_id, topic, partition, kafka_offset, event_id,
                 payload_sha256, disposition, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_id,
                topic,
                partition,
                offset,
                event.event_id,
                event.payload_sha256,
                disposition,
                now,
            ],
        )
        if disposition == "conflict":
            connection.execute(
                """
                INSERT INTO kafka_event_conflicts
                    (run_id, topic, partition, kafka_offset, event_id,
                     existing_payload_sha256, incoming_payload_sha256,
                     incoming_payload_json, observed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    run_id,
                    topic,
                    partition,
                    offset,
                    event.event_id,
                    existing[0],
                    event.payload_sha256,
                    event.payload_json,
                    now,
                ],
            )
        elif offset == durable_next:
            connection.execute(
                """
                UPDATE kafka_sink_checkpoint
                SET durable_next_offset = ?, updated_at = ?
                WHERE checkpoint_id = 1
                """,
                [offset + 1, now],
            )
        connection.execute("COMMIT")
        committed = True
    except Exception:
        if not committed:
            connection.execute("ROLLBACK")
        raise

    outcome = DeliveryOutcome(
        topic=topic,
        partition=partition,
        offset=offset,
        event_id=event.event_id,
        payload_sha256=event.payload_sha256,
        disposition=disposition,
    )
    if disposition == "conflict":
        raise EventIdCollision(
            f"event_id {event.event_id} has different content at "
            f"{topic}[{partition}] offset {offset}"
        )
    return outcome


def consumer_configuration(bootstrap_servers: str, group_id: str) -> dict[str, Any]:
    return {
        "bootstrap.servers": bootstrap_servers,
        "group.id": group_id,
        "client.id": "shopping-kafka-sink",
        "enable.auto.commit": False,
        "enable.auto.offset.store": False,
        "auto.offset.reset": "earliest",
        "session.timeout.ms": 6000,
        "max.poll.interval.ms": 60000,
    }


def _client_types():
    try:
        from confluent_kafka import Consumer, KafkaError, Producer, TopicPartition
        from confluent_kafka.admin import AdminClient, NewTopic
    except ImportError as exc:
        raise KafkaLabFailure(
            "confluent_kafka is required for the real Kafka lab"
        ) from exc
    return Consumer, KafkaError, Producer, TopicPartition, AdminClient, NewTopic


def create_topic(bootstrap_servers: str, topic: str) -> None:
    _, _, _, _, AdminClient, NewTopic = _client_types()
    admin = AdminClient(
        {"bootstrap.servers": bootstrap_servers, "client.id": "shopping-topic-admin"}
    )
    future = admin.create_topics(
        [NewTopic(topic, num_partitions=1, replication_factor=1)]
    )[topic]
    future.result(timeout=15)


def produce_payloads(
    bootstrap_servers: str,
    topic: str,
    payloads: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    _, _, Producer, _, _, _ = _client_types()
    producer = Producer(
        {
            "bootstrap.servers": bootstrap_servers,
            "client.id": "shopping-event-producer",
            "acks": "all",
            "enable.idempotence": True,
        }
    )
    delivered: list[dict[str, Any]] = []
    errors: list[str] = []
    for item in payloads:
        event: CanonicalEvent = item["event"]
        label = str(item["delivery_id"])

        def on_delivery(error, message, *, delivery_id=label, source_event=event):
            if error is not None:
                errors.append(f"{delivery_id}: {error}")
                return
            delivered.append(
                {
                    "delivery_id": delivery_id,
                    "event_id": source_event.event_id,
                    "payload_sha256": source_event.payload_sha256,
                    "topic": message.topic(),
                    "partition": message.partition(),
                    "offset": message.offset(),
                }
            )

        producer.produce(
            topic,
            key=event.event_id.encode("utf-8"),
            value=event.payload_json.encode("utf-8"),
            partition=0,
            on_delivery=on_delivery,
        )
        producer.poll(0)
    remaining = producer.flush(15)
    if remaining or errors or len(delivered) != len(payloads):
        raise KafkaLabFailure(
            f"producer delivery failed: remaining={remaining}, errors={errors}, "
            f"delivered={len(delivered)}/{len(payloads)}"
        )
    return sorted(delivered, key=lambda item: (item["partition"], item["offset"]))


def committed_next_offset(
    bootstrap_servers: str,
    group_id: str,
    topic: str,
    partition: int = 0,
) -> int:
    Consumer, _, _, TopicPartition, _, _ = _client_types()
    consumer = Consumer(consumer_configuration(bootstrap_servers, group_id))
    try:
        return int(
            _read_committed_offset(
                consumer,
                TopicPartition(topic, partition),
                timeout_seconds=15,
            )
        )
    finally:
        # Official client behavior: enable.auto.commit=False makes close() leave
        # the group and release resources without committing an offset.
        consumer.close()


def _read_committed_offset(
    consumer,
    partition,
    *,
    timeout_seconds: float,
) -> int:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            committed = consumer.committed([partition], timeout=2)
            if len(committed) != 1:
                raise KafkaLabFailure(
                    f"committed offset query returned {len(committed)} partitions"
                )
            result = committed[0]
            if result.error is not None:
                raise KafkaLabFailure(
                    f"committed offset query failed for "
                    f"{result.topic}[{result.partition}]: {result.error}"
                )
            return int(result.offset)
        except Exception as exc:
            # A newly created one-node lab may be serving metadata before its
            # __consumer_offsets coordinator has finished loading. Retry only
            # within the explicit readiness deadline.
            last_error = exc
            time.sleep(0.1)
    raise KafkaLabFailure(
        f"consumer group coordinator was not ready within {timeout_seconds}s: {last_error}"
    )


def _read_cluster_id(consumer, topic: str) -> str:
    metadata = consumer.list_topics(topic=topic, timeout=10)
    cluster_id = getattr(metadata, "cluster_id", None)
    if not isinstance(cluster_id, str) or not cluster_id:
        raise KafkaCheckpointMismatch(
            "broker metadata did not return a stable cluster id for stream binding"
        )
    return cluster_id


def consume_available(
    *,
    bootstrap_servers: str,
    topic: str,
    group_id: str,
    database_path: Path,
    checkpoint: KafkaCheckpoint | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    Consumer, KafkaError, _, TopicPartition, _, _ = _client_types()
    database_path = database_path.resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    owner_token = f"{os.getpid()}:{uuid.uuid4()}"
    lock_file = _acquire_sink_lock(database_path, owner_token)
    connection: duckdb.DuckDBPyConnection | None = None
    consumer = None
    run_id = str(uuid.uuid4())
    started_at = utc_now()
    processed: list[dict[str, Any]] = []
    try:
        connection = duckdb.connect(str(database_path))
        initialize_sink(connection)
        recovered_run_ids = _recover_interrupted_runs(connection)
        connection.execute(
            """
            INSERT INTO kafka_consumer_runs
                (run_id, group_id, topic, status, started_at, owner_pid)
            VALUES (?, ?, ?, 'running', ?, ?)
            """,
            [run_id, group_id, topic, started_at, os.getpid()],
        )
        consumer = Consumer(consumer_configuration(bootstrap_servers, group_id))
        partition = TopicPartition(topic, 0)
        cluster_id = _read_cluster_id(consumer, topic)
        low_offset, target_high = consumer.get_watermark_offsets(
            partition, timeout=10
        )
        before_offset = _read_committed_offset(
            consumer,
            partition,
            timeout_seconds=min(timeout_seconds, 15),
        )
        durable_before = bind_or_validate_sink(
            connection,
            cluster_id=cluster_id,
            group_id=group_id,
            topic=topic,
            partition=0,
            broker_committed_offset=int(before_offset),
            low_offset=int(low_offset),
            high_offset=int(target_high),
        )
        caught_up = before_offset >= target_high or (
            target_high == 0 and durable_before == 0 and before_offset < 0
        )
        if caught_up:
            connection.execute(
                """
                UPDATE kafka_consumer_runs
                SET status = 'succeeded', completed_at = ?
                WHERE run_id = ?
                """,
                [utc_now(), run_id],
            )
            return {
                "run_id": run_id,
                "recovered_run_ids": recovered_run_ids,
                "target_high_offset": int(target_high),
                "committed_before": int(before_offset),
                "committed_after": int(before_offset),
                "sink_next_before": durable_before,
                "sink_next_after": durable_before,
                "deliveries": [],
            }

        consumer.subscribe([topic])
        deadline = time.monotonic() + timeout_seconds
        committed_after = int(before_offset)
        while committed_after < target_high:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise KafkaLabFailure(
                    f"consumer did not reach high offset {target_high} before timeout"
                )
            message = consumer.poll(min(1.0, remaining))
            if message is None:
                continue
            if message.error():
                if message.error().code() == KafkaError._PARTITION_EOF:
                    continue
                raise KafkaLabFailure(f"consumer error: {message.error()}")
            if message.topic() != topic or message.partition() != 0:
                raise KafkaLabFailure(
                    f"unexpected delivery position: {message.topic()}[{message.partition()}]"
                )

            outcome = apply_delivery(
                connection,
                run_id=run_id,
                cluster_id=cluster_id,
                group_id=group_id,
                topic=message.topic(),
                partition=message.partition(),
                offset=message.offset(),
                payload=message.value(),
            )
            outcome_payload = asdict(outcome)
            processed.append(outcome_payload)
            if checkpoint is not None:
                checkpoint(
                    "after_sink_commit_before_offset_commit",
                    run_id,
                    outcome_payload,
                )

            committed = consumer.commit(message=message, asynchronous=False)
            if not committed or len(committed) != 1 or committed[0].error is not None:
                raise KafkaLabFailure(f"synchronous offset commit failed: {committed}")
            committed_after = int(committed[0].offset)
            expected_next = message.offset() + 1
            if committed_after != expected_next:
                raise KafkaLabFailure(
                    f"committed offset {committed_after} != expected next {expected_next}"
                )
            if checkpoint is not None:
                checkpoint(
                    "after_offset_commit_before_local_audit",
                    run_id,
                    outcome_payload,
                )
            connection.execute(
                """
                INSERT INTO kafka_offset_commits
                    (run_id, topic, partition, committed_next_offset, committed_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [run_id, topic, 0, committed_after, utc_now()],
            )

        connection.execute(
            """
            UPDATE kafka_consumer_runs
            SET status = 'succeeded', completed_at = ?
            WHERE run_id = ?
            """,
            [utc_now(), run_id],
        )
        return {
            "run_id": run_id,
            "recovered_run_ids": recovered_run_ids,
            "target_high_offset": int(target_high),
            "committed_before": int(before_offset),
            "committed_after": committed_after,
            "sink_next_before": durable_before,
            "sink_next_after": int(
                connection.execute(
                    """
                    SELECT durable_next_offset FROM kafka_sink_checkpoint
                    WHERE checkpoint_id = 1
                    """
                ).fetchone()[0]
            ),
            "deliveries": processed,
        }
    except Exception as exc:
        if connection is not None:
            try:
                connection.execute("ROLLBACK")
            except duckdb.TransactionException:
                pass
            connection.execute(
                """
                UPDATE kafka_consumer_runs
                SET status = 'failed', completed_at = ?, error_message = ?
                WHERE run_id = ? AND status = 'running'
                """,
                [utc_now(), str(exc), run_id],
            )
        raise
    finally:
        if consumer is not None:
            consumer.close()
        if connection is not None:
            connection.close()
        _release_sink_lock(lock_file)


def load_sink_snapshot(database_path: Path) -> dict[str, Any]:
    connection = duckdb.connect(str(database_path.resolve()), read_only=True)
    try:
        aggregates = [
            {
                "observed_date": row[0].isoformat(),
                "product_code": row[1],
                "signed_quantity_sum": int(row[2]),
                "unique_event_count": int(row[3]),
            }
            for row in connection.execute(
                """
                SELECT observed_date, product_code, signed_quantity_sum,
                       unique_event_count
                FROM kafka_product_daily
                ORDER BY observed_date, product_code
                """
            ).fetchall()
        ]
        runs = [
            {
                "run_id": row[0],
                "status": row[1],
                "error_message": row[2],
                "recovered_at": row[3].isoformat(timespec="seconds") if row[3] else None,
            }
            for row in connection.execute(
                """
                SELECT run_id, status, error_message, recovered_at
                FROM kafka_consumer_runs ORDER BY rowid
                """
            ).fetchall()
        ]
        attempts = [
            {
                "run_id": row[0],
                "topic": row[1],
                "partition": int(row[2]),
                "offset": int(row[3]),
                "event_id": row[4],
                "payload_sha256": row[5],
                "disposition": row[6],
            }
            for row in connection.execute(
                """
                SELECT run_id, topic, partition, kafka_offset, event_id,
                       payload_sha256, disposition
                FROM kafka_delivery_attempts ORDER BY rowid
                """
            ).fetchall()
        ]
        event_count = int(
            connection.execute("SELECT COUNT(*) FROM kafka_events").fetchone()[0]
        )
        conflict_count = int(
            connection.execute("SELECT COUNT(*) FROM kafka_event_conflicts").fetchone()[0]
        )
        checkpoint_row = connection.execute(
            """
            SELECT cluster_id, group_id, topic, partition, durable_next_offset
            FROM kafka_sink_checkpoint WHERE checkpoint_id = 1
            """
        ).fetchone()
        checkpoint = (
            {
                "cluster_id": checkpoint_row[0],
                "group_id": checkpoint_row[1],
                "topic": checkpoint_row[2],
                "partition": int(checkpoint_row[3]),
                "durable_next_offset": int(checkpoint_row[4]),
            }
            if checkpoint_row is not None
            else None
        )
        offset_commits = [
            {
                "run_id": row[0],
                "topic": row[1],
                "partition": int(row[2]),
                "committed_next_offset": int(row[3]),
            }
            for row in connection.execute(
                """
                SELECT run_id, topic, partition, committed_next_offset
                FROM kafka_offset_commits ORDER BY rowid
                """
            ).fetchall()
        ]
    finally:
        connection.close()
    return {
        "event_count": event_count,
        "conflict_count": conflict_count,
        "aggregates": aggregates,
        "runs": runs,
        "delivery_attempts": attempts,
        "checkpoint": checkpoint,
        "offset_commits": offset_commits,
    }


def independent_python_batch(events: list[CanonicalEvent]) -> list[dict[str, Any]]:
    totals: dict[tuple[date, str], dict[str, int]] = {}
    seen: dict[str, str] = {}
    for event in events:
        previous = seen.get(event.event_id)
        if previous is not None:
            if previous != event.payload_sha256:
                raise EventIdCollision(
                    f"independent batch collision: {event.event_id}"
                )
            continue
        seen[event.event_id] = event.payload_sha256
        key = (event.observed_date, event.product_code)
        bucket = totals.setdefault(key, {"quantity": 0, "events": 0})
        bucket["quantity"] += event.quantity
        bucket["events"] += 1
    return [
        {
            "observed_date": observed_date.isoformat(),
            "product_code": product_code,
            "signed_quantity_sum": value["quantity"],
            "unique_event_count": value["events"],
        }
        for (observed_date, product_code), value in sorted(totals.items())
    ]


def independent_sql_batch(events: list[CanonicalEvent]) -> list[dict[str, Any]]:
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            """
            CREATE TABLE fixture_events (
                event_id VARCHAR,
                product_code VARCHAR,
                occurred_at VARCHAR,
                quantity BIGINT
            )
            """
        )
        unique: dict[str, CanonicalEvent] = {}
        for event in events:
            previous = unique.get(event.event_id)
            if previous is not None and previous.payload_sha256 != event.payload_sha256:
                raise EventIdCollision(
                    f"independent SQL batch collision: {event.event_id}"
                )
            unique[event.event_id] = event
        connection.executemany(
            "INSERT INTO fixture_events VALUES (?, ?, ?, ?)",
            [
                [event.event_id, event.product_code, event.occurred_at, event.quantity]
                for event in unique.values()
            ],
        )
        rows = connection.execute(
            KAFKA_FIXTURE_BATCH_SQL.read_text(encoding="utf-8")
        ).fetchall()
    finally:
        connection.close()
    return [
        {
            "observed_date": row[0].isoformat(),
            "product_code": row[1],
            "signed_quantity_sum": int(row[2]),
            "unique_event_count": int(row[3]),
        }
        for row in rows
    ]
