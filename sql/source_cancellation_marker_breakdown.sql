-- Explain one product/date result by the observed InvoiceNo C/c-prefix marker.
-- This is not a sale, product-cancellation, refund, or original-transaction
-- classification. Description remains on the contributing source rows and is
-- deliberately not part of this product result grain.
SELECT
    CAST(t.invoice_timestamp AS DATE) AS observed_date,
    t.stock_code,
    t.source_cancellation_marker,
    SUM(t.quantity) AS signed_quantity_sum,
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
    t.source_cancellation_marker
ORDER BY
    observed_date,
    stock_code,
    CASE t.source_cancellation_marker
        WHEN 'true' THEN 1
        WHEN 'false' THEN 2
        WHEN 'unknown' THEN 3
        ELSE 4
    END;
