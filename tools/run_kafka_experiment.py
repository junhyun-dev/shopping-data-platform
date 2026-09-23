from __future__ import annotations

import argparse
import json
import os
import select
import signal
import subprocess
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from shopping_data.config import (
    KAFKA_CONFLICT_EVENT,
    KAFKA_DELIVERIES,
    KAFKA_EVENTS,
)
from shopping_data.kafka_lab import (
    EventIdCollision,
    KafkaCheckpointMismatch,
    canonicalize_event,
    committed_next_offset,
    consume_available,
    create_topic,
    independent_python_batch,
    independent_sql_batch,
    load_events,
    load_sink_snapshot,
    planned_payloads,
    produce_payloads,
)
from shopping_data.kafka_runtime import load_runtime_config, start_local_broker


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the isolated one-broker Kafka redelivery experiment"
    )
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--broker-port", type=int, default=19092)
    parser.add_argument("--controller-port", type=int, default=19093)
    return parser


def start_crash_consumer(
    *,
    bootstrap_servers: str,
    topic: str,
    group_id: str,
    database: Path,
    checkpoint: str = "after_sink_commit_before_offset_commit",
) -> tuple[subprocess.Popen[str], dict]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    process = subprocess.Popen(
        [
            sys.executable,
            str(PROJECT_ROOT / "tests/kafka_crash_consumer.py"),
            "--bootstrap-servers",
            bootstrap_servers,
            "--topic",
            topic,
            "--group-id",
            group_id,
            "--database",
            str(database),
            "--checkpoint",
            checkpoint,
        ],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    ready, _, _ = select.select([process.stdout], [], [], 30)
    if not ready:
        process.kill()
        stdout, stderr = process.communicate(timeout=10)
        raise RuntimeError(
            f"consumer did not reach sink/offset barrier; stdout={stdout!r}, stderr={stderr!r}"
        )
    barrier_line = process.stdout.readline()
    if not barrier_line:
        process.wait(timeout=10)
        _, stderr = process.communicate(timeout=10)
        raise RuntimeError(
            f"consumer exited before barrier with {process.returncode}; stderr={stderr!r}"
        )
    barrier = json.loads(barrier_line)
    if process.poll() is not None:
        stdout, stderr = process.communicate(timeout=10)
        raise RuntimeError(
            f"consumer exited before forced termination: {barrier}, {stdout!r}, {stderr!r}"
        )
    return process, barrier


def kill_owned_process(process: subprocess.Popen[str]) -> None:
    process.kill()
    process.wait(timeout=10)
    if process.returncode != -signal.SIGKILL:
        raise RuntimeError(f"unexpected consumer return code: {process.returncode}")


def write_result(path: Path, value: dict) -> None:
    encoded = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    args = build_parser().parse_args()
    run_root = args.run_root.resolve()
    broker = start_local_broker(
        run_root=run_root,
        broker_port=args.broker_port,
        controller_port=args.controller_port,
    )
    topic = f"shopping-events-{uuid.uuid4().hex[:10]}"
    group_id = f"shopping-sink-{uuid.uuid4().hex[:10]}"
    sink_database = run_root / "sink.duckdb"
    result: dict = {
        "run_root": str(run_root),
        "broker_pid": broker.process.pid,
        "broker_log": str(broker.log_path),
        "bootstrap_servers": broker.bootstrap_servers,
        "cluster_id": broker.cluster_id,
        "topic": topic,
        "partition_count": 1,
        "group_id": group_id,
        "sink_database": str(sink_database),
        "runtime": load_runtime_config(),
    }
    try:
        create_topic(broker.bootstrap_servers, topic)
        initial_plan = planned_payloads(
            KAFKA_EVENTS, KAFKA_DELIVERIES, phase="initial"
        )
        initial_positions = produce_payloads(
            broker.bootstrap_servers, topic, initial_plan
        )
        if [item["offset"] for item in initial_positions] != [0, 1, 2, 3]:
            raise RuntimeError(f"unexpected initial offsets: {initial_positions}")

        consumer, barrier = start_crash_consumer(
            bootstrap_servers=broker.bootstrap_servers,
            topic=topic,
            group_id=group_id,
            database=sink_database,
        )
        try:
            if barrier["delivery"]["offset"] != 0:
                raise RuntimeError(f"unexpected barrier delivery: {barrier}")
            kill_owned_process(consumer)
        finally:
            if consumer.poll() is None:
                consumer.kill()
                consumer.wait(timeout=10)

        committed_after_kill = committed_next_offset(
            broker.bootstrap_servers, group_id, topic
        )
        snapshot_after_kill = load_sink_snapshot(sink_database)
        if committed_after_kill >= 0:
            raise RuntimeError(
                f"offset advanced before explicit commit: {committed_after_kill}"
            )
        if snapshot_after_kill["event_count"] != 1:
            raise RuntimeError(f"sink commit was not preserved: {snapshot_after_kill}")

        resumed = consume_available(
            bootstrap_servers=broker.bootstrap_servers,
            topic=topic,
            group_id=group_id,
            database_path=sink_database,
            timeout_seconds=35,
        )
        initial_snapshot = load_sink_snapshot(sink_database)
        if resumed["committed_after"] != 4:
            raise RuntimeError(f"initial restart did not commit offset 4: {resumed}")
        offset_zero_attempts = [
            item
            for item in initial_snapshot["delivery_attempts"]
            if item["offset"] == 0
        ]
        if [item["disposition"] for item in offset_zero_attempts] != [
            "accepted",
            "redelivery",
        ]:
            raise RuntimeError(
                f"offset 0 was not observed as crash redelivery: {offset_zero_attempts}"
            )
        if initial_snapshot["checkpoint"]["durable_next_offset"] != 4:
            raise RuntimeError(
                f"sink checkpoint did not reach offset 4: {initial_snapshot}"
            )

        empty_sink_database = run_root / "empty-sink.duckdb"
        try:
            consume_available(
                bootstrap_servers=broker.bootstrap_servers,
                topic=topic,
                group_id=group_id,
                database_path=empty_sink_database,
                timeout_seconds=15,
            )
        except KafkaCheckpointMismatch as exc:
            empty_sink_error = str(exc)
        else:
            raise RuntimeError("empty sink accepted an already advanced consumer group")
        empty_sink_snapshot = load_sink_snapshot(empty_sink_database)
        if empty_sink_snapshot["event_count"] != 0:
            raise RuntimeError(
                f"empty mismatched sink materialized events: {empty_sink_snapshot}"
            )

        late_plan = planned_payloads(
            KAFKA_EVENTS, KAFKA_DELIVERIES, phase="late"
        )
        late_positions = produce_payloads(broker.bootstrap_servers, topic, late_plan)
        late_consumer, late_barrier = start_crash_consumer(
            bootstrap_servers=broker.bootstrap_servers,
            topic=topic,
            group_id=group_id,
            database=sink_database,
            checkpoint="after_offset_commit_before_local_audit",
        )
        try:
            if late_barrier["delivery"]["offset"] != 4:
                raise RuntimeError(f"unexpected late barrier: {late_barrier}")
            kill_owned_process(late_consumer)
        finally:
            if late_consumer.poll() is None:
                late_consumer.kill()
                late_consumer.wait(timeout=10)

        committed_after_late_kill = committed_next_offset(
            broker.bootstrap_servers, group_id, topic
        )
        snapshot_after_late_kill = load_sink_snapshot(sink_database)
        if committed_after_late_kill != 5:
            raise RuntimeError(
                "broker commit was not preserved before missing local audit: "
                f"{committed_after_late_kill}"
            )
        if snapshot_after_late_kill["checkpoint"]["durable_next_offset"] != 5:
            raise RuntimeError(
                f"sink checkpoint did not preserve the late event: {snapshot_after_late_kill}"
            )
        if any(
            item["committed_next_offset"] == 5
            for item in snapshot_after_late_kill["offset_commits"]
        ):
            raise RuntimeError("local audit unexpectedly recorded offset 5 before the kill")

        late_result = consume_available(
            bootstrap_servers=broker.bootstrap_servers,
            topic=topic,
            group_id=group_id,
            database_path=sink_database,
            timeout_seconds=30,
        )
        if late_result["deliveries"]:
            raise RuntimeError(
                f"broker-committed late event was incorrectly redelivered: {late_result}"
            )
        final_snapshot = load_sink_snapshot(sink_database)

        delivered_events = [item["event"] for item in initial_plan + late_plan]
        python_batch = independent_python_batch(delivered_events)
        sql_batch = independent_sql_batch(delivered_events)
        if not (python_batch == sql_batch == final_snapshot["aggregates"]):
            raise RuntimeError(
                "independent batch mismatch: "
                f"python={python_batch}, sql={sql_batch}, sink={final_snapshot['aggregates']}"
            )

        conflict_raw = json.loads(KAFKA_CONFLICT_EVENT.read_text(encoding="utf-8"))
        conflict_event = canonicalize_event(conflict_raw)
        conflict_positions = produce_payloads(
            broker.bootstrap_servers,
            topic,
            [{"delivery_id": "conflict-0001", "event": conflict_event}],
        )
        collision_errors: list[str] = []
        for _ in range(2):
            try:
                consume_available(
                    bootstrap_servers=broker.bootstrap_servers,
                    topic=topic,
                    group_id=group_id,
                    database_path=sink_database,
                    timeout_seconds=30,
                )
            except EventIdCollision as exc:
                collision_errors.append(str(exc))
            else:
                raise RuntimeError("conflicting event unexpectedly advanced the consumer")
        committed_after_conflict = committed_next_offset(
            broker.bootstrap_servers, group_id, topic
        )
        conflict_snapshot = load_sink_snapshot(sink_database)
        if committed_after_conflict != 5:
            raise RuntimeError(
                f"conflict advanced committed offset: {committed_after_conflict}"
            )
        if conflict_snapshot["aggregates"] != final_snapshot["aggregates"]:
            raise RuntimeError("conflict changed the accepted event aggregate")
        if conflict_snapshot["conflict_count"] != 2:
            raise RuntimeError(
                f"conflict was not redelivered at the same offset: {conflict_snapshot}"
            )

        result.update(
            {
                "initial_produced": initial_positions,
                "forced_termination": {
                    "consumer_pid": consumer.pid,
                    "consumer_returncode": consumer.returncode,
                    "barrier": barrier,
                    "committed_offset_after_kill": committed_after_kill,
                    "sink_after_kill": snapshot_after_kill,
                },
                "resumed_consumer": resumed,
                "initial_snapshot": initial_snapshot,
                "empty_sink_mismatch": {
                    "database": str(empty_sink_database),
                    "error": empty_sink_error,
                    "snapshot": empty_sink_snapshot,
                },
                "late_input": {
                    "produced": late_positions,
                    "forced_termination": {
                        "consumer_pid": late_consumer.pid,
                        "consumer_returncode": late_consumer.returncode,
                        "barrier": late_barrier,
                        "committed_offset_after_kill": committed_after_late_kill,
                        "sink_after_kill": snapshot_after_late_kill,
                    },
                    "recovery": late_result,
                    "changed_date": "2026-09-21",
                },
                "independent_batches": {
                    "python": python_batch,
                    "sql": sql_batch,
                    "matches_sink": True,
                },
                "conflict": {
                    "produced": conflict_positions,
                    "errors": collision_errors,
                    "committed_offset_after_two_attempts": committed_after_conflict,
                    "snapshot": conflict_snapshot,
                },
            }
        )
    finally:
        broker.stop()

    result["broker_exit_code"] = broker.process.returncode
    result_path = run_root / "experiment-result.json"
    write_result(result_path, result)
    print(json.dumps({**result, "result_path": str(result_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
