SELECT
    t.sheet_name,
    t.source_row_number,
    t.invoice_no,
    t.source_cancellation_marker,
    t.invoice_no_source_kind,
    t.stock_code,
    t.description,
    t.quantity,
    t.invoice_timestamp,
    t.unit_price_source_text,
    t.country
FROM typed_transaction_lines AS t
JOIN run_input_rows AS i
  ON i.source_file_id = t.source_file_id
 AND i.sheet_name = t.sheet_name
 AND i.source_row_number = t.source_row_number
WHERE i.run_id = ?
ORDER BY
    CAST(t.invoice_timestamp AS DATE),
    t.stock_code,
    t.source_row_number;
