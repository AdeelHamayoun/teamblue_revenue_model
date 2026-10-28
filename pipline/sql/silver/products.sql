-- SILVER.PRODUCTS
-- Grain: one row per product_id.
-- Cleaning: the source file has trailing ",,,," / '"' garbage glued to the last
-- column (product_cat_desc) -> 'Hosting",,,,' becomes 'Hosting'. Without this the
-- category dimension has 7 values instead of 4 and category revenue is split.
-- One line (product 164202) is wrapped in an extra layer of CSV quoting. Because
-- bronze disables quote parsing, its fields arrive as '"164202', '""Managed ... ""',
-- 'Hosting",,,,' - we strip the stray quotes and un-double '""' instead of dropping
-- the row (otherwise 24 order lines would look orphaned).
CREATE OR REPLACE TABLE silver.products AS
WITH cleaned AS (
    SELECT
        SAFE_CAST(TRIM(product_id, '" ') AS INT64)                         AS product_id,
        TRIM(product_code, '" ')                                           AS product_code,
        TRIM(REPLACE(product_desc, '""', '"'), '" ')                       AS product_desc,
        TRIM(product_subgroup_desc)                                        AS product_subgroup,
        TRIM(product_group_desc)                                           AS product_group,
        TRIM(product_subcat_desc)                                          AS product_subcategory,
        NULLIF(TRIM(REGEXP_REPLACE(product_cat_desc, r'[,"\s]+$', '')), '') AS product_category,
        _row_hash, _ingested_at
    FROM bronze.products
)
SELECT *
FROM cleaned
WHERE TRUE
QUALIFY ROW_NUMBER() OVER (PARTITION BY product_id ORDER BY _row_hash) = 1;
