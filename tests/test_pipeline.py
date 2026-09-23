from __future__ import annotations

import hashlib
import json
import os
import select
import signal
import subprocess
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import duckdb
import pytest
from openpyxl import Workbook, load_workbook

from shopping_data.pipeline import (
    PROCESS_CHECKPOINTS,
    PipelineBusy,
    PipelineFailure,
    compare_published_results,
    compare_results,
    load_current_result,
    list_published_results,
    load_run_history,
    run_pipeline,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


HEADERS = [
    "InvoiceNo",
    "StockCode",
    "Description",
    "Quantity",
    "InvoiceDate",
    "UnitPrice",
    "CustomerID",
    "Country",
]

# Adapted from UCI Online Retail (Chen, 2015), CC BY 4.0; see README.
# Customer IDs are replaced with the fictional TEST-CUSTOMER value.
# Additional rows exercise synthetic edge cases; this is not a source extract.
ROWS = {
    2: [536365, "85123A", "WHITE HANGING HEART T-LIGHT HOLDER", 6, datetime(2010, 12, 1, 8, 26), 2.55, "TEST-CUSTOMER", "United Kingdom"],
    3: [536365, 71053, "WHITE METAL LANTERN", 6, datetime(2010, 12, 1, 8, 26), 3.39, "TEST-CUSTOMER", "United Kingdom"],
    4: [536365, "84406B", "CREAM CUPID HEARTS COAT HANGER", 8, datetime(2010, 12, 1, 8, 26), 2.75, "TEST-CUSTOMER", "United Kingdom"],
    5: [536365, "84029G", "KNITTED UNION FLAG HOT WATER BOTTLE", 6, datetime(2010, 12, 1, 8, 26), 3.39, "TEST-CUSTOMER", "United Kingdom"],
    6: [536365, "84029E", "RED WOOLLY HOTTIE WHITE HEART.", 6, datetime(2010, 12, 1, 8, 26), 3.39, "TEST-CUSTOMER", "United Kingdom"],
    7: [536365, 22752, "SET 7 BABUSHKA NESTING BOXES", 2, datetime(2010, 12, 1, 8, 26), 7.65, "TEST-CUSTOMER", "United Kingdom"],
    8: [536365, 21730, "GLASS STAR FROSTED T-LIGHT HOLDER", 6, datetime(2010, 12, 1, 8, 26), 4.25, "TEST-CUSTOMER", "United Kingdom"],
    9: [536365, "ZERO", "ZERO QUANTITY TEST ROW", 0, datetime(2010, 12, 1, 8, 26), 1.0, "TEST-CUSTOMER", "United Kingdom"],
    10: [536365, "85123A", "WHITE HANGING HEART T-LIGHT HOLDER", 6, datetime(2010, 12, 1, 8, 26), 2.55, "TEST-CUSTOMER", "United Kingdom"],
    11: [None, "MISSING", "MISSING INVOICE NUMBER", 1, datetime(2010, 12, 1, 9, 0), 1.0, "TEST-CUSTOMER", "United Kingdom"],
    12: [" c-test", "LOWER-C", "LOWERCASE PREFIX", 1, datetime(2010, 12, 1, 9, 1), 1.0, "TEST-CUSTOMER", "United Kingdom"],
    13: ["   ", "BLANK", "BLANK INVOICE NUMBER", 1, datetime(2010, 12, 1, 9, 2), 1.0, "TEST-CUSTOMER", "United Kingdom"],
    14: [True, "UNSUPPORTED", "UNSUPPORTED INVOICE TYPE", 1, datetime(2010, 12, 1, 9, 3), 1.0, "TEST-CUSTOMER", "United Kingdom"],
    15: ["\t", "TAB-BLANK", "TAB-ONLY INVOICE NUMBER", 1, datetime(2010, 12, 1, 9, 4), 1.0, "TEST-CUSTOMER", "United Kingdom"],
    16: ["\tC123\n", "TAB-C", "C PREFIX AFTER TAB", 1, datetime(2010, 12, 1, 9, 5), 1.0, "TEST-CUSTOMER", "United Kingdom"],
    143: ["C536379", "D", "Discount", -1, datetime(2010, 12, 1, 9, 41), 27.5, "TEST-CUSTOMER", "United Kingdom"],
}


def write_workbook(path: Path, selected_rows: list[int]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Online Retail"
    for column, header in enumerate(HEADERS, start=1):
        sheet.cell(row=1, column=column, value=header)
    for row_number in selected_rows:
        for column, value in enumerate(ROWS[row_number], start=1):
            sheet.cell(row=row_number, column=column, value=value)
    workbook.save(path)


def write_manifest(path: Path, workbook_path: Path, selected_rows: list[int]) -> None:
    digest = hashlib.sha256(workbook_path.read_bytes()).hexdigest()
    path.write_text(
        json.dumps(
            {
                "dataset": "UCI-derived test workbook with synthetic edge cases",
                "source_url": "local-test-only",
                "expected_sha256": digest,
                "sheet": "Online Retail",
                "selected_excel_rows": selected_rows,
                "scope_note": "Synthetic test rows only.",
            }
        ),
        encoding="utf-8",
    )


def local_paths(tmp_path: Path, selected_rows: list[int] | None = None):
    selected_rows = selected_rows or [2, 3, 4, 5, 6, 7, 8, 143]
    workbook = tmp_path / "sample.xlsx"
    manifest = tmp_path / "manifest.json"
    database = tmp_path / "shopping.duckdb"
    published = tmp_path / "published"
    write_workbook(workbook, selected_rows)
    write_manifest(manifest, workbook, selected_rows)
    return workbook, manifest, database, published


def start_crash_worker(
    *,
    workbook: Path,
    manifest: Path,
    database: Path,
    published: Path,
    selected_rows: list[int],
    checkpoint: str,
) -> tuple[subprocess.Popen[str], dict]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(PROJECT_ROOT)
        if not existing_pythonpath
        else f"{PROJECT_ROOT}{os.pathsep}{existing_pythonpath}"
    )
    process = subprocess.Popen(
        [
            sys.executable,
            str(PROJECT_ROOT / "tests" / "crash_worker.py"),
            "--source",
            str(workbook),
            "--manifest",
            str(manifest),
            "--database",
            str(database),
            "--published-root",
            str(published),
            "--rows",
            *[str(row) for row in selected_rows],
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
    ready, _, _ = select.select([process.stdout], [], [], 20)
    if not ready:
        process.kill()
        stdout, stderr = process.communicate(timeout=10)
        raise AssertionError(
            f"child did not reach checkpoint {checkpoint}; stdout={stdout!r}, stderr={stderr!r}"
        )
    payload = json.loads(process.stdout.readline())
    assert payload["state"] == "ready_for_forced_termination"
    assert payload["checkpoint"] == checkpoint
    assert process.poll() is None
    return process, payload


def kill_owned_worker(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)
    assert process.returncode == -signal.SIGKILL


def product(result: dict, stock_code: str) -> dict:
    return next(item for item in result["products"] if item["stock_code"] == stock_code)


def test_repeated_input_is_idempotent_and_products_do_not_mix(tmp_path: Path) -> None:
    selected = [2, 3, 4, 5, 6, 7, 8, 10, 143]
    workbook, manifest, database, published = local_paths(tmp_path, selected)
    first = run_pipeline(
        source_path=workbook,
        manifest_path=manifest,
        database_path=database,
        published_root=published,
    )
    second = run_pipeline(
        source_path=workbook,
        manifest_path=manifest,
        database_path=database,
        published_root=published,
    )

    first_85123a = product(first, "85123A")
    second_85123a = product(second, "85123A")
    assert first_85123a["sample_quantity_sum"] == 12
    assert second_85123a["sample_quantity_sum"] == 12
    assert second_85123a["observed_row_count"] == 2
    assert [row["source_row_number"] for row in second_85123a["contributing_rows"]] == [2, 10]

    numeric_code = product(second, "71053")
    assert numeric_code["sample_quantity_sum"] == 6
    assert [row["source_row_number"] for row in numeric_code["contributing_rows"]] == [3]

    connection = duckdb.connect(str(database), read_only=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM source_files").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM source_rows").fetchone()[0] == 9
        assert connection.execute(
            "SELECT COUNT(*) FROM published_results WHERE is_current = TRUE"
        ).fetchone()[0] == 1
    finally:
        connection.close()

    current = load_current_result(database, published)
    assert current["run"]["run_id"] == second["run"]["run_id"]
    assert "customer" not in json.dumps(current).lower()


@pytest.mark.parametrize("failure_point", ["after_data", "after_artifact"])
def test_incomplete_run_does_not_replace_last_good_result(
    tmp_path: Path, failure_point: str
) -> None:
    workbook, manifest, database, published = local_paths(tmp_path)
    first = run_pipeline(
        source_path=workbook,
        manifest_path=manifest,
        database_path=database,
        published_root=published,
    )

    with pytest.raises(PipelineFailure, match="simulated failure"):
        run_pipeline(
            source_path=workbook,
            manifest_path=manifest,
            database_path=database,
            published_root=published,
            failure_point=failure_point,
        )

    current = load_current_result(database, published)
    assert current["run"]["run_id"] == first["run"]["run_id"]
    history = load_run_history(database)
    assert history[0]["status"] == "failed"
    assert history[1]["status"] == "succeeded"


def test_unreadable_input_does_not_replace_last_good_result(tmp_path: Path) -> None:
    workbook, manifest, database, published = local_paths(tmp_path)
    first = run_pipeline(
        source_path=workbook,
        manifest_path=manifest,
        database_path=database,
        published_root=published,
    )

    unreadable = tmp_path / "not-a-workbook.xlsx"
    unreadable.write_bytes(b"not an xlsx file")
    bad_manifest = tmp_path / "bad-manifest.json"
    write_manifest(bad_manifest, unreadable, [2])

    with pytest.raises(PipelineFailure, match="workbook could not be read"):
        run_pipeline(
            source_path=unreadable,
            manifest_path=bad_manifest,
            database_path=database,
            published_root=published,
        )

    assert load_current_result(database, published)["run"]["run_id"] == first["run"]["run_id"]


def test_fractional_quantity_is_rejected_without_replacing_last_good_result(
    tmp_path: Path,
) -> None:
    workbook, manifest, database, published = local_paths(tmp_path)
    first = run_pipeline(
        source_path=workbook,
        manifest_path=manifest,
        database_path=database,
        published_root=published,
    )

    fractional = tmp_path / "fractional.xlsx"
    write_workbook(fractional, [2])
    changed = load_workbook(fractional)
    changed["Online Retail"]["D2"] = 1.5
    changed["Online Retail"]["F2"] = 1.234567
    changed.save(fractional)
    changed.close()
    fractional_manifest = tmp_path / "fractional-manifest.json"
    write_manifest(fractional_manifest, fractional, [2])

    with pytest.raises(PipelineFailure, match="quantity cannot be represented losslessly"):
        run_pipeline(
            source_path=fractional,
            manifest_path=fractional_manifest,
            database_path=database,
            published_root=published,
        )

    assert load_current_result(database, published)["run"]["run_id"] == first["run"]["run_id"]
    history = load_run_history(database)
    assert history[0]["status"] == "failed"
    assert "Excel rows: 2" in history[0]["error_message"]


def test_unit_price_source_text_is_not_truncated(tmp_path: Path) -> None:
    workbook, manifest, database, published = local_paths(tmp_path, [2])
    changed = load_workbook(workbook)
    changed["Online Retail"]["D2"] = 1
    changed["Online Retail"]["F2"] = 1.234567
    changed.save(workbook)
    changed.close()
    write_manifest(manifest, workbook, [2])

    result = run_pipeline(
        source_path=workbook,
        manifest_path=manifest,
        database_path=database,
        published_root=published,
    )

    row = product(result, "85123A")["contributing_rows"][0]
    assert row["unit_price_source_text"] == "1.234567"


def test_zero_quantity_is_present_not_missing(tmp_path: Path) -> None:
    selected = [2, 9, 143]
    workbook, manifest, database, published = local_paths(tmp_path, selected)
    result = run_pipeline(
        source_path=workbook,
        manifest_path=manifest,
        database_path=database,
        published_root=published,
    )

    zero = product(result, "ZERO")
    assert zero["data_state"] == "available"
    assert zero["sample_quantity_sum"] == 0
    assert zero["observed_row_count"] == 1
    assert all(item["stock_code"] != "NOT-PRESENT" for item in result["products"])


def test_source_cancellation_marker_is_derived_without_changing_invoice_text(
    tmp_path: Path,
) -> None:
    selected = [2, 11, 12, 13, 14, 15, 16, 143]
    workbook, manifest, database, published = local_paths(tmp_path, selected)
    result = run_pipeline(
        source_path=workbook,
        manifest_path=manifest,
        database_path=database,
        published_root=published,
    )

    observed = {
        item["stock_code"]: item["contributing_rows"][0]
        for item in result["products"]
    }
    assert observed["85123A"]["invoice_no"] == "536365"
    assert observed["85123A"]["invoice_no_source_kind"] == "integer"
    assert observed["85123A"]["source_cancellation_marker"] == "false"
    assert observed["D"]["invoice_no"] == "C536379"
    assert observed["D"]["source_cancellation_marker"] == "true"
    assert observed["LOWER-C"]["invoice_no"] == " c-test"
    assert observed["LOWER-C"]["invoice_no_source_kind"] == "text"
    assert observed["LOWER-C"]["source_cancellation_marker"] == "true"
    assert observed["MISSING"]["invoice_no"] is None
    assert observed["MISSING"]["invoice_no_source_kind"] == "missing"
    assert observed["MISSING"]["source_cancellation_marker"] == "unknown"
    assert observed["BLANK"]["invoice_no"] == "   "
    assert observed["BLANK"]["source_cancellation_marker"] == "unknown"
    assert observed["UNSUPPORTED"]["invoice_no"] == "True"
    assert observed["UNSUPPORTED"]["invoice_no_source_kind"] == "unsupported"
    assert observed["UNSUPPORTED"]["source_cancellation_marker"] == "unknown"
    assert observed["TAB-BLANK"]["invoice_no"] == "\t"
    assert observed["TAB-BLANK"]["source_cancellation_marker"] == "unknown"
    assert observed["TAB-C"]["invoice_no"] == "\tC123\n"
    assert observed["TAB-C"]["source_cancellation_marker"] == "true"
    assert result["transformation_rule_id"].startswith("sha256:")
    assert "archive.ics.uci.edu" in result["source_cancellation_marker_contract"][
        "official_definition_url"
    ]


def test_actual_uci_sample_matches_independent_workbook_calculation(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    source = project_root / "data" / "source" / "Online Retail.xlsx"
    manifest = project_root / "config" / "source.json"
    if not source.exists():
        pytest.skip("local preserved UCI workbook is not available")

    result = run_pipeline(
        source_path=source,
        manifest_path=manifest,
        database_path=tmp_path / "actual.duckdb",
        published_root=tmp_path / "published",
    )

    config = json.loads(manifest.read_text(encoding="utf-8"))
    selected = set(config["selected_excel_rows"])
    independent_totals: dict[str, dict[str, int]] = {}
    workbook = load_workbook(source, read_only=True, data_only=True)
    try:
        sheet = workbook[config["sheet"]]
        for row_number, row in enumerate(
            sheet.iter_rows(min_row=2, max_row=max(selected), max_col=8, values_only=True),
            start=2,
        ):
            if row_number not in selected:
                continue
            key = str(row[1])
            current = independent_totals.setdefault(key, {"quantity": 0, "rows": 0})
            current["quantity"] += int(row[3])
            current["rows"] += 1
    finally:
        workbook.close()

    for item in result["products"]:
        expected = independent_totals[item["stock_code"]]
        assert item["sample_quantity_sum"] == expected["quantity"]
        assert item["observed_row_count"] == expected["rows"]

    assert product(result, "85123A")["sample_quantity_sum"] == 6
    assert product(result, "D")["sample_quantity_sum"] == -1
    assert product(result, "85123A")["contributing_rows"][0][
        "source_cancellation_marker"
    ] == "false"
    assert product(result, "D")["contributing_rows"][0][
        "source_cancellation_marker"
    ] == "true"


def test_run_scope_is_separate_from_preserved_source_rows_and_can_be_compared(
    tmp_path: Path,
) -> None:
    workbook, manifest, database, published = local_paths(tmp_path, [2, 3])

    first = run_pipeline(
        source_path=workbook,
        manifest_path=manifest,
        database_path=database,
        published_root=published,
        selected_rows=[2, 3],
    )
    second = run_pipeline(
        source_path=workbook,
        manifest_path=manifest,
        database_path=database,
        published_root=published,
        selected_rows=[3],
    )
    third = run_pipeline(
        source_path=workbook,
        manifest_path=manifest,
        database_path=database,
        published_root=published,
        selected_rows=[3, 2],
    )

    assert first["source"]["selected_excel_rows"] == [2, 3]
    assert second["source"]["selected_excel_rows"] == [3]
    assert third["source"]["selected_excel_rows"] == [2, 3]
    assert first["source"]["input_scope_id"] == third["source"]["input_scope_id"]
    assert product(first, "85123A")["sample_quantity_sum"] == 6
    assert [item["stock_code"] for item in second["products"]] == ["71053"]
    assert product(third, "85123A")["sample_quantity_sum"] == 6

    connection = duckdb.connect(str(database), read_only=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM source_files").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM source_rows").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM run_input_scopes").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM run_input_rows").fetchone()[0] == 5
    finally:
        connection.close()

    comparison = compare_published_results(
        database,
        published,
        base_run_id=first["run"]["run_id"],
        current_run_id=second["run"]["run_id"],
    )
    assert comparison["change_basis"]["code"] == "selection_scope_changed"
    assert comparison["change_basis"]["selected_rows_added"] == []
    assert comparison["change_basis"]["selected_rows_removed"] == [2]
    changed = next(
        item for item in comparison["groups"] if item["stock_code"] == "85123A"
    )
    assert changed["base_sample_quantity_sum"] == 6
    assert changed["current_state"] == "not_present"
    assert changed["removed_source_rows"] == [
        {
            "source_file_id": first["source"]["source_file_id"],
            "sheet": "Online Retail",
            "source_row_number": 2,
        }
    ]

    results = list_published_results(database, published)
    assert [item["run_id"] for item in results] == [
        third["run"]["run_id"],
        second["run"]["run_id"],
        first["run"]["run_id"],
    ]
    assert results[0]["is_current"] is True


def test_failed_new_scope_does_not_replace_or_become_comparable_result(
    tmp_path: Path,
) -> None:
    workbook, manifest, database, published = local_paths(tmp_path, [2, 3])
    first = run_pipeline(
        source_path=workbook,
        manifest_path=manifest,
        database_path=database,
        published_root=published,
        selected_rows=[2, 3],
    )

    with pytest.raises(PipelineFailure, match="simulated failure"):
        run_pipeline(
            source_path=workbook,
            manifest_path=manifest,
            database_path=database,
            published_root=published,
            selected_rows=[3],
            failure_point="after_artifact",
        )

    assert load_current_result(database, published)["run"]["run_id"] == first["run"]["run_id"]
    assert [item["run_id"] for item in list_published_results(database, published)] == [
        first["run"]["run_id"]
    ]


def test_file_or_sql_change_is_not_labeled_as_selection_only(tmp_path: Path) -> None:
    workbook, manifest, database, published = local_paths(tmp_path, [2])
    base = run_pipeline(
        source_path=workbook,
        manifest_path=manifest,
        database_path=database,
        published_root=published,
    )
    changed = deepcopy(base)
    changed["run"]["run_id"] = "different-result"
    changed["source"]["source_file_id"] = "sha256:different-source"
    changed["source"]["sha256"] = "different-source"
    changed["source"]["input_scope_id"] = "sha256:different-scope"
    changed["aggregation_rule_id"] = "sha256:different-rule"

    comparison = compare_results(base, changed)

    assert comparison["change_basis"]["code"] == "source_sheet_or_rule_changed"
    assert comparison["change_basis"]["source_file_changed"] is True
    assert comparison["change_basis"]["aggregation_rule_changed"] is True

    transformation_changed = deepcopy(base)
    transformation_changed["run"]["run_id"] = "different-transformation"
    transformation_changed["transformation_rule_id"] = "sha256:different-transformation"
    transformation_comparison = compare_results(base, transformation_changed)
    assert transformation_comparison["change_basis"]["code"] == "source_sheet_or_rule_changed"
    assert transformation_comparison["change_basis"]["transformation_rule_changed"] is True

    legacy = deepcopy(base)
    legacy["run"]["run_id"] = "legacy-result"
    legacy.pop("transformation_rule_id")
    legacy.pop("source_cancellation_marker_contract")
    legacy.pop("applied_transformation_sql")
    for item in legacy["products"]:
        for row in item["contributing_rows"]:
            row.pop("source_cancellation_marker")
    legacy_comparison = compare_results(legacy, base)
    assert legacy_comparison["change_basis"]["transformation_rule_changed"] is True


@pytest.mark.parametrize("checkpoint", PROCESS_CHECKPOINTS)
def test_forced_process_termination_recovers_from_database_evidence(
    tmp_path: Path, checkpoint: str
) -> None:
    workbook, manifest, database, published = local_paths(tmp_path, [2, 3])
    baseline = run_pipeline(
        source_path=workbook,
        manifest_path=manifest,
        database_path=database,
        published_root=published,
        selected_rows=[2],
    )
    process, barrier = start_crash_worker(
        workbook=workbook,
        manifest=manifest,
        database=database,
        published=published,
        selected_rows=[3],
        checkpoint=checkpoint,
    )
    try:
        kill_owned_worker(process)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)

    crashed_run_id = barrier["run_id"]
    current_after_death = load_current_result(database, published)
    expected_current = (
        crashed_run_id
        if checkpoint == "after_publish_commit"
        else baseline["run"]["run_id"]
    )
    assert current_after_death["run"]["run_id"] == expected_current

    before_recovery = {
        item["run_id"]: item for item in load_run_history(database)
    }[crashed_run_id]
    expected_status = (
        "succeeded" if checkpoint == "after_publish_commit" else "running"
    )
    assert before_recovery["status"] == expected_status
    assert before_recovery["recovered_at"] is None

    connection = duckdb.connect(str(database), read_only=True)
    try:
        rows_after_death = connection.execute(
            "SELECT COUNT(*) FROM source_rows"
        ).fetchone()[0]
        crashed_membership = connection.execute(
            "SELECT COUNT(*) FROM run_input_rows WHERE run_id = ?",
            [crashed_run_id],
        ).fetchone()[0]
    finally:
        connection.close()
    if checkpoint == "before_data_commit":
        assert rows_after_death == 1
        assert crashed_membership == 0
    else:
        assert rows_after_death == 2
        assert crashed_membership == 1

    active_artifact = published / f"{crashed_run_id}.json"
    assert active_artifact.exists() is (
        checkpoint in {"after_artifact_write", "after_publish_commit"}
    )
    successful_before_recovery = {
        item["run_id"] for item in list_published_results(database, published)
    }
    assert baseline["run"]["run_id"] in successful_before_recovery
    assert (crashed_run_id in successful_before_recovery) is (
        checkpoint == "after_publish_commit"
    )

    retried = run_pipeline(
        source_path=workbook,
        manifest_path=manifest,
        database_path=database,
        published_root=published,
        selected_rows=[2, 3],
    )
    assert {item["stock_code"] for item in retried["products"]} == {
        "71053",
        "85123A",
    }
    assert product(retried, "85123A")["sample_quantity_sum"] == 6
    assert product(retried, "71053")["sample_quantity_sum"] == 6
    assert load_current_result(database, published)["run"]["run_id"] == retried[
        "run"
    ]["run_id"]

    history = {item["run_id"]: item for item in load_run_history(database)}
    recovered = history[crashed_run_id]
    successful_after_recovery = {
        item["run_id"] for item in list_published_results(database, published)
    }
    if checkpoint == "after_publish_commit":
        assert recovered["status"] == "succeeded"
        assert recovered["recovered_at"] is None
        assert crashed_run_id in successful_after_recovery
        assert active_artifact.is_file()
    else:
        assert recovered["status"] == "failed"
        assert recovered["recovered_at"] is not None
        assert "previous writer process ended" in recovered["recovery_reason"]
        assert crashed_run_id not in successful_after_recovery
        if checkpoint == "after_artifact_write":
            orphaned = published / "orphaned" / f"{crashed_run_id}.json"
            assert not active_artifact.exists()
            assert orphaned.is_file()
            assert recovered["recovered_artifact_path"] == str(orphaned.resolve())
        else:
            assert recovered["recovered_artifact_path"] is None

    connection = duckdb.connect(str(database), read_only=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM source_files").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM source_rows").fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM published_results WHERE is_current = TRUE"
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_live_writer_lock_rejects_second_writer_without_recovering_it(
    tmp_path: Path,
) -> None:
    workbook, manifest, database, published = local_paths(tmp_path, [2, 3])
    run_pipeline(
        source_path=workbook,
        manifest_path=manifest,
        database_path=database,
        published_root=published,
        selected_rows=[2],
    )
    process, barrier = start_crash_worker(
        workbook=workbook,
        manifest=manifest,
        database=database,
        published=published,
        selected_rows=[3],
        checkpoint="after_data_commit",
    )
    try:
        with pytest.raises(PipelineBusy, match="another local pipeline writer"):
            run_pipeline(
                source_path=workbook,
                manifest_path=manifest,
                database_path=database,
                published_root=published,
                selected_rows=[2, 3],
            )
        lock_state = json.loads(
            database.with_name(f"{database.name}.writer.lock").read_text(
                encoding="utf-8"
            )
        )
        assert lock_state["owner_pid"] == process.pid
        assert process.poll() is None
    finally:
        kill_owned_worker(process)

    child_history = {
        item["run_id"]: item for item in load_run_history(database)
    }[barrier["run_id"]]
    assert child_history["status"] == "running"
    assert child_history["recovered_at"] is None

    retried = run_pipeline(
        source_path=workbook,
        manifest_path=manifest,
        database_path=database,
        published_root=published,
        selected_rows=[2, 3],
    )
    recovered = {
        item["run_id"]: item for item in load_run_history(database)
    }[barrier["run_id"]]
    assert recovered["status"] == "failed"
    assert recovered["recovered_at"] is not None
    assert product(retried, "85123A")["sample_quantity_sum"] == 6
    assert product(retried, "71053")["sample_quantity_sum"] == 6
