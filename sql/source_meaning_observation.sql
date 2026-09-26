-- Read one selected run without assigning sales, cancellation, refund, or
-- original-transaction meaning. Description stays in the grouping so a
-- product-looking row and a D / Discount row cannot be collapsed together.
SELECT
    CAST(t.invoice_timestamp AS DATE) AS observed_date,
    t.stock_code,
    t.description,
    t.source_cancellation_marker,
    SUM(t.quantity) AS quantity_sum,
    COUNT(*) AS observed_row_count,
    LIST(t.source_row_number ORDER BY t.source_row_number)
        AS contributing_excel_rows
FROM typed_transaction_lines AS t
JOIN run_input_rows AS i
  ON i.source_file_id = t.source_file_id
 AND i.sheet_name = t.sheet_name
 AND i.source_row_number = t.source_row_number
WHERE i.run_id = ?
GROUP BY
    CAST(t.invoice_timestamp AS DATE),
    t.stock_code,
    t.description,
    t.source_cancellation_marker
ORDER BY
    observed_date,
    stock_code,
    description,
    source_cancellation_marker;
