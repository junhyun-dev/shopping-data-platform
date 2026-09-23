SELECT
    t.source_file_id,
    CAST(invoice_timestamp AS DATE) AS observed_date,
    stock_code,
    SUM(quantity)::BIGINT AS sample_quantity_sum,
    COUNT(*)::BIGINT AS observed_row_count
FROM typed_transaction_lines AS t
JOIN run_input_rows AS i
  ON i.source_file_id = t.source_file_id
 AND i.sheet_name = t.sheet_name
 AND i.source_row_number = t.source_row_number
WHERE i.run_id = ?
GROUP BY t.source_file_id, observed_date, stock_code
ORDER BY observed_date, stock_code;
