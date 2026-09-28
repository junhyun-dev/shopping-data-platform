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
    PLATFORM_FIXTURE_MANIFEST,
)
from .pipeline import run_pipeline
from .platform import platform_runtime_paths, prepare_platform_runtime
from .platform_server import serve_platform
from .server import serve
from .source_meaning import DEFAULT_SOURCE_MEANING_ROWS, observe_source_meaning


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

    observation = subcommands.add_parser(
        "observe-source-meaning",
        help="compare source-marker groups in an isolated temporary run",
    )
    observation.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    observation.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    observation.add_argument(
        "--rows",
        type=parse_rows,
        default=list(DEFAULT_SOURCE_MEANING_ROWS),
        help="comma-separated Excel rows; defaults to 2,143,156",
    )

    platform_prepare = subcommands.add_parser(
        "platform-prepare",
        help="prepare a new isolated synthetic platform runtime",
    )
    platform_prepare.add_argument("--runtime-root", type=Path, required=True)
    platform_prepare.add_argument("--manifest", type=Path, default=PLATFORM_FIXTURE_MANIFEST)

    platform_web = subcommands.add_parser(
        "platform-serve",
        help="serve the synthetic platform from an existing isolated runtime",
    )
    platform_web.add_argument("--runtime-root", type=Path, required=True)
    platform_web.add_argument("--manifest", type=Path, default=PLATFORM_FIXTURE_MANIFEST)
    platform_web.add_argument("--port", type=int, default=8770)
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

    if args.command == "observe-source-meaning":
        result = observe_source_meaning(
            source_path=args.source,
            manifest_path=args.manifest,
            selected_rows=args.rows,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "platform-prepare":
        result = prepare_platform_runtime(
            args.runtime_root,
            manifest_path=args.manifest,
        )
        database_path, published_root = platform_runtime_paths(args.runtime_root)
        print(
            json.dumps(
                {
                    "run_id": result["run"]["run_id"],
                    "result_state": result["result_state"],
                    "summary": result["summary"],
                    "database": str(database_path),
                    "published_root": str(published_root),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.command == "platform-serve":
        serve_platform(
            runtime_root=args.runtime_root,
            manifest_path=args.manifest,
            port=args.port,
        )
        return

    serve(
        web_root=WEB_ROOT,
        database_path=args.database,
        published_root=args.published_root,
        port=args.port,
    )


if __name__ == "__main__":
    main()
