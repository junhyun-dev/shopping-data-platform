from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "data" / "source" / "Online Retail.xlsx"
DEFAULT_MANIFEST = PROJECT_ROOT / "config" / "source.json"
DEFAULT_DATABASE = PROJECT_ROOT / "var" / "shopping.duckdb"
DEFAULT_PUBLISHED_ROOT = PROJECT_ROOT / "published" / "runs"
WEB_ROOT = PROJECT_ROOT / "web"
SCHEMA_SQL = PROJECT_ROOT / "sql" / "schema.sql"
TRANSFORMATION_SQL = PROJECT_ROOT / "sql" / "typed_transaction_lines.sql"
AGGREGATE_SQL = PROJECT_ROOT / "sql" / "sample_product_daily.sql"
CONTRIBUTIONS_SQL = PROJECT_ROOT / "sql" / "contribution_rows.sql"
