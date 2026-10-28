-- SILVER.CUSTOMERS
-- Grain: one row per client_id (verified unique in bronze; enforced by DQ rule).
-- Cleaning decisions:

--   * country NULL -> 'ZZ' (ISO user-assigned "unknown") so group-bys never drop rows
--   * client_id is NOT rewritten even when malformed ('--2736-EURO'): it is the join
--     key used consistently in orders, so we flag it instead of "fixing" it and
--     breaking referential integrity.
--   * 'Registry' segment / '-EUROMNT' accounts are registry maintainer accounts
--     (36 IDs, ~12% of order lines, no subscription_id, no join_date). They are
--     kept (they are revenue) but flagged so customer-count KPIs can exclude them.
CREATE OR REPLACE TABLE silver.customers AS
WITH src AS (
    SELECT
        TRIM(client_id)                                         AS client_id,
        NULLIF(NULLIF(TRIM(client_country), 'NULL'), '')        AS client_country,
        SAFE_CAST(NULLIF(TRIM(join_date), 'NULL') AS DATE)      AS join_date,
        NULLIF(NULLIF(TRIM(segment), 'NULL'), '')               AS segment_code,
        _row_hash, _ingested_at
    FROM bronze.customers
),
cleaned AS (
    SELECT
        client_id,
        COALESCE(UPPER(client_country), 'ZZ')                   AS country_code,
        join_date, segment_code, _row_hash, _ingested_at
    FROM src
)
SELECT
    client_id,
    country_code,
    country_code = 'IT'                                         AS is_domestic,
    join_date,
    segment_code,
    CASE segment_code
        WHEN 'Ret-E'    THEN 'Retail'
        WHEN 'Ret-N'    THEN 'Retail'
        WHEN 'Res'      THEN 'Reseller'
        WHEN 'obp'      THEN 'OBP'
        WHEN 'Registry' THEN 'Registry'
        ELSE 'Unknown'
    END                                                         AS segment_group,
    (COALESCE(segment_code = 'Registry', FALSE) OR client_id LIKE '%-EUROMNT') AS is_registry_account,
    NOT REGEXP_CONTAINS(client_id, r'^[A-Z]+[0-9]+-EURO(MNT)?$') AS has_malformed_id,
    _row_hash,
    _ingested_at
FROM cleaned
WHERE TRUE
-- defensive dedup (source is unique today; this keeps the model idempotent if a
-- future snapshot ships duplicates)
QUALIFY ROW_NUMBER() OVER (PARTITION BY client_id ORDER BY join_date NULLS LAST, _row_hash) = 1;