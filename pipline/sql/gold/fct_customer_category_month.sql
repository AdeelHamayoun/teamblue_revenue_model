-- GOLD.FCT_CUSTOMER_CATEGORY_MONTH
-- materialization: partition_overwrite(month)
-- BigQuery table: PARTITION BY DATE_TRUNC(month, MONTH), CLUSTER BY client_id, product_category
-- Grain: client_id x product_category x month. One row whenever the customer pays
-- in that category this month OR paid last month (so category churn is a row).
--
-- Powers: category performance (MRR bridge per category) and cross-sell.
--   cross_sell          = a customer who was ALREADY PAYING last month starts paying
--                         in a category they have never paid for before
--   category_reactivation = returns to a category they paid for in the past
WITH cur AS (
    SELECT client_id, product_category, month,
           SUM(mrr)                AS mrr,
           COUNTIF(is_paying)      AS paying_subscriptions
    FROM gold.fct_subscription_month
    WHERE month BETWEEN DATE '{{PREV_MONTH}}' AND DATE '{{WIN_END}}'
    GROUP BY client_id, product_category, month
),
spine AS (
    SELECT client_id, product_category, month FROM cur
    WHERE month >= DATE '{{WIN_START}}' AND mrr > {{EPS}}
    UNION DISTINCT
    SELECT client_id, product_category, DATE_ADD(month, INTERVAL 1 MONTH) FROM cur
    WHERE mrr > {{EPS}}
      AND DATE_ADD(month, INTERVAL 1 MONTH) BETWEEN DATE '{{WIN_START}}' AND DATE '{{WIN_END}}'
),
first_cat AS (
    -- earliest paid subscription start per customer & category, over full history
    SELECT s.client_id,
           COALESCE(d.product_category, 'Unknown')              AS product_category,
           DATE_TRUNC(MIN(s.start_date), MONTH)                 AS category_first_paid_month
    FROM gold.int_subscriptions s
    LEFT JOIN gold.dim_product d ON d.product_id = s.current_product_id
    WHERE s.has_paid_price
    GROUP BY 1, 2
),
base AS (
    SELECT
        sp.month, sp.client_id, sp.product_category,
        COALESCE(c.mrr, 0)                   AS mrr,
        COALESCE(p.mrr, 0)                   AS mrr_prev,
        COALESCE(c.paying_subscriptions, 0)  AS paying_subscriptions,
        f.category_first_paid_month,
        SUM(COALESCE(p.mrr, 0)) OVER (PARTITION BY sp.client_id, sp.month) AS customer_mrr_prev
    FROM spine sp
    LEFT JOIN cur c ON c.client_id = sp.client_id AND c.product_category = sp.product_category
                   AND c.month = sp.month
    LEFT JOIN cur p ON p.client_id = sp.client_id AND p.product_category = sp.product_category
                   AND p.month = DATE_SUB(sp.month, INTERVAL 1 MONTH)
    LEFT JOIN first_cat f ON f.client_id = sp.client_id AND f.product_category = sp.product_category
)
SELECT
    month, client_id, product_category,
    ROUND(mrr, 4)                                AS mrr,
    ROUND(mrr_prev, 4)                           AS mrr_prev,
    ROUND(mrr - mrr_prev, 4)                     AS mrr_change,
    paying_subscriptions,
    CASE
        WHEN mrr_prev <= {{EPS}} AND mrr > {{EPS}} AND customer_mrr_prev <= {{EPS}}
             THEN 'new'                          -- customer (re)starts paying with this category
        WHEN mrr_prev <= {{EPS}} AND mrr > {{EPS}}
             AND COALESCE(category_first_paid_month, month) >= month
             THEN 'cross_sell'
        WHEN mrr_prev <= {{EPS}} AND mrr > {{EPS}}
             THEN 'category_reactivation'
        WHEN mrr_prev > {{EPS}} AND mrr <= {{EPS}}            THEN 'churn'
        WHEN mrr - mrr_prev >  {{EPS}}                        THEN 'expansion'
        WHEN mrr_prev - mrr >  {{EPS}}                        THEN 'contraction'
        ELSE 'flat'
    END                                          AS movement_type,
    category_first_paid_month
FROM base