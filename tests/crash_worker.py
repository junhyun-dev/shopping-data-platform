"""Subprocess target used only by process-interruption integration tests."""

from __future__ import annotations

import argparse
import json
import signal
from pathlib import Path

from shopping_data.pipeline import PROCESS_CHECKPOINTS, run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--published-root", type=Path, required=True)
    parser.add_argument("--rows", type=int, nargs="+", required=True)
    parser.add_argument("--checkpoint", choices=PROCESS_CHECKPOINTS, required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()

    def wait_for_parent(checkpoint: str, run_id: str) -> None:
        if checkpoint != args.checkpoint:
            return
        print(
            json.dumps(
                {
                    "state": "ready_for_forced_termination",
                    "checkpoint": checkpoint,
                    "run_id": run_id,
                }
            ),
            flush=True,
        )
        signal.pause()

    run_pipeline(
        source_path=args.source,
        manifest_path=args.manifest,
        database_path=args.database,
        published_root=args.published_root,
        selected_rows=args.rows,
        process_checkpoint=wait_for_parent,
    )


if __name__ == "__main__":
    main()
