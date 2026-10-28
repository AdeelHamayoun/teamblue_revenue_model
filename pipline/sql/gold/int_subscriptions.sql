-- GOLD.INT_SUBSCRIPTIONS  (full refresh; intermediate, not exposed to BI)
-- Grain: one row per subscription_key.
--
-- Reconciles the two halves of the truth:
--   * LIFECYCLE (is it alive in a month?)  <- silver.subscriptions (start/expire)
--   * PRICE     (what is it worth?)        <- silver.order_lines (recurring lines)
--
-- Two lifecycle sources:
--   'subscription_master' : subscription_id exists in the subscription master
--   'order_period'        : recurring lines with no (valid) subscription_id
--                           (registry maintainer accounts + 4 orphan ids). With no
--                           master record, the billing period itself is the lifecycle.

-- usable price evidence for MRR: recurring, not fully refunded
CREATE OR REPLACE TABLE gold.int_price_lines AS
SELECT
    o.order_line_key,
    CASE WHEN s.subscription_id IS NOT NULL THEN CAST(o.subscription_id AS STRING)
         ELSE CONCAT('OL-', o.order_line_key) END                  AS subscription_key,
    o.client_id, o.product_id, o.order_date, o.period_end_date,
    o.duration_months, o.price, o.mrr_amount, o.is_free_placeholder
FROM silver.order_lines o
LEFT JOIN silver.subscriptions s
       ON s.subscription_id = o.subscription_id AND NOT s.has_invalid_period
WHERE o.line_type = 'recurring' AND NOT o.is_voided;

CREATE OR REPLACE TABLE gold.int_subscriptions AS
WITH owner AS (
    -- current owner = client on the most recent line (handles 366 ownership changes)
    SELECT subscription_id, client_id
    FROM silver.order_lines
    WHERE subscription_id IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (PARTITION BY subscription_id
                               ORDER BY order_date DESC, period_end_date DESC, order_line_key) = 1
),
latest_price AS (
    SELECT subscription_key, product_id AS current_product_id
    FROM gold.int_price_lines
    WHERE TRUE
    QUALIFY ROW_NUMBER() OVER (PARTITION BY subscription_key
                               ORDER BY order_date DESC, period_end_date DESC, order_line_key) = 1
),
price_flags AS (
    SELECT subscription_key,
           LOGICAL_OR(mrr_amount > 0)  AS has_paid_price,
           COUNT(*)                    AS n_price_lines
    FROM gold.int_price_lines
    GROUP BY subscription_key
),
master AS (
    SELECT
        CAST(s.subscription_id AS STRING) AS subscription_key,
        s.subscription_id,
        o.client_id,
        'subscription_master'             AS lifecycle_source,
        s.start_date,
        s.expire_date                     AS end_date,
        s.status,
        s.status_group,
        s.is_terminal_status
    FROM silver.subscriptions s
    JOIN owner o ON o.subscription_id = s.subscription_id
    WHERE NOT s.has_invalid_period
),
order_based AS (
    SELECT
        subscription_key,
        CAST(NULL AS INT64)               AS subscription_id,
        client_id,
        'order_period'                    AS lifecycle_source,
        order_date                        AS start_date,
        period_end_date                   AS end_date,
        CAST(NULL AS STRING)              AS status,
        'No master record'                AS status_group,
        FALSE                             AS is_terminal_status
    FROM gold.int_price_lines
    WHERE subscription_key LIKE 'OL-%'
),
unioned AS (
    SELECT * FROM master
    UNION ALL
    SELECT * FROM order_based
)
SELECT
    b.*,
    COALESCE(lp.current_product_id, -1)   AS current_product_id,
    COALESCE(pf.has_paid_price, FALSE)    AS has_paid_price,
    COALESCE(pf.n_price_lines, 0)         AS n_price_lines
FROM unioned b
LEFT JOIN latest_price lp ON lp.subscription_key = b.subscription_key
LEFT JOIN price_flags pf  ON pf.subscription_key = b.subscription_key;
