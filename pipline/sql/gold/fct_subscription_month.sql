-- GOLD.FCT_SUBSCRIPTION_MONTH
-- materialization: partition_overwrite(month)   [incremental, idempotent, backfillable]
-- BigQuery table: PARTITION BY DATE_TRUNC(month, MONTH), CLUSTER BY client_id, subscription_key
-- Grain: subscription_key x month (month-end snapshot). Only months where the
-- subscription is alive at the last day of the month.
--
-- Convention: a subscription contributes MRR to month M if
--     start_date <= last_day(M)  AND  end_date > last_day(M)
-- (month-end snapshot = the industry-standard MRR convention; avoids double
-- counting mid-month replacements).
--
-- Price selection per month (provenance kept in mrr_source):
--   1. 'observed'        a billing line whose period covers month-end
--   2. 'carried_forward' latest billing line that ended before the month
--   3. 'backfilled'      earliest billing line that starts after the month
--   4. 'no_price_line'   alive but no recurring billing evidence -> MRR 0
-- (2) and (3) are needed because the orders extract is a sample; the share of
-- imputed MRR is reported in the KPI mart as a data-confidence metric.
WITH months AS (
    SELECT m AS month, LAST_DAY(m, MONTH) AS month_end
    FROM UNNEST(GENERATE_DATE_ARRAY(DATE '{{WIN_START}}', DATE '{{WIN_END}}', INTERVAL 1 MONTH)) AS m
),
alive AS (
    SELECT s.*, m.month, m.month_end
    FROM gold.int_subscriptions s
    JOIN months m
      ON s.start_date <= m.month_end
     AND s.end_date   >  m.month_end
),
priced AS (
    SELECT
        a.month, a.month_end, a.subscription_key, a.subscription_id, a.client_id,
        a.lifecycle_source, a.status_group, a.current_product_id,
        p.order_line_key, p.product_id, p.mrr_amount, p.is_free_placeholder,
        CASE
            WHEN p.order_line_key IS NULL                                        THEN 4
            WHEN p.order_date <= a.month_end AND p.period_end_date > a.month_end THEN 1
            WHEN p.period_end_date <= a.month_end                                THEN 2
            ELSE 3
        END AS price_rank,
        p.order_date, p.period_end_date
    FROM alive a
    LEFT JOIN gold.int_price_lines p ON p.subscription_key = a.subscription_key
),
picked AS (
    SELECT *
    FROM priced
    WHERE TRUE
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY subscription_key, month
        ORDER BY price_rank,
                 CASE WHEN price_rank = 1 THEN order_date END DESC,
                 CASE WHEN price_rank = 2 THEN period_end_date END DESC,
                 CASE WHEN price_rank = 3 THEN order_date END ASC,
                 order_line_key
    ) = 1
)
SELECT
    p.month,
    p.subscription_key,
    p.subscription_id,
    p.client_id,
    COALESCE(p.product_id, p.current_product_id)                        AS product_id,
    COALESCE(d.product_category, 'Unknown')                             AS product_category,
    ROUND(COALESCE(p.mrr_amount, 0), 4)                                 AS mrr,
    COALESCE(p.mrr_amount, 0) > 0                                       AS is_paying,
    CASE p.price_rank WHEN 1 THEN 'observed' WHEN 2 THEN 'carried_forward'
                      WHEN 3 THEN 'backfilled' ELSE 'no_price_line' END AS mrr_source,
    p.order_line_key                                                    AS price_line_key,
    p.lifecycle_source,
    p.status_group
FROM picked p
LEFT JOIN gold.dim_product d
       ON d.product_id = COALESCE(p.product_id, p.current_product_id)