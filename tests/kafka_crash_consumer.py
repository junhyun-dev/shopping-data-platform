"""Kafka consumer target used only by the real process-interruption experiment."""

from __future__ import annotations

import argparse
import json
import signal
from pathlib import Path

from shopping_data.kafka_lab import consume_available


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap-servers", required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--group-id", required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--checkpoint",
        choices=(
            "after_sink_commit_before_offset_commit",
            "after_offset_commit_before_local_audit",
        ),
        default="after_sink_commit_before_offset_commit",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    stopped = False

    def wait_after_sink_commit(
        checkpoint: str,
        run_id: str,
        delivery: dict,
    ) -> None:
        nonlocal stopped
        if stopped or checkpoint != args.checkpoint:
            return
        stopped = True
        print(
            json.dumps(
                {
                    "state": "ready_for_forced_termination",
                    "checkpoint": checkpoint,
                    "run_id": run_id,
                    "delivery": delivery,
                }
            ),
            flush=True,
        )
        signal.pause()

    consume_available(
        bootstrap_servers=args.bootstrap_servers,
        topic=args.topic,
        group_id=args.group_id,
        database_path=args.database,
        checkpoint=wait_after_sink_commit,
    )


if __name__ == "__main__":
    main()
