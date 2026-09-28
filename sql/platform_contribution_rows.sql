SELECT
    CAST(po.target_date AS VARCHAR) AS observed_date,
    pol.product_id,
    po.order_id,
    pol.line_id,
    CAST(po.occurred_at AS VARCHAR) AS occurred_at,
    pol.segment_id,
    pol.qty,
    pol.unit_price_krw,
    CAST(CAST(pol.qty AS HUGEINT) * CAST(pol.unit_price_krw AS HUGEINT) AS VARCHAR) AS line_amount_krw,
    psf.source_file_id,
    psf.stored_path,
    pol.source_order_index,
    pol.source_line_index
FROM platform_run_segments AS prs
JOIN platform_orders AS po
  ON po.source_file_id = prs.source_file_id
 AND po.segment_id = prs.segment_id
 AND po.target_date = prs.target_date
JOIN platform_order_lines AS pol
  ON pol.order_id = po.order_id
 AND pol.source_file_id = prs.source_file_id
 AND pol.segment_id = prs.segment_id
JOIN platform_source_files AS psf
  ON psf.source_file_id = prs.source_file_id
WHERE prs.run_id = ?
ORDER BY po.occurred_at, po.order_id, pol.line_id;
