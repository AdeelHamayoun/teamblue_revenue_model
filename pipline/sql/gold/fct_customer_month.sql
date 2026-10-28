-- GOLD.FCT_CUSTOMER_MONTH   <-- the primary "Data Product"
-- materialization: partition_overwrite(month)
-- BigQuery table: PARTITION BY DATE_TRUNC(month, MONTH), CLUSTER BY client_id
-- Grain: client_id x month. A row exists when the customer is paying this month
-- OR was paying last month (so the churn month is an explicit row).
--
-- ACTIVE  = customer MRR > 0 at month end (>= 1 alive subscription with a paid price)
-- CHURNED = active last month, not active this month (all paid subscriptions ended)
-- MRR movements are mutually exclusive and sum to the month's MRR change, so
--   opening MRR + new + reactivation + expansion - contraction - churn = closing MRR
-- (asserted by a gold DQ rule).
WITH cur AS (
    SELECT
        client_id, month,
        SUM(mrr)                                                          AS mrr,
        COUNT(*)                                                          AS active_subscriptions,
        COUNTIF(is_paying)                                                AS paying_subscriptions,
        COUNT(DISTINCT IF(is_paying, product_category, NULL))             AS n_paying_categories,
        SUM(IF(mrr_source IN ('carried_forward', 'backfilled'), mrr, NULL)) AS imputed_mrr,
        LOGICAL_OR(status_group = 'Payment issue' AND is_paying)          AS has_payment_issue
    FROM gold.fct_subscription_month
    WHERE month BETWEEN DATE '{{PREV_MONTH}}' AND DATE '{{WIN_END}}'
    GROUP BY client_id, month
),
spine AS (
    SELECT client_id, month FROM cur
    WHERE month >= DATE '{{WIN_START}}' AND mrr > {{EPS}}
    UNION DISTINCT
    SELECT client_id, DATE_ADD(month, INTERVAL 1 MONTH) FROM cur
    WHERE mrr > {{EPS}}
      AND DATE_ADD(month, INTERVAL 1 MONTH) BETWEEN DATE '{{WIN_START}}' AND DATE '{{WIN_END}}'
),
first_paid AS (
    SELECT client_id, DATE_TRUNC(MIN(start_date), MONTH) AS first_paid_month
    FROM gold.int_subscriptions
    WHERE has_paid_price
    GROUP BY client_id
),
xsell AS (
    SELECT client_id, month,
           SUM(IF(movement_type = 'cross_sell', mrr, NULL))   AS cross_sell_mrr,
           COUNTIF(movement_type = 'cross_sell')              AS cross_sell_categories
    FROM gold.fct_customer_category_month
    WHERE month BETWEEN DATE '{{WIN_START}}' AND DATE '{{WIN_END}}'
    GROUP BY client_id, month
),
base AS (
    SELECT
        sp.month, sp.client_id,
        COALESCE(c.mrr, 0)                        AS mrr,
        COALESCE(p.mrr, 0)                        AS mrr_prev,
        COALESCE(c.active_subscriptions, 0)       AS active_subscriptions,
        COALESCE(c.paying_subscriptions, 0)       AS paying_subscriptions,
        COALESCE(c.n_paying_categories, 0)        AS n_paying_categories,
        COALESCE(p.n_paying_categories, 0)        AS n_paying_categories_prev,
        COALESCE(c.imputed_mrr, 0)                AS imputed_mrr,
        COALESCE(c.has_payment_issue, FALSE)      AS has_payment_issue,
        f.first_paid_month,
        COALESCE(x.cross_sell_mrr, 0)             AS cross_sell_mrr,
        COALESCE(x.cross_sell_categories, 0)      AS cross_sell_categories
    FROM spine sp
    LEFT JOIN cur c ON c.client_id = sp.client_id AND c.month = sp.month
    LEFT JOIN cur p ON p.client_id = sp.client_id AND p.month = DATE_SUB(sp.month, INTERVAL 1 MONTH)
    LEFT JOIN first_paid f ON f.client_id = sp.client_id
    LEFT JOIN xsell x ON x.client_id = sp.client_id AND x.month = sp.month
),
classified AS (
    SELECT *,
        CASE
            WHEN mrr_prev <= {{EPS}} AND mrr > {{EPS}}
                 AND COALESCE(first_paid_month, month) >= month       THEN 'new'
            WHEN mrr_prev <= {{EPS}} AND mrr > {{EPS}}                THEN 'reactivation'
            WHEN mrr_prev >  {{EPS}} AND mrr <= {{EPS}}               THEN 'churn'
            WHEN mrr - mrr_prev > {{EPS}}                             THEN 'expansion'
            WHEN mrr_prev - mrr > {{EPS}}                             THEN 'contraction'
            ELSE 'flat'
        END AS movement_type
    FROM base
)
SELECT
    month,
    client_id,
    ROUND(mrr, 4)                                                              AS mrr,
    ROUND(mrr_prev, 4)                                                         AS mrr_prev,
    ROUND(mrr - mrr_prev, 4)                                                   AS mrr_change,
    movement_type,
    ROUND(CASE WHEN movement_type = 'new'          THEN mrr            ELSE 0 END, 4) AS mrr_new,
    ROUND(CASE WHEN movement_type = 'reactivation' THEN mrr            ELSE 0 END, 4) AS mrr_reactivation,
    ROUND(CASE WHEN movement_type = 'expansion'    THEN mrr - mrr_prev ELSE 0 END, 4) AS mrr_expansion,
    ROUND(CASE WHEN movement_type = 'contraction'  THEN mrr - mrr_prev ELSE 0 END, 4) AS mrr_contraction,
    ROUND(CASE WHEN movement_type = 'churn'        THEN -mrr_prev      ELSE 0 END, 4) AS mrr_churn,
    ROUND(CASE WHEN movement_type = 'flat'         THEN mrr - mrr_prev ELSE 0 END, 4) AS mrr_rounding,
    mrr > {{EPS}}                                                              AS is_active,
    mrr_prev > {{EPS}}                                                         AS was_active,
    active_subscriptions,
    paying_subscriptions,
    n_paying_categories,
    n_paying_categories_prev,
    n_paying_categories >= 2                                                   AS is_multi_category,
    ROUND(cross_sell_mrr, 4)                                                   AS cross_sell_mrr,
    cross_sell_categories > 0                                                  AS has_cross_sell,
    ROUND(imputed_mrr, 4)                                                      AS imputed_mrr,
    has_payment_issue,
    first_paid_month                                                           AS cohort_month,
    DATE_DIFF(month, first_paid_month, MONTH)                                  AS months_since_first_paid
FROM classified
