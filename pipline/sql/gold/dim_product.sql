-- GOLD.DIM_PRODUCT  (full refresh, SCD1)
-- Grain: one row per product_id, plus "unknown member" rows for product_ids that
-- appear in orders but not in the product master (164202 if its row were lost).
-- Facts never drop revenue because of a missing dimension row; it shows up as 'Unknown'.
CREATE OR REPLACE TABLE gold.dim_product AS
SELECT
    product_id, product_code, product_desc, product_subgroup, product_group,
    product_subcategory, product_category,
    FALSE AS is_unknown_member
FROM silver.products
UNION ALL
SELECT DISTINCT
    o.product_id,
    CAST(NULL AS STRING),
    'Unknown product (missing from master)', 'Unknown', 'Unknown',
    'Unknown', 'Unknown', TRUE
FROM silver.order_lines o
LEFT JOIN silver.products p ON p.product_id = o.product_id
WHERE p.product_id IS NULL
UNION ALL
SELECT -1, CAST(NULL AS STRING), 'Not applicable', 'Unknown', 'Unknown', 'Unknown', 'Unknown', TRUE;
