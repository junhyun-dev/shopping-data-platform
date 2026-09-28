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
SOURCE_CANCELLATION_BREAKDOWN_SQL = (
    PROJECT_ROOT / "sql" / "source_cancellation_marker_breakdown.sql"
)
SOURCE_MEANING_SQL = PROJECT_ROOT / "sql" / "source_meaning_observation.sql"
KAFKA_RUNTIME_CONFIG = PROJECT_ROOT / "config" / "kafka-runtime.json"
KAFKA_EVENTS = PROJECT_ROOT / "config" / "kafka-events.jsonl"
KAFKA_DELIVERIES = PROJECT_ROOT / "config" / "kafka-deliveries.jsonl"
KAFKA_CONFLICT_EVENT = PROJECT_ROOT / "config" / "kafka-conflict-event.json"
KAFKA_SINK_SCHEMA_SQL = PROJECT_ROOT / "sql" / "kafka_sink_schema.sql"
KAFKA_FIXTURE_BATCH_SQL = PROJECT_ROOT / "sql" / "kafka_fixture_batch.sql"
KAFKA_DISTRIBUTION_ROOT = PROJECT_ROOT / "var" / "kafka"
PLATFORM_FIXTURE_MANIFEST = (
    PROJECT_ROOT / "fixtures" / "platform" / "2026-09-26" / "manifest.json"
)
PLATFORM_SCHEMA_SQL = PROJECT_ROOT / "sql" / "platform_schema.sql"
PLATFORM_PRODUCT_SUMMARY_SQL = PROJECT_ROOT / "sql" / "platform_product_summary.sql"
PLATFORM_CONTRIBUTIONS_SQL = PROJECT_ROOT / "sql" / "platform_contribution_rows.sql"
