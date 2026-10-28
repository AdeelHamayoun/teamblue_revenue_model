-- GOLD.DIM_CUSTOMER  (full refresh, SCD1 + derived lifecycle attributes)
-- Grain: one row per client_id (every customer in the master, paying or not).
CREATE OR REPLACE TABLE gold.dim_customer
CLUSTER BY client_id AS
WITH paid AS (
    SELECT client_id,
           DATE_TRUNC(MIN(start_date), MONTH)  AS first_paid_month,
           COUNT(*)                            AS paid_subscriptions_ever
    FROM gold.int_subscriptions
    WHERE has_paid_price
    GROUP BY client_id
),
any_sub AS (
    SELECT client_id, MIN(start_date) AS first_subscription_date, COUNT(*) AS subscriptions_ever
    FROM gold.int_subscriptions
    GROUP BY client_id
),
window_activity AS (
    SELECT client_id,
           MIN(IF(is_active, month, NULL))      AS first_active_month_in_window,
           MAX(IF(is_active, month, NULL))      AS last_active_month_in_window,
           COUNTIF(is_active)                   AS active_months_in_window,
           MAX(n_paying_categories)             AS max_paying_categories
    FROM gold.fct_customer_month
    GROUP BY client_id
),
latest AS (
    SELECT client_id, mrr AS current_mrr, n_paying_categories AS current_paying_categories
    FROM gold.fct_customer_month
    WHERE month = DATE '{{REPORT_END}}' AND is_active
),
cats_ever AS (
    SELECT client_id, STRING_AGG(DISTINCT product_category, ', ' ORDER BY product_category) AS categories_ever
    FROM gold.fct_customer_category_month
    WHERE mrr > 0
    GROUP BY client_id
)
SELECT
    c.client_id,
    c.country_code,
    c.is_domestic,
    c.segment_code,
    c.segment_group,
    c.is_registry_account,
    c.has_malformed_id,
    -- registry accounts have no join_date: fall back to first subscription start
    COALESCE(c.join_date, a.first_subscription_date)                  AS join_date,
    c.join_date IS NULL                                               AS join_date_imputed,
    a.first_subscription_date,
    p.first_paid_month                                                AS cohort_month,
    EXTRACT(YEAR FROM p.first_paid_month)                             AS cohort_year,
    COALESCE(a.subscriptions_ever, 0)                                 AS subscriptions_ever,
    COALESCE(p.paid_subscriptions_ever, 0)                            AS paid_subscriptions_ever,
    w.first_active_month_in_window,
    w.last_active_month_in_window,
    COALESCE(w.active_months_in_window, 0)                            AS active_months_in_window,
    COALESCE(w.max_paying_categories, 0)                              AS max_paying_categories,
    ce.categories_ever,
    ROUND(COALESCE(l.current_mrr, 0), 4)                              AS current_mrr,
    COALESCE(l.current_paying_categories, 0)                          AS current_paying_categories,
    CASE
        WHEN l.client_id IS NOT NULL                   THEN 'Active'
        WHEN w.last_active_month_in_window IS NOT NULL THEN 'Churned'
        WHEN p.client_id IS NOT NULL                   THEN 'Churned before window'
        WHEN a.client_id IS NOT NULL                   THEN 'Never paid (free only)'
        ELSE 'No subscription'
    END                                                               AS customer_status_at_report_end
FROM silver.customers c
LEFT JOIN paid p            ON p.client_id = c.client_id
LEFT JOIN any_sub a         ON a.client_id = c.client_id
LEFT JOIN window_activity w ON w.client_id = c.client_id
LEFT JOIN latest l          ON l.client_id = c.client_id
LEFT JOIN cats_ever ce      ON ce.client_id = c.client_id;
