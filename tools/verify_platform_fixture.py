from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROJECT_ROOT / "fixtures" / "platform" / "2026-09-26" / "manifest.json"


def verify_fixture(manifest_path: Path) -> dict:
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    products = {item["product_id"]: item["name"] for item in manifest["products"]}
    by_product: dict[str, dict] = defaultdict(
        lambda: {"quantity": 0, "orders": set(), "lines": 0, "line_amount_krw": 0, "locations": []}
    )
    by_segment = {}
    all_orders = set()
    for segment in manifest["expected_segments"]:
        path = (manifest_path.parent / segment["source_file"]).resolve()
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != segment["expected_sha256"]:
            raise ValueError(f"SHA mismatch: {segment['segment_id']}")
        source = json.loads(raw)
        segment_summary = {"orders": 0, "lines": 0, "quantity": 0, "line_amount_krw": 0}
        for order_index, order in enumerate(source["orders"]):
            all_orders.add(order["order_id"])
            segment_summary["orders"] += 1
            for line_index, line in enumerate(order["lines"]):
                if line["product_id"] not in products:
                    raise ValueError(f"unknown product: {line['product_id']}")
                amount = line["qty"] * line["unit_price_krw"]
                item = by_product[line["product_id"]]
                item["quantity"] += line["qty"]
                item["orders"].add(order["order_id"])
                item["lines"] += 1
                item["line_amount_krw"] += amount
                item["locations"].append(
                    {
                        "segment_id": segment["segment_id"],
                        "order_index": order_index,
                        "line_index": line_index,
                        "order_id": order["order_id"],
                        "line_id": line["line_id"],
                    }
                )
                segment_summary["lines"] += 1
                segment_summary["quantity"] += line["qty"]
                segment_summary["line_amount_krw"] += amount
        by_segment[segment["segment_id"]] = segment_summary

    normalized_products = {
        product_id: {
            "name": products[product_id],
            "quantity": item["quantity"],
            "order_count": len(item["orders"]),
            "line_count": item["lines"],
            "line_amount_krw": item["line_amount_krw"],
            "locations": item["locations"],
        }
        for product_id, item in sorted(by_product.items())
    }
    complete = {
        "order_count": len(all_orders),
        "order_line_count": sum(item["lines"] for item in by_segment.values()),
        "product_quantity": sum(item["quantity"] for item in by_segment.values()),
        "line_amount_krw": sum(item["line_amount_krw"] for item in by_segment.values()),
    }
    initial_ids = set(manifest["initial_loaded_segments"])
    initial = {
        "order_count": sum(item["orders"] for key, item in by_segment.items() if key in initial_ids),
        "order_line_count": sum(item["lines"] for key, item in by_segment.items() if key in initial_ids),
        "product_quantity": sum(item["quantity"] for key, item in by_segment.items() if key in initial_ids),
        "line_amount_krw": sum(item["line_amount_krw"] for key, item in by_segment.items() if key in initial_ids),
    }
    return {
        "manifest": str(manifest_path),
        "target_date": manifest["target_date"],
        "initial": initial,
        "complete": complete,
        "segments": by_segment,
        "products": normalized_products,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Independently total the synthetic platform fixture")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    print(json.dumps(verify_fixture(args.manifest), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
