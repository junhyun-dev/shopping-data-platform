from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook

from shopping_data.source_meaning import observe_source_meaning


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


def write_synthetic_source(path: Path) -> list[int]:
    rows = {
        2: [
            100,
            "P-1",
            "SYNTHETIC PRODUCT",
            1,
            datetime(2026, 9, 25, 9),
            1,
            "TEST-CUSTOMER",
            "Test",
        ],
        3: [
            101,
            "P-1",
            "SYNTHETIC PRODUCT",
            -1,
            datetime(2026, 9, 25, 10),
            1,
            "TEST-CUSTOMER",
            "Test",
        ],
        4: [
            "\tC102\n",
            "P-1",
            "SYNTHETIC PRODUCT",
            -1,
            datetime(2026, 9, 25, 11),
            1,
            "TEST-CUSTOMER",
            "Test",
        ],
        5: [
            None,
            "P-1",
            "SYNTHETIC PRODUCT",
            2,
            datetime(2026, 9, 25, 12),
            1,
            "TEST-CUSTOMER",
            "Test",
        ],
    }
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Online Retail"
    sheet.append(HEADERS)
    for row_number, values in rows.items():
        for column, value in enumerate(values, start=1):
            sheet.cell(row=row_number, column=column, value=value)
    workbook.save(path)
    return list(rows)


def write_manifest(path: Path, source: Path, selected_rows: list[int]) -> None:
    path.write_text(
        json.dumps(
            {
                "dataset": "explicit synthetic source-marker counterexample",
                "source_url": "local-test-only",
                "expected_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "sheet": "Online Retail",
                "selected_excel_rows": selected_rows,
                "scope_note": "Synthetic marker separation and zero-value test only.",
            }
        ),
        encoding="utf-8",
    )


def test_actual_three_rows_are_observed_separately_and_match_openpyxl() -> None:
    project_root = Path(__file__).resolve().parents[1]
    source = project_root / "data" / "source" / "Online Retail.xlsx"
    if not source.exists():
        pytest.skip("local preserved UCI workbook is not available")

    result = observe_source_meaning(
        source_path=source,
        manifest_path=project_root / "config" / "source.json",
    )

    assert result["source"]["selected_excel_rows"] == [2, 143, 156]
    assert result["source"]["sha256"] == (
        "43465a06f2ccf7c8b5bd2892bc7defb52f97487934fe93b16ae4c3936424676d"
    )
    assert result["groups"] == [
        {
            "observed_date": "2010-12-01",
            "stock_code": "35004C",
            "description": "SET OF 3 COLOURED  FLYING DUCKS",
            "source_cancellation_marker": "true",
            "quantity_sum": -1,
            "observed_row_count": 1,
            "contributing_excel_rows": [156],
        },
        {
            "observed_date": "2010-12-01",
            "stock_code": "85123A",
            "description": "WHITE HANGING HEART T-LIGHT HOLDER",
            "source_cancellation_marker": "false",
            "quantity_sum": 6,
            "observed_row_count": 1,
            "contributing_excel_rows": [2],
        },
        {
            "observed_date": "2010-12-01",
            "stock_code": "D",
            "description": "Discount",
            "source_cancellation_marker": "true",
            "quantity_sum": -1,
            "observed_row_count": 1,
            "contributing_excel_rows": [143],
        },
    ]
    assert result["independent_check"]["matches"] is True
    assert result["independent_check"]["groups"] == result["groups"]
    assert result["original_sale_date"]["state"] == (
        "not_calculable_from_observed_fields"
    )
    assert "CustomerID" not in json.dumps(result, ensure_ascii=False)


def test_same_product_keeps_true_false_unknown_and_zero_distinct(
    tmp_path: Path,
) -> None:
    source = tmp_path / "synthetic.xlsx"
    selected_rows = write_synthetic_source(source)
    manifest = tmp_path / "manifest.json"
    write_manifest(manifest, source, selected_rows)

    result = observe_source_meaning(
        source_path=source,
        manifest_path=manifest,
        selected_rows=selected_rows,
    )

    groups = {
        group["source_cancellation_marker"]: group for group in result["groups"]
    }
    assert groups["false"]["quantity_sum"] == 0
    assert groups["false"]["observed_row_count"] == 2
    assert groups["false"]["contributing_excel_rows"] == [2, 3]
    assert groups["true"]["quantity_sum"] == -1
    assert groups["true"]["observed_row_count"] == 1
    assert groups["true"]["contributing_excel_rows"] == [4]
    assert groups["unknown"]["quantity_sum"] == 2
    assert groups["unknown"]["observed_row_count"] == 1
    assert groups["unknown"]["contributing_excel_rows"] == [5]
    assert result["independent_check"]["groups"] == result["groups"]
    assert all(group["observed_row_count"] > 0 for group in result["groups"])
