SELECT
    (CAST(occurred_at AS TIMESTAMPTZ) AT TIME ZONE 'UTC')::DATE AS observed_date,
    product_code,
    SUM(CAST(quantity AS BIGINT)) AS signed_quantity_sum,
    COUNT(*)::BIGINT AS unique_event_count
FROM fixture_events
GROUP BY observed_date, product_code
ORDER BY observed_date, product_code;
