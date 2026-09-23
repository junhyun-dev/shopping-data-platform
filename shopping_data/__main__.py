from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import (
    DEFAULT_DATABASE,
    DEFAULT_MANIFEST,
    DEFAULT_PUBLISHED_ROOT,
    DEFAULT_SOURCE,
    WEB_ROOT,
)
from .pipeline import run_pipeline
from .server import serve


def parse_rows(value: str) -> list[int]:
    try:
        rows = [int(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--rows must be comma-separated Excel row numbers") from exc
    if not rows:
        raise argparse.ArgumentTypeError("--rows must include at least one Excel row number")
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local shopping-data pipeline")
    subcommands = parser.add_subparsers(dest="command", required=True)

    run = subcommands.add_parser("run", help="load the selected source rows and publish a result")
    run.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    run.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    run.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    run.add_argument("--published-root", type=Path, default=DEFAULT_PUBLISHED_ROOT)
    run.add_argument(
        "--rows",
        type=parse_rows,
        help="override the manifest sample with comma-separated Excel rows for this run",
    )

    web = subcommands.add_parser("serve", help="serve only the loopback result viewer")
    web.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    web.add_argument("--published-root", type=Path, default=DEFAULT_PUBLISHED_ROOT)
    web.add_argument("--port", type=int, default=8765)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "run":
        result = run_pipeline(
            source_path=args.source,
            manifest_path=args.manifest,
            database_path=args.database,
            published_root=args.published_root,
            selected_rows=args.rows,
        )
        summary = {
            "run_id": result["run"]["run_id"],
            "result_state": result["result_state"],
            "source_file_id": result["source"]["source_file_id"],
            "products": [
                {
                    "observed_date": item["observed_date"],
                    "stock_code": item["stock_code"],
                    "sample_quantity_sum": item["sample_quantity_sum"],
                    "observed_row_count": item["observed_row_count"],
                }
                for item in result["products"]
            ],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    serve(
        web_root=WEB_ROOT,
        database_path=args.database,
        published_root=args.published_root,
        port=args.port,
    )


if __name__ == "__main__":
    main()
