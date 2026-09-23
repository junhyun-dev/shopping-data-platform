-- UCI Online Retail variable definition:
-- InvoiceNo starts with the letter C when the transaction is a cancellation.
-- https://archive.ics.uci.edu/dataset/352/online+retail
--
-- This view preserves invoice_no_raw unchanged as invoice_no. For this marker
-- only, the supported surrounding whitespace set is ASCII space, tab, CR, and
-- LF. Missing, supported-whitespace-only, and unsupported source-cell types
-- are unknown; no C/c prefix means false, not "confirmed sale".
CREATE OR REPLACE VIEW typed_transaction_lines AS
WITH observed_invoice_numbers AS (
    SELECT
        *,
        TRIM(invoice_no_raw, CONCAT(' ', CHR(9), CHR(13), CHR(10)))
            AS observed_invoice_no
    FROM source_rows
)
SELECT
    source_file_id,
    sheet_name,
    source_row_number,
    invoice_no_raw AS invoice_no,
    CASE
        WHEN invoice_no_source_kind IS NULL
          OR invoice_no_source_kind IN ('missing', 'unsupported')
          OR invoice_no_raw IS NULL
          OR observed_invoice_no = '' THEN 'unknown'
        WHEN UPPER(LEFT(observed_invoice_no, 1)) = 'C' THEN 'true'
        ELSE 'false'
    END AS source_cancellation_marker,
    invoice_no_source_kind,
    stock_code_raw AS stock_code,
    description_raw AS description,
    quantity_raw AS quantity_source_text,
    TRY_CAST(quantity_raw AS BIGINT) AS quantity,
    TRY_CAST(invoice_date_raw AS TIMESTAMP) AS invoice_timestamp,
    unit_price_raw AS unit_price_source_text,
    country_raw AS country
FROM observed_invoice_numbers;
