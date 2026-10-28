-- SILVER.SUBSCRIPTIONS
-- Grain: one row per subscription_id. This table is the SYSTEM OF RECORD for
-- subscription lifecycle (start -> expire). It is the basis of Active/Churn.
--
-- Why not derive lifecycle from orders? Profiling showed the orders extract is a
-- sample: of 82,933 subscriptions alive throughout 2020, only 22% have a 2020
-- billing line. Order gaps therefore do NOT mean churn.
--
-- status is a *current-state* snapshot attribute (no history), mapped to a
-- lifecycle group in config.STATUS_GROUPS (seeded into meta.status_map).
CREATE OR REPLACE TABLE silver.subscriptions AS
WITH src AS (
    SELECT
        SAFE_CAST(TRIM(subscription_id) AS INT64)                AS subscription_id,
        SAFE_CAST(TRIM(creation_date) AS DATE)                   AS creation_date,
        SAFE_CAST(TRIM(start_date) AS DATE)                      AS start_date,
        SAFE_CAST(TRIM(expire_date) AS DATE)                     AS expire_date,
        NULLIF(NULLIF(TRIM(status), 'NULL'), '')                 AS status,
        TRIM(product_code)                                       AS product_code,
        _row_hash, _ingested_at
    FROM bronze.subscriptions
)
SELECT
    s.subscription_id,
    s.creation_date,
    s.start_date,
    s.expire_date,
    s.status,
    COALESCE(m.status_group, 'Unmapped')                         AS status_group,
    COALESCE(m.status_group, 'Unmapped') IN ('Terminated', 'Transferred out') AS is_terminal_status,
    s.product_code,
    s.start_date < s.creation_date                               AS started_before_created,  -- migrated records
    s.expire_date < s.start_date                                 AS has_invalid_period,
    s._row_hash,
    s._ingested_at
FROM src s
LEFT JOIN meta.status_map m ON m.status IS NOT DISTINCT FROM s.status
WHERE TRUE
QUALIFY ROW_NUMBER() OVER (PARTITION BY s.subscription_id ORDER BY s.creation_date DESC, s._row_hash) = 1;

-- Quarantine: rows that cannot form a valid lifecycle are excluded from gold
-- (kept here for audit + DQ trend).
CREATE OR REPLACE TABLE silver.quarantine_subscriptions AS
SELECT *, 'expire_date < start_date' AS reject_reason
FROM silver.subscriptions
WHERE has_invalid_period;