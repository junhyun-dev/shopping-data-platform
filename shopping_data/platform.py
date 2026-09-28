from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Callable, TextIO
from zoneinfo import ZoneInfo

import duckdb

from .config import (
    PLATFORM_CONTRIBUTIONS_SQL,
    PLATFORM_FIXTURE_MANIFEST,
    PLATFORM_PRODUCT_SUMMARY_SQL,
    PLATFORM_SCHEMA_SQL,
    PROJECT_ROOT,
)
from .pipeline import (
    PipelineBusy,
    _acquire_writer_lock,
    _release_writer_lock,
    sha256_file,
    utc_now,
)


class PlatformFailure(RuntimeError):
    """The synthetic order input could not produce a trustworthy result."""


class PlatformNotFound(LookupError):
    """The requested saved platform result does not exist."""


class PlatformReadError(RuntimeError):
    """A registered saved result cannot be read safely."""


PlatformCheckpoint = Callable[[str, str], None]

PLATFORM_CHECKPOINTS = (
    "before_data_commit",
    "after_data_commit",
    "after_artifact_write",
    "after_publish_commit",
)

_MANIFEST_KEYS = {
    "schema_version",
    "dataset",
    "target_date",
    "timezone",
    "coverage",
    "input_limits",
    "products",
    "initial_loaded_segments",
    "recovery_target_segment",
    "expected_segments",
}
_COVERAGE_KEYS = {"from", "to", "full_day"}
_LIMIT_KEYS = {
    "max_source_bytes",
    "max_orders_per_segment",
    "max_lines_per_segment",
}
_PRODUCT_KEYS = {"product_id", "name"}
_SEGMENT_MANIFEST_KEYS = {
    "segment_id",
    "from",
    "to",
    "source_file",
    "expected_sha256",
    "archive_arrived_at",
}
_SOURCE_KEYS = {
    "schema_version",
    "target_date",
    "timezone",
    "segment_id",
    "orders",
}
_ORDER_KEYS = {"order_id", "occurred_at", "lines"}
_LINE_KEYS = {"line_id", "product_id", "qty", "unit_price_krw"}


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"missing={','.join(missing)}")
        if extra:
            parts.append(f"unexpected={','.join(extra)}")
        raise PlatformFailure(f"{label} fields are invalid: {'; '.join(parts)}")


def _nonempty_text(value: Any, label: str, *, max_length: int = 200) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise PlatformFailure(f"{label} must be non-empty text up to {max_length} characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise PlatformFailure(f"{label} contains a control character")
    return value


def _positive_integer(value: Any, label: str, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PlatformFailure(f"{label} must be an integer")
    if value <= 0 or value > maximum:
        raise PlatformFailure(f"{label} must be between 1 and {maximum}")
    return value


def _parse_date(value: Any, label: str) -> date:
    if not isinstance(value, str):
        raise PlatformFailure(f"{label} must be YYYY-MM-DD text")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise PlatformFailure(f"{label} must be a real YYYY-MM-DD date") from exc
    if parsed.isoformat() != value:
        raise PlatformFailure(f"{label} must use canonical YYYY-MM-DD form")
    return parsed


def _parse_time(value: Any, label: str) -> time:
    if not isinstance(value, str):
        raise PlatformFailure(f"{label} must be HH:MM text")
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise PlatformFailure(f"{label} must be a real HH:MM time") from exc
    if parsed.second or parsed.microsecond or parsed.strftime("%H:%M") != value:
        raise PlatformFailure(f"{label} must use canonical HH:MM form")
    return parsed


def _parse_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise PlatformFailure(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PlatformFailure(f"{label} must be a valid ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PlatformFailure(f"{label} must include an explicit UTC offset")
    return parsed


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sql_rule_id(sql: str) -> str:
    return f"sha256:{hashlib.sha256(sql.encode('utf-8')).hexdigest()}"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _completion_time(started_at: datetime) -> datetime:
    return max(utc_now(), started_at)


def _invoke_checkpoint(
    checkpoint: PlatformCheckpoint | None, name: str, run_id: str
) -> None:
    if checkpoint is not None:
        checkpoint(name, run_id)


def load_platform_manifest(path: Path = PLATFORM_FIXTURE_MANIFEST) -> dict[str, Any]:
    path = path.resolve()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PlatformFailure(f"platform manifest could not be read: {exc}") from exc
    if len(raw) > 262_144:
        raise PlatformFailure("platform manifest exceeds the 256 KiB limit")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlatformFailure(f"platform manifest is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise PlatformFailure("platform manifest must be a JSON object")
    _exact_keys(manifest, _MANIFEST_KEYS, "platform manifest")
    if manifest["schema_version"] != 1:
        raise PlatformFailure("platform manifest schema_version must be 1")
    _nonempty_text(manifest["dataset"], "dataset")
    target_date = _parse_date(manifest["target_date"], "target_date")
    timezone_name = _nonempty_text(manifest["timezone"], "timezone", max_length=64)
    try:
        ZoneInfo(timezone_name)
    except Exception as exc:
        raise PlatformFailure(f"timezone is not available: {timezone_name}") from exc

    coverage = manifest["coverage"]
    if not isinstance(coverage, dict):
        raise PlatformFailure("coverage must be an object")
    _exact_keys(coverage, _COVERAGE_KEYS, "coverage")
    coverage_from = _parse_time(coverage["from"], "coverage.from")
    coverage_to = _parse_time(coverage["to"], "coverage.to")
    if coverage_from >= coverage_to:
        raise PlatformFailure("coverage.from must be earlier than coverage.to")
    if not isinstance(coverage["full_day"], bool) or coverage["full_day"]:
        raise PlatformFailure("this fixture must explicitly remain a non-full-day scope")

    limits = manifest["input_limits"]
    if not isinstance(limits, dict):
        raise PlatformFailure("input_limits must be an object")
    _exact_keys(limits, _LIMIT_KEYS, "input_limits")
    for key in sorted(_LIMIT_KEYS):
        _positive_integer(limits[key], f"input_limits.{key}", maximum=10_000_000)

    products = manifest["products"]
    if not isinstance(products, list) or not products:
        raise PlatformFailure("products must be a non-empty array")
    product_ids: set[str] = set()
    for index, product in enumerate(products):
        if not isinstance(product, dict):
            raise PlatformFailure(f"products[{index}] must be an object")
        _exact_keys(product, _PRODUCT_KEYS, f"products[{index}]")
        product_id = _nonempty_text(product["product_id"], f"products[{index}].product_id", max_length=64)
        _nonempty_text(product["name"], f"products[{index}].name")
        if product_id in product_ids:
            raise PlatformFailure(f"duplicate product_id: {product_id}")
        product_ids.add(product_id)

    segments = manifest["expected_segments"]
    if not isinstance(segments, list) or not segments:
        raise PlatformFailure("expected_segments must be a non-empty array")
    segment_ids: set[str] = set()
    previous_to: time | None = None
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise PlatformFailure(f"expected_segments[{index}] must be an object")
        _exact_keys(segment, _SEGMENT_MANIFEST_KEYS, f"expected_segments[{index}]")
        segment_id = _nonempty_text(segment["segment_id"], f"expected_segments[{index}].segment_id", max_length=64)
        if segment_id in segment_ids:
            raise PlatformFailure(f"duplicate segment_id: {segment_id}")
        segment_ids.add(segment_id)
        from_time = _parse_time(segment["from"], f"{segment_id}.from")
        to_time = _parse_time(segment["to"], f"{segment_id}.to")
        if from_time >= to_time:
            raise PlatformFailure(f"segment range is empty or reversed: {segment_id}")
        if previous_to is not None and previous_to != from_time:
            raise PlatformFailure("expected segments must be contiguous and ordered")
        previous_to = to_time
        source_relative = Path(_nonempty_text(segment["source_file"], f"{segment_id}.source_file"))
        if source_relative.is_absolute() or ".." in source_relative.parts:
            raise PlatformFailure(f"source_file must stay under the fixture directory: {segment_id}")
        expected_sha = _nonempty_text(segment["expected_sha256"], f"{segment_id}.expected_sha256", max_length=64)
        if len(expected_sha) != 64 or any(character not in "0123456789abcdef" for character in expected_sha):
            raise PlatformFailure(f"expected_sha256 is invalid: {segment_id}")
        archive_arrived = _parse_timestamp(segment["archive_arrived_at"], f"{segment_id}.archive_arrived_at")
        if archive_arrived.astimezone(ZoneInfo(timezone_name)).date() != target_date:
            raise PlatformFailure(f"archive arrival date does not match target_date: {segment_id}")

    if segments[0]["from"] != coverage["from"] or segments[-1]["to"] != coverage["to"]:
        raise PlatformFailure("coverage boundaries must equal the expected segment boundaries")
    initial = manifest["initial_loaded_segments"]
    if not isinstance(initial, list) or not initial or any(not isinstance(value, str) for value in initial):
        raise PlatformFailure("initial_loaded_segments must be a non-empty text array")
    if len(initial) != len(set(initial)) or not set(initial).issubset(segment_ids):
        raise PlatformFailure("initial_loaded_segments contains duplicates or unknown segments")
    recovery_target = _nonempty_text(manifest["recovery_target_segment"], "recovery_target_segment", max_length=64)
    if recovery_target not in segment_ids or recovery_target in initial:
        raise PlatformFailure("recovery_target_segment must be expected and absent from the initial scope")
    if set(initial) | {recovery_target} != segment_ids:
        raise PlatformFailure("initial scope plus recovery target must cover exactly the expected segments")
    return manifest


def _manifest_segment(manifest: dict[str, Any], segment_id: str) -> dict[str, Any]:
    for segment in manifest["expected_segments"]:
        if segment["segment_id"] == segment_id:
            return segment
    raise PlatformFailure(f"segment is not allowlisted by the manifest: {segment_id}")


def _source_path(manifest_path: Path, source_relative: str) -> Path:
    fixture_root = manifest_path.resolve().parent
    source_path = (fixture_root / source_relative).resolve()
    if not source_path.is_relative_to(fixture_root):
        raise PlatformFailure("source path escaped the fixture directory")
    return source_path


def _load_segment_source(
    manifest: dict[str, Any], manifest_path: Path, segment_id: str
) -> tuple[dict[str, Any], Path, str]:
    segment = _manifest_segment(manifest, segment_id)
    source_path = _source_path(manifest_path, segment["source_file"])
    try:
        size = source_path.stat().st_size
    except OSError as exc:
        raise PlatformFailure(f"source archive is unavailable for {segment_id}: {exc}") from exc
    if size > manifest["input_limits"]["max_source_bytes"]:
        raise PlatformFailure(f"source archive exceeds the configured byte limit: {segment_id}")
    digest = sha256_file(source_path)
    if digest != segment["expected_sha256"]:
        raise PlatformFailure(
            f"source SHA-256 mismatch for {segment_id}: expected {segment['expected_sha256']}, got {digest}"
        )
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlatformFailure(f"source archive is not valid UTF-8 JSON for {segment_id}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PlatformFailure(f"source archive must be an object: {segment_id}")
    _exact_keys(payload, _SOURCE_KEYS, f"source {segment_id}")
    if payload["schema_version"] != 1:
        raise PlatformFailure(f"source schema_version must be 1: {segment_id}")
    if payload["target_date"] != manifest["target_date"]:
        raise PlatformFailure(f"source target_date does not match the manifest: {segment_id}")
    if payload["timezone"] != manifest["timezone"]:
        raise PlatformFailure(f"source timezone does not match the manifest: {segment_id}")
    if payload["segment_id"] != segment_id:
        raise PlatformFailure(f"source segment_id does not match its allowlisted segment: {segment_id}")
    if not isinstance(payload["orders"], list):
        raise PlatformFailure(f"orders must be an array: {segment_id}")
    if len(payload["orders"]) > manifest["input_limits"]["max_orders_per_segment"]:
        raise PlatformFailure(f"source order count exceeds the configured limit: {segment_id}")

    product_ids = {product["product_id"] for product in manifest["products"]}
    zone = ZoneInfo(manifest["timezone"])
    target_date = _parse_date(manifest["target_date"], "target_date")
    from_time = _parse_time(segment["from"], f"{segment_id}.from")
    to_time = _parse_time(segment["to"], f"{segment_id}.to")
    seen_orders: set[str] = set()
    line_count = 0
    for order_index, order in enumerate(payload["orders"]):
        if not isinstance(order, dict):
            raise PlatformFailure(f"orders[{order_index}] must be an object: {segment_id}")
        _exact_keys(order, _ORDER_KEYS, f"{segment_id}.orders[{order_index}]")
        order_id = _nonempty_text(order["order_id"], f"{segment_id}.orders[{order_index}].order_id", max_length=64)
        if order_id in seen_orders:
            raise PlatformFailure(f"duplicate order_id inside {segment_id}: {order_id}")
        seen_orders.add(order_id)
        occurred_at = _parse_timestamp(order["occurred_at"], f"{segment_id}.{order_id}.occurred_at")
        local = occurred_at.astimezone(zone)
        if local.date() != target_date or not (from_time <= local.time().replace(tzinfo=None) < to_time):
            raise PlatformFailure(f"order timestamp is outside the half-open segment range: {order_id}")
        lines = order["lines"]
        if not isinstance(lines, list) or not lines:
            raise PlatformFailure(f"order lines must be a non-empty array: {order_id}")
        seen_lines: set[str] = set()
        for line_index, line in enumerate(lines):
            if not isinstance(line, dict):
                raise PlatformFailure(f"line must be an object: {order_id}[{line_index}]")
            _exact_keys(line, _LINE_KEYS, f"{order_id}.lines[{line_index}]")
            line_id = _nonempty_text(line["line_id"], f"{order_id}.line_id", max_length=64)
            if line_id in seen_lines:
                raise PlatformFailure(f"duplicate line_id inside {order_id}: {line_id}")
            seen_lines.add(line_id)
            product_id = _nonempty_text(line["product_id"], f"{order_id}/{line_id}.product_id", max_length=64)
            if product_id not in product_ids:
                raise PlatformFailure(f"order line references an unknown product: {order_id}/{line_id}")
            _positive_integer(line["qty"], f"{order_id}/{line_id}.qty", maximum=1_000_000)
            _positive_integer(line["unit_price_krw"], f"{order_id}/{line_id}.unit_price_krw", maximum=1_000_000_000)
            line_count += 1
    if line_count > manifest["input_limits"]["max_lines_per_segment"]:
        raise PlatformFailure(f"source line count exceeds the configured limit: {segment_id}")
    return payload, source_path, digest


def initialize_platform_database(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(PLATFORM_SCHEMA_SQL.read_text(encoding="utf-8"))


def _register_contract(
    connection: duckdb.DuckDBPyConnection,
    manifest: dict[str, Any],
) -> None:
    target_date = manifest["target_date"]
    for product in manifest["products"]:
        existing = connection.execute(
            "SELECT product_name FROM platform_products WHERE product_id = ?",
            [product["product_id"]],
        ).fetchone()
        if existing is not None and existing[0] != product["name"]:
            raise PlatformFailure(f"product contract changed for {product['product_id']}")
        connection.execute(
            "INSERT OR IGNORE INTO platform_products (product_id, product_name) VALUES (?, ?)",
            [product["product_id"], product["name"]],
        )
    for segment in manifest["expected_segments"]:
        expected = (
            segment["from"],
            segment["to"],
            segment["source_file"],
            segment["expected_sha256"],
            _parse_timestamp(segment["archive_arrived_at"], "archive_arrived_at"),
        )
        existing = connection.execute(
            """
            SELECT CAST(from_time AS VARCHAR), CAST(to_time AS VARCHAR),
                   source_relative_path, expected_sha256, archive_arrived_at
            FROM platform_expected_segments
            WHERE target_date = ? AND segment_id = ?
            """,
            [target_date, segment["segment_id"]],
        ).fetchone()
        if existing is not None:
            normalized = (existing[0][:5], existing[1][:5], existing[2], existing[3], existing[4])
            if normalized != expected:
                raise PlatformFailure(f"expected segment contract changed for {segment['segment_id']}")
            continue
        connection.execute(
            """
            INSERT INTO platform_expected_segments
                (target_date, segment_id, from_time, to_time, source_relative_path,
                 expected_sha256, archive_arrived_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [target_date, segment["segment_id"], *expected],
        )


def _insert_source_payload(
    connection: duckdb.DuckDBPyConnection,
    *,
    manifest: dict[str, Any],
    segment: dict[str, Any],
    payload: dict[str, Any],
    source_path: Path,
    digest: str,
    registered_at: datetime,
) -> str:
    source_file_id = f"sha256:{digest}"
    existing_file = connection.execute(
        """
        SELECT target_date, segment_id, sha256, stored_path
        FROM platform_source_files WHERE source_file_id = ?
        """,
        [source_file_id],
    ).fetchone()
    expected_file = (
        _parse_date(manifest["target_date"], "target_date"),
        segment["segment_id"],
        digest,
        str(source_path),
    )
    if existing_file is not None and tuple(existing_file) != expected_file:
        raise PlatformFailure(f"source file identity collision: {source_file_id}")
    connection.execute(
        """
        INSERT OR IGNORE INTO platform_source_files
            (source_file_id, target_date, segment_id, sha256, stored_path,
             archive_arrived_at, registered_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            source_file_id,
            manifest["target_date"],
            segment["segment_id"],
            digest,
            str(source_path),
            _parse_timestamp(segment["archive_arrived_at"], "archive_arrived_at"),
            registered_at,
        ],
    )

    for order_index, order in enumerate(payload["orders"]):
        order_contract = {
            "order_id": order["order_id"],
            "occurred_at": order["occurred_at"],
            "target_date": manifest["target_date"],
            "segment_id": segment["segment_id"],
            "lines": order["lines"],
        }
        order_fingerprint = _fingerprint(order_contract)
        existing_order = connection.execute(
            "SELECT order_fingerprint FROM platform_orders WHERE order_id = ?",
            [order["order_id"]],
        ).fetchone()
        if existing_order is not None and existing_order[0] != order_fingerprint:
            raise PlatformFailure(f"order ID collision with different content: {order['order_id']}")
        connection.execute(
            """
            INSERT OR IGNORE INTO platform_orders
                (order_id, target_date, occurred_at, segment_id, source_file_id,
                 source_order_index, order_fingerprint, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                order["order_id"],
                manifest["target_date"],
                _parse_timestamp(order["occurred_at"], "occurred_at"),
                segment["segment_id"],
                source_file_id,
                order_index,
                order_fingerprint,
                _canonical_json(order),
            ],
        )
        for line_index, line in enumerate(order["lines"]):
            line_contract = {
                "order_id": order["order_id"],
                "line_id": line["line_id"],
                "product_id": line["product_id"],
                "qty": line["qty"],
                "unit_price_krw": line["unit_price_krw"],
                "segment_id": segment["segment_id"],
            }
            line_fingerprint = _fingerprint(line_contract)
            existing_line = connection.execute(
                """
                SELECT line_fingerprint FROM platform_order_lines
                WHERE order_id = ? AND line_id = ?
                """,
                [order["order_id"], line["line_id"]],
            ).fetchone()
            if existing_line is not None and existing_line[0] != line_fingerprint:
                raise PlatformFailure(
                    f"order-line ID collision with different content: {order['order_id']}/{line['line_id']}"
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO platform_order_lines
                    (order_id, line_id, product_id, qty, unit_price_krw,
                     source_file_id, segment_id, source_order_index,
                     source_line_index, line_fingerprint, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    order["order_id"],
                    line["line_id"],
                    line["product_id"],
                    line["qty"],
                    line["unit_price_krw"],
                    source_file_id,
                    segment["segment_id"],
                    order_index,
                    line_index,
                    line_fingerprint,
                    _canonical_json(line),
                ],
            )
    return source_file_id


def _build_platform_result(
    connection: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    manifest: dict[str, Any],
    selected_segments: list[str],
    started_at: datetime,
    completed_at: datetime,
) -> dict[str, Any]:
    product_sql = PLATFORM_PRODUCT_SUMMARY_SQL.read_text(encoding="utf-8").strip()
    contribution_sql = PLATFORM_CONTRIBUTIONS_SQL.read_text(encoding="utf-8").strip()
    product_rows = connection.execute(product_sql, [run_id]).fetchall()
    contribution_rows = connection.execute(contribution_sql, [run_id]).fetchall()
    contributions_by_product: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in contribution_rows:
        contribution = {
            "observed_date": row[0],
            "product_id": row[1],
            "order_id": row[2],
            "line_id": row[3],
            "occurred_at": row[4],
            "segment_id": row[5],
            "qty": row[6],
            "quantity_integer_text": str(row[6]),
            "unit_price_krw": row[7],
            "line_amount_krw": row[8],
            "source_file_id": row[9],
            "source_path": row[10],
            "source_order_index": row[11],
            "source_line_index": row[12],
        }
        contributions_by_product.setdefault((row[0], row[1]), []).append(contribution)

    products: list[dict[str, Any]] = []
    for row in product_rows:
        key = (row[0], row[1])
        product_contributions = contributions_by_product.get(key, [])
        product = {
            "observed_date": row[0],
            "product_id": row[1],
            "product_name": next(item["name"] for item in manifest["products"] if item["product_id"] == row[1]),
            "product_quantity": row[2],
            "product_quantity_integer_text": str(row[2]),
            "contributing_order_count": row[3],
            "order_line_count": row[4],
            "line_amount_krw": str(row[5]),
            "contributing_rows": product_contributions,
        }
        if sum(item["qty"] for item in product_contributions) != product["product_quantity"]:
            raise PlatformFailure(f"product quantity reconciliation failed: {row[1]}")
        if len({item["order_id"] for item in product_contributions}) != product["contributing_order_count"]:
            raise PlatformFailure(f"product order reconciliation failed: {row[1]}")
        if len(product_contributions) != product["order_line_count"]:
            raise PlatformFailure(f"product line reconciliation failed: {row[1]}")
        products.append(product)

    order_count = len({row[2] for row in contribution_rows})
    line_count = len(contribution_rows)
    product_quantity = sum(row[6] for row in contribution_rows)
    line_amount_krw = sum(int(row[8]) for row in contribution_rows)
    expected_segment_ids = [item["segment_id"] for item in manifest["expected_segments"]]
    result_state = "complete" if selected_segments == expected_segment_ids else "partial"
    sources = connection.execute(
        """
        SELECT prs.segment_id, psf.source_file_id, psf.sha256, psf.stored_path,
               CAST(psf.archive_arrived_at AS VARCHAR), CAST(psf.registered_at AS VARCHAR)
        FROM platform_run_segments AS prs
        JOIN platform_source_files AS psf USING (source_file_id)
        WHERE prs.run_id = ?
        ORDER BY prs.segment_id
        """,
        [run_id],
    ).fetchall()
    return {
        "kind": "synthetic-shopping-platform-result",
        "result_state": result_state,
        "run": {
            "run_id": run_id,
            "target_date": manifest["target_date"],
            "started_at": started_at.isoformat(timespec="seconds"),
            "completed_at": completed_at.isoformat(timespec="seconds"),
            "selected_segments": selected_segments,
        },
        "coverage": {
            "from": manifest["coverage"]["from"],
            "to": manifest["coverage"]["to"],
            "full_day": False,
            "timezone": manifest["timezone"],
            "expected_segments": expected_segment_ids,
        },
        "summary": {
            "order_count": order_count,
            "order_line_count": line_count,
            "product_quantity": product_quantity,
            "line_amount_krw": str(line_amount_krw),
        },
        "products": products,
        "sources": [
            {
                "segment_id": row[0],
                "source_file_id": row[1],
                "sha256": row[2],
                "stored_path": row[3],
                "archive_arrived_at": row[4],
                "processed_at": row[5],
            }
            for row in sources
        ],
        "sql": {
            "product_summary": product_sql,
            "product_summary_rule_id": _sql_rule_id(product_sql),
            "contribution_rows": contribution_sql,
            "contribution_rows_rule_id": _sql_rule_id(contribution_sql),
        },
    }


def run_platform_pipeline(
    *,
    manifest_path: Path,
    database_path: Path,
    published_root: Path,
    selected_segments: list[str],
    run_kind: str,
    failure_point: str | None = None,
    process_checkpoint: PlatformCheckpoint | None = None,
) -> dict[str, Any]:
    if run_kind not in {"initial", "recovery"}:
        raise ValueError("run_kind must be initial or recovery")
    if failure_point not in {None, "after_data", "after_artifact"}:
        raise ValueError("unknown platform failure point")
    manifest_path = manifest_path.resolve()
    database_path = database_path.resolve()
    published_root = published_root.resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    published_root.mkdir(parents=True, exist_ok=True)
    manifest = load_platform_manifest(manifest_path)
    expected_order = [item["segment_id"] for item in manifest["expected_segments"]]
    if len(selected_segments) != len(set(selected_segments)):
        raise PlatformFailure("selected_segments contains duplicates")
    if not set(selected_segments).issubset(set(expected_order)):
        raise PlatformFailure("selected_segments contains a segment outside the allowlist")
    selected_segments = [segment_id for segment_id in expected_order if segment_id in selected_segments]
    if not selected_segments:
        raise PlatformFailure("selected_segments must not be empty")

    run_id = str(uuid.uuid4())
    owner_token = f"{os.getpid()}:{uuid.uuid4()}"
    started_at = utc_now()
    artifact_path: Path | None = None
    artifact_registered = False
    lock_file, _ = _acquire_writer_lock(database_path, owner_token)
    connection: duckdb.DuckDBPyConnection | None = None
    try:
        connection = duckdb.connect(str(database_path))
        initialize_platform_database(connection)
        connection.execute("BEGIN TRANSACTION")
        _register_contract(connection, manifest)
        connection.execute("COMMIT")
        connection.execute(
            """
            INSERT INTO platform_runs
                (run_id, target_date, run_kind, status, selected_segments,
                 started_at, owner_pid, owner_token)
            VALUES (?, ?, ?, 'running', ?, ?, ?, ?)
            """,
            [
                run_id,
                manifest["target_date"],
                run_kind,
                json.dumps(selected_segments, separators=(",", ":")),
                started_at,
                os.getpid(),
                owner_token,
            ],
        )
        try:
            connection.execute("BEGIN TRANSACTION")
            registered_at = utc_now()
            for segment_id in selected_segments:
                segment = _manifest_segment(manifest, segment_id)
                payload, source_path, digest = _load_segment_source(
                    manifest, manifest_path, segment_id
                )
                source_file_id = _insert_source_payload(
                    connection,
                    manifest=manifest,
                    segment=segment,
                    payload=payload,
                    source_path=source_path,
                    digest=digest,
                    registered_at=registered_at,
                )
                connection.execute(
                    """
                    INSERT INTO platform_run_segments
                        (run_id, target_date, segment_id, source_file_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    [run_id, manifest["target_date"], segment_id, source_file_id],
                )
            _invoke_checkpoint(process_checkpoint, "before_data_commit", run_id)
            connection.execute("COMMIT")
            _invoke_checkpoint(process_checkpoint, "after_data_commit", run_id)
            if failure_point == "after_data":
                raise PlatformFailure("simulated platform failure after data commit")

            completed_at = _completion_time(started_at)
            payload = _build_platform_result(
                connection,
                run_id=run_id,
                manifest=manifest,
                selected_segments=selected_segments,
                started_at=started_at,
                completed_at=completed_at,
            )
            artifact_path = (published_root / f"{run_id}.json").resolve()
            if not artifact_path.is_relative_to(published_root):
                raise PlatformFailure("platform result path escaped the published directory")
            _write_json_atomic(artifact_path, payload)
            _invoke_checkpoint(process_checkpoint, "after_artifact_write", run_id)
            if failure_point == "after_artifact":
                raise PlatformFailure("simulated platform failure before publish")

            connection.execute("BEGIN TRANSACTION")
            if payload["result_state"] == "complete":
                connection.execute(
                    "UPDATE platform_result_artifacts SET is_current = FALSE WHERE is_current = TRUE"
                )
            connection.execute(
                """
                INSERT INTO platform_result_artifacts
                    (run_id, artifact_path, result_state, published_at, is_current)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    run_id,
                    str(artifact_path),
                    payload["result_state"],
                    completed_at if payload["result_state"] == "complete" else None,
                    payload["result_state"] == "complete",
                ],
            )
            connection.execute(
                """
                UPDATE platform_runs
                SET status = 'succeeded', completed_at = ?, error_message = NULL
                WHERE run_id = ?
                """,
                [completed_at, run_id],
            )
            connection.execute("COMMIT")
            artifact_registered = True
            _invoke_checkpoint(process_checkpoint, "after_publish_commit", run_id)
            return payload
        except Exception as exc:
            try:
                connection.execute("ROLLBACK")
            except duckdb.TransactionException:
                pass
            if artifact_path is not None and not artifact_registered:
                artifact_path.unlink(missing_ok=True)
            if not artifact_registered:
                connection.execute(
                    """
                    UPDATE platform_runs
                    SET status = 'failed', completed_at = ?, error_message = ?
                    WHERE run_id = ?
                    """,
                    [_completion_time(started_at), str(exc), run_id],
                )
            raise
    finally:
        if connection is not None:
            connection.close()
        _release_writer_lock(lock_file)


def _safe_runtime_root(runtime_root: Path) -> Path:
    runtime_root = runtime_root.resolve()
    allowed = (PROJECT_ROOT / "var").resolve()
    if runtime_root == allowed or not runtime_root.is_relative_to(allowed):
        raise PlatformFailure("platform runtime root must be a new child of the project var directory")
    return runtime_root


def prepare_platform_runtime(
    runtime_root: Path,
    *,
    manifest_path: Path = PLATFORM_FIXTURE_MANIFEST,
) -> dict[str, Any]:
    runtime_root = _safe_runtime_root(runtime_root)
    if runtime_root.exists():
        raise PlatformFailure(
            f"platform runtime root already exists; reopen it instead of preparing again: {runtime_root}"
        )
    runtime_root.mkdir(parents=True)
    manifest = load_platform_manifest(manifest_path)
    return run_platform_pipeline(
        manifest_path=manifest_path,
        database_path=runtime_root / "platform.duckdb",
        published_root=runtime_root / "published",
        selected_segments=list(manifest["initial_loaded_segments"]),
        run_kind="initial",
    )


def _latest_successful_run_segments(
    database_path: Path, target_date: str
) -> tuple[str, list[str]]:
    try:
        connection = duckdb.connect(str(database_path), read_only=True)
    except duckdb.Error as exc:
        raise PlatformReadError(f"platform database could not be opened: {exc}") from exc
    try:
        row = connection.execute(
            """
            SELECT pra.run_id
            FROM platform_result_artifacts AS pra
            JOIN platform_runs AS pr USING (run_id)
            WHERE pr.status = 'succeeded' AND pr.target_date = ?
            ORDER BY pr.completed_at DESC, pr.run_id DESC
            LIMIT 1
            """,
            [target_date],
        ).fetchone()
        if row is None:
            raise PlatformNotFound("no successful platform calculation exists for the target date")
        segments = [
            item[0]
            for item in connection.execute(
                """
                SELECT segment_id FROM platform_run_segments
                WHERE run_id = ? ORDER BY segment_id
                """,
                [row[0]],
            ).fetchall()
        ]
        return row[0], segments
    except duckdb.Error as exc:
        raise PlatformReadError(f"platform run scope could not be read: {exc}") from exc
    finally:
        connection.close()


def recover_platform_segment(
    *,
    manifest_path: Path,
    database_path: Path,
    published_root: Path,
    target_date: str,
    segment_id: str,
    failure_point: str | None = None,
) -> dict[str, Any]:
    manifest = load_platform_manifest(manifest_path)
    if target_date != manifest["target_date"]:
        raise PlatformFailure("target_date is outside the platform recovery allowlist")
    if segment_id != manifest["recovery_target_segment"]:
        raise PlatformFailure("segment_id is outside the platform recovery allowlist")
    _, previous_segments = _latest_successful_run_segments(database_path, target_date)
    selected = set(previous_segments)
    selected.add(segment_id)
    expected_order = [item["segment_id"] for item in manifest["expected_segments"]]
    return run_platform_pipeline(
        manifest_path=manifest_path,
        database_path=database_path,
        published_root=published_root,
        selected_segments=[item for item in expected_order if item in selected],
        run_kind="recovery",
        failure_point=failure_point,
    )


def _read_registered_result(
    database_path: Path, published_root: Path, run_id: str
) -> dict[str, Any]:
    published_root = published_root.resolve()
    try:
        connection = duckdb.connect(str(database_path), read_only=True)
    except duckdb.Error as exc:
        raise PlatformReadError(f"platform database could not be opened: {exc}") from exc
    try:
        row = connection.execute(
            """
            SELECT pra.artifact_path
            FROM platform_result_artifacts AS pra
            JOIN platform_runs AS pr USING (run_id)
            WHERE pra.run_id = ? AND pr.status = 'succeeded'
            """,
            [run_id],
        ).fetchone()
    except duckdb.Error as exc:
        raise PlatformReadError(f"platform result registration could not be read: {exc}") from exc
    finally:
        connection.close()
    if row is None:
        raise PlatformNotFound(f"successful platform result not found: {run_id}")
    artifact_path = Path(row[0]).resolve()
    if not artifact_path.is_relative_to(published_root):
        raise PlatformReadError("platform result path points outside the published directory")
    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlatformReadError(f"platform result could not be read: {exc}") from exc
    if payload.get("run", {}).get("run_id") != run_id:
        raise PlatformReadError("platform result run ID does not match its registration")
    return payload


def load_platform_result(
    database_path: Path, published_root: Path, run_id: str
) -> dict[str, Any]:
    if not isinstance(run_id, str) or len(run_id) != 36:
        raise PlatformNotFound("platform run ID is invalid")
    return _read_registered_result(database_path.resolve(), published_root.resolve(), run_id)


def _source_available(
    manifest: dict[str, Any], manifest_path: Path, segment_id: str
) -> tuple[bool, str | None]:
    try:
        _load_segment_source(manifest, manifest_path, segment_id)
    except PlatformFailure as exc:
        return False, str(exc)
    return True, None


def load_platform_state(
    *,
    manifest_path: Path,
    database_path: Path,
    published_root: Path,
) -> dict[str, Any]:
    manifest = load_platform_manifest(manifest_path)
    database_path = database_path.resolve()
    published_root = published_root.resolve()
    if not database_path.is_file():
        raise PlatformNotFound("platform runtime has not been prepared")
    try:
        connection = duckdb.connect(str(database_path), read_only=True)
    except duckdb.Error as exc:
        raise PlatformReadError(f"platform database could not be opened: {exc}") from exc
    try:
        latest = connection.execute(
            """
            SELECT pra.run_id
            FROM platform_result_artifacts AS pra
            JOIN platform_runs AS pr USING (run_id)
            WHERE pr.status = 'succeeded' AND pr.target_date = ?
            ORDER BY pr.completed_at DESC, pr.run_id DESC LIMIT 1
            """,
            [manifest["target_date"]],
        ).fetchone()
        current = connection.execute(
            """
            SELECT pra.run_id
            FROM platform_result_artifacts AS pra
            JOIN platform_runs AS pr USING (run_id)
            WHERE pra.is_current = TRUE AND pra.result_state = 'complete'
              AND pr.status = 'succeeded' AND pr.target_date = ?
            """,
            [manifest["target_date"]],
        ).fetchone()
        previous_calculation = connection.execute(
            """
            SELECT pra.run_id
            FROM platform_result_artifacts AS pra
            JOIN platform_runs AS pr USING (run_id)
            WHERE pr.status = 'succeeded' AND pr.target_date = ?
              AND pra.run_id <> ?
            ORDER BY pr.completed_at DESC, pr.run_id DESC LIMIT 1
            """,
            [manifest["target_date"], latest[0] if latest is not None else ""],
        ).fetchone()
        previous = None
        if current is not None:
            previous = connection.execute(
                """
                SELECT pra.run_id
                FROM platform_result_artifacts AS pra
                JOIN platform_runs AS pr USING (run_id)
                WHERE pra.result_state = 'complete' AND pr.status = 'succeeded'
                  AND pr.target_date = ? AND pra.run_id <> ?
                ORDER BY pr.completed_at DESC, pr.run_id DESC LIMIT 1
                """,
                [manifest["target_date"], current[0]],
            ).fetchone()
        runs = connection.execute(
            """
            SELECT run_id, run_kind, status, selected_segments,
                   CAST(started_at AS VARCHAR), CAST(completed_at AS VARCHAR), error_message
            FROM platform_runs
            ORDER BY started_at DESC, run_id DESC LIMIT 20
            """
        ).fetchall()
    except duckdb.Error as exc:
        raise PlatformReadError(f"platform state could not be read: {exc}") from exc
    finally:
        connection.close()
    if latest is None:
        raise PlatformNotFound("no successful platform calculation exists")
    latest_result = _read_registered_result(database_path, published_root, latest[0])
    current_result = (
        _read_registered_result(database_path, published_root, current[0])
        if current is not None
        else None
    )
    previous_result = (
        _read_registered_result(database_path, published_root, previous[0])
        if previous is not None
        else None
    )
    previous_calculation_result = (
        _read_registered_result(database_path, published_root, previous_calculation[0])
        if previous_calculation is not None
        else None
    )
    selected = set(latest_result["run"]["selected_segments"])
    segment_states = []
    for segment in manifest["expected_segments"]:
        available, source_error = _source_available(
            manifest, manifest_path, segment["segment_id"]
        )
        segment_states.append(
            {
                "segment_id": segment["segment_id"],
                "from": segment["from"],
                "to": segment["to"],
                "expected": True,
                "source_available": available,
                "source_error": source_error,
                "archive_arrived_at": segment["archive_arrived_at"] if available else None,
                "loaded": segment["segment_id"] in selected,
            }
        )
    recovery_segment = manifest["recovery_target_segment"]
    recovery_state = next(item for item in segment_states if item["segment_id"] == recovery_segment)
    return {
        "kind": "synthetic-shopping-platform-state",
        "target_date": manifest["target_date"],
        "timezone": manifest["timezone"],
        "coverage": {**manifest["coverage"], "full_day": False},
        "expected_segment_count": len(manifest["expected_segments"]),
        "loaded_segment_count": len(selected),
        "segments": segment_states,
        "displayed_result": current_result or latest_result,
        "latest_calculation": latest_result,
        "previous_calculation_result": previous_calculation_result,
        "current_normal_result": current_result,
        "previous_normal_result": previous_result,
        "recovery_plan": {
            "target_date": manifest["target_date"],
            "segment_id": recovery_segment,
            "source_available": recovery_state["source_available"],
            "already_loaded": recovery_state["loaded"],
            "can_run": recovery_state["source_available"] and not recovery_state["loaded"],
        },
        "runs": [
            {
                "run_id": row[0],
                "run_kind": row[1],
                "status": row[2],
                "selected_segments": json.loads(row[3]),
                "started_at": row[4],
                "completed_at": row[5],
                "error_message": row[6],
            }
            for row in runs
        ],
    }


def platform_runtime_paths(runtime_root: Path) -> tuple[Path, Path]:
    runtime_root = _safe_runtime_root(runtime_root)
    return runtime_root / "platform.duckdb", runtime_root / "published"
