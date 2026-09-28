from __future__ import annotations

import hashlib
import json
import shutil
import threading
import urllib.error
import urllib.request
from pathlib import Path

import duckdb
import pytest

from shopping_data.config import PLATFORM_FIXTURE_MANIFEST, PROJECT_ROOT
from shopping_data.platform import (
    PlatformFailure,
    load_platform_state,
    prepare_platform_runtime,
    recover_platform_segment,
    run_platform_pipeline,
)
from shopping_data.platform_server import make_platform_server
from shopping_data.pipeline import _acquire_writer_lock, _release_writer_lock
from tools.verify_platform_fixture import verify_fixture


def copy_fixture(tmp_path: Path) -> Path:
    destination = tmp_path / "fixture"
    shutil.copytree(PLATFORM_FIXTURE_MANIFEST.parent, destination)
    return destination / "manifest.json"


def runtime_paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "platform.duckdb", tmp_path / "published"


def run_initial(manifest: Path, tmp_path: Path) -> dict:
    database, published = runtime_paths(tmp_path)
    fixture = json.loads(manifest.read_text(encoding="utf-8"))
    return run_platform_pipeline(
        manifest_path=manifest,
        database_path=database,
        published_root=published,
        selected_segments=fixture["initial_loaded_segments"],
        run_kind="initial",
    )


def recover(manifest: Path, tmp_path: Path, *, failure_point: str | None = None) -> dict:
    database, published = runtime_paths(tmp_path)
    return recover_platform_segment(
        manifest_path=manifest,
        database_path=database,
        published_root=published,
        target_date="2026-09-26",
        segment_id="SEG-1300",
        failure_point=failure_point,
    )


def state(manifest: Path, tmp_path: Path) -> dict:
    database, published = runtime_paths(tmp_path)
    return load_platform_state(
        manifest_path=manifest,
        database_path=database,
        published_root=published,
    )


def test_initial_partial_then_recovery_matches_independent_fixture(tmp_path: Path) -> None:
    manifest = copy_fixture(tmp_path)
    independent = verify_fixture(manifest)
    initial = run_initial(manifest, tmp_path)
    assert initial["result_state"] == "partial"
    assert initial["summary"] == {
        "order_count": 7,
        "order_line_count": 10,
        "product_quantity": 14,
        "line_amount_krw": "310000",
    }
    assert state(manifest, tmp_path)["current_normal_result"] is None

    completed = recover(manifest, tmp_path)
    assert completed["result_state"] == "complete"
    assert completed["summary"] == {
        **independent["complete"],
        "line_amount_krw": str(independent["complete"]["line_amount_krw"]),
    }
    assert [(item["product_id"], item["product_quantity"], item["contributing_order_count"])
            for item in completed["products"]] == [
        ("P-101", 8, 4), ("P-205", 7, 4), ("P-330", 4, 3), ("P-410", 3, 3)
    ]
    current = state(manifest, tmp_path)
    assert current["current_normal_result"]["run"]["run_id"] == completed["run"]["run_id"]
    assert current["previous_normal_result"] is None
    assert current["previous_calculation_result"]["run"]["run_id"] == initial["run"]["run_id"]
    assert current["previous_calculation_result"]["summary"]["order_count"] == 7


def test_same_sources_reprocess_without_amplification(tmp_path: Path) -> None:
    manifest = copy_fixture(tmp_path)
    run_initial(manifest, tmp_path)
    first = recover(manifest, tmp_path)
    second = recover(manifest, tmp_path)
    assert first["summary"] == second["summary"]
    assert first["products"] == second["products"]
    database, _ = runtime_paths(tmp_path)
    connection = duckdb.connect(str(database), read_only=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM platform_source_files").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM platform_orders").fetchone()[0] == 10
        assert connection.execute("SELECT COUNT(*) FROM platform_order_lines").fetchone()[0] == 14
        assert connection.execute("SELECT COUNT(*) FROM platform_run_segments").fetchone()[0] == 11
    finally:
        connection.close()


def test_different_content_for_existing_order_is_rejected_and_preserves_partial(tmp_path: Path) -> None:
    manifest = copy_fixture(tmp_path)
    fixture = json.loads(manifest.read_text(encoding="utf-8"))
    recovery = manifest.parent / "segments" / "SEG-1300.json"
    payload = json.loads(recovery.read_text(encoding="utf-8"))
    payload["orders"][0]["order_id"] = "ORD-2601"
    recovery.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = hashlib.sha256(recovery.read_bytes()).hexdigest()
    next(item for item in fixture["expected_segments"] if item["segment_id"] == "SEG-1300")["expected_sha256"] = digest
    manifest.write_text(json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    initial = run_initial(manifest, tmp_path)
    with pytest.raises(PlatformFailure, match="order ID collision"):
        recover(manifest, tmp_path)
    current = state(manifest, tmp_path)
    assert current["latest_calculation"]["run"]["run_id"] == initial["run"]["run_id"]
    assert current["current_normal_result"] is None
    database, _ = runtime_paths(tmp_path)
    connection = duckdb.connect(str(database), read_only=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM platform_orders").fetchone()[0] == 7
        assert connection.execute("SELECT COUNT(*) FROM platform_run_segments WHERE run_id <> ?", [initial["run"]["run_id"]]).fetchone()[0] == 0
    finally:
        connection.close()


def test_missing_source_and_pre_publish_failure_do_not_replace_last_good(tmp_path: Path) -> None:
    manifest = copy_fixture(tmp_path)
    run_initial(manifest, tmp_path)
    good = recover(manifest, tmp_path)
    with pytest.raises(PlatformFailure, match="before publish"):
        recover(manifest, tmp_path, failure_point="after_artifact")
    assert state(manifest, tmp_path)["current_normal_result"]["run"]["run_id"] == good["run"]["run_id"]

    recovery_source = manifest.parent / "segments" / "SEG-1300.json"
    recovery_source.rename(recovery_source.with_suffix(".unavailable"))
    with pytest.raises(PlatformFailure, match="unavailable"):
        recover(manifest, tmp_path)
    after = state(manifest, tmp_path)
    assert after["current_normal_result"]["run"]["run_id"] == good["run"]["run_id"]
    assert after["recovery_plan"]["source_available"] is False


def test_prepare_refuses_existing_or_outside_runtime(tmp_path: Path) -> None:
    with pytest.raises(PlatformFailure, match="new child"):
        prepare_platform_runtime(tmp_path / "outside")
    existing = PROJECT_ROOT / "var" / "platform-smoke-20260927-a"
    with pytest.raises(PlatformFailure, match="already exists"):
        prepare_platform_runtime(existing)


def request_json(url: str, *, data: dict | None = None, origin: str | None = None):
    encoded = None if data is None else json.dumps(data).encode("utf-8")
    headers = {} if encoded is None else {"Content-Type": "application/json"}
    if origin is not None:
        headers["Origin"] = origin
    request = urllib.request.Request(url, data=encoded, headers=headers, method="POST" if encoded is not None else "GET")
    try:
        response = urllib.request.urlopen(request)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read()) if exc.headers.get_content_type() == "application/json" else None
    with response:
        return response.status, json.loads(response.read())


def test_loopback_api_serves_only_assets_and_allowlisted_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = copy_fixture(tmp_path)
    run_initial(manifest, tmp_path)
    runtime_root = tmp_path
    monkeypatch.setattr(
        "shopping_data.platform_server.platform_runtime_paths",
        lambda _runtime_root: runtime_paths(tmp_path),
    )
    server = make_platform_server(runtime_root=runtime_root, manifest_path=manifest, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        status, payload = request_json(f"{base}/api/platform/state")
        assert status == 200 and payload["displayed_result"]["summary"]["order_count"] == 7
        status, _ = request_json(
            f"{base}/api/platform/recover",
            data={"target_date": "2026-09-26", "segment_id": "SEG-1300"},
        )
        assert status == 403
        status, _ = request_json(
            f"{base}/api/platform/recover",
            data={"target_date": "2026-09-26", "segment_id": "SEG-1300", "source_path": "fixture.json"},
            origin=base,
        )
        assert status == 400
        database, _ = runtime_paths(tmp_path)
        lock_file, _ = _acquire_writer_lock(database, "test-owner")
        try:
            status, payload = request_json(
                f"{base}/api/platform/recover",
                data={"target_date": "2026-09-26", "segment_id": "SEG-1300"},
                origin=base,
            )
            assert status == 409 and payload["data_state"] == "busy"
        finally:
            _release_writer_lock(lock_file)
        status, payload = request_json(
            f"{base}/api/platform/recover",
            data={"target_date": "2026-09-26", "segment_id": "SEG-1300"},
            origin=base,
        )
        assert status == 200 and payload["status"] == "succeeded"
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{base}/../README.md")
        assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
