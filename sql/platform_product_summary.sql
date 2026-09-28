WITH selected_lines AS (
    SELECT
        prs.run_id,
        po.target_date,
        pol.product_id,
        po.order_id,
        pol.line_id,
        pol.qty,
        pol.unit_price_krw
    FROM platform_run_segments AS prs
    JOIN platform_orders AS po
      ON po.source_file_id = prs.source_file_id
     AND po.segment_id = prs.segment_id
     AND po.target_date = prs.target_date
    JOIN platform_order_lines AS pol
      ON pol.order_id = po.order_id
     AND pol.source_file_id = prs.source_file_id
     AND pol.segment_id = prs.segment_id
    WHERE prs.run_id = ?
)
SELECT
    CAST(target_date AS VARCHAR) AS observed_date,
    product_id,
    SUM(qty) AS product_quantity,
    COUNT(DISTINCT order_id) AS contributing_order_count,
    COUNT(*) AS order_line_count,
    SUM(CAST(qty AS HUGEINT) * CAST(unit_price_krw AS HUGEINT)) AS line_amount_krw
FROM selected_lines
GROUP BY target_date, product_id
ORDER BY target_date, product_id;
