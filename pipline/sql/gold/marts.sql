-- GOLD MARTS (full refresh; small aggregates rebuilt from the facts every run)
-- Reporting window only: {{REPORT_START}} .. {{REPORT_END}}
--
-- "scope" lets every mart be sliced consistently:
--   'All', 'All excl. Registry', and each segment_group.
-- Registry maintainer accounts are 36 wholesale IDs; customer-count KPIs (ARPU,
-- logo churn) should normally be read on 'All excl. Registry'.

CREATE OR REPLACE TABLE gold.customer_scope AS
SELECT client_id, 'All' AS scope FROM gold.dim_customer
UNION ALL
SELECT client_id, 'All excl. Registry' FROM gold.dim_customer WHERE NOT is_registry_account
UNION ALL
SELECT client_id, segment_group FROM gold.dim_customer;

-- ---------------------------------------------------------------------------
-- 1. MRR BRIDGE: opening -> new / reactivation / expansion / contraction / churn -> closing
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE gold.mart_mrr_bridge_monthly AS
SELECT
    f.month, s.scope,
    SUM(f.mrr_prev)                                    AS opening_mrr,
    SUM(f.mrr_new)                                     AS new_mrr,
    SUM(f.mrr_reactivation)                            AS reactivation_mrr,
    SUM(f.mrr_expansion)                               AS expansion_mrr,
    SUM(f.cross_sell_mrr)                              AS cross_sell_mrr,   -- subset of expansion
    SUM(f.mrr_contraction)                             AS contraction_mrr,
    SUM(f.mrr_churn)                                   AS churned_mrr,
    SUM(f.mrr_rounding)                                AS rounding_mrr,
    SUM(f.mrr_new + f.mrr_reactivation + f.mrr_expansion + f.mrr_contraction + f.mrr_churn + f.mrr_rounding)
                                                       AS net_new_mrr,
    SUM(f.mrr)                                         AS closing_mrr,
    COUNTIF(f.was_active)                              AS opening_customers,
    COUNTIF(f.movement_type = 'new')                   AS new_customers,
    COUNTIF(f.movement_type = 'reactivation')          AS reactivated_customers,
    COUNTIF(f.movement_type = 'churn')                 AS churned_customers,
    COUNTIF(f.movement_type = 'expansion')             AS expanded_customers,
    COUNTIF(f.movement_type = 'contraction')           AS contracted_customers,
    COUNTIF(f.is_active)                               AS closing_customers,
    COUNTIF(f.has_cross_sell)                          AS cross_sell_customers,
    COUNTIF(f.is_active AND f.is_multi_category)       AS multi_category_customers,
    SUM(IF(f.is_active, f.n_paying_categories, NULL))  AS paying_category_slots,
    SUM(f.imputed_mrr)                                 AS imputed_mrr,
    SUM(IF(f.has_payment_issue, f.mrr, NULL))          AS mrr_with_payment_issue,
    -- like-for-like base: customers already paying before the window (fixed population,
    -- not affected by the extract's under-sampling of recent acquisitions)
    COALESCE(SUM(IF(f.cohort_month < DATE '{{REPORT_START}}', f.mrr, NULL)), 0)      AS legacy_base_mrr,
    COALESCE(SUM(IF(f.cohort_month < DATE '{{REPORT_START}}', f.mrr_prev, NULL)), 0) AS legacy_base_opening_mrr
FROM gold.fct_customer_month f
JOIN gold.customer_scope s ON s.client_id = f.client_id
WHERE f.month BETWEEN DATE '{{REPORT_START}}' AND DATE '{{REPORT_END}}'
GROUP BY f.month, s.scope;

-- ---------------------------------------------------------------------------
-- 2. CATEGORY PERFORMANCE: MRR bridge per product category
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE gold.mart_category_monthly AS
WITH x AS (
    SELECT f.*, s.scope
    FROM gold.fct_customer_category_month f
    JOIN gold.customer_scope s ON s.client_id = f.client_id
    WHERE f.month BETWEEN DATE '{{REPORT_START}}' AND DATE '{{REPORT_END}}'
),
agg AS (
    SELECT
        month, scope, product_category,
        SUM(mrr_prev)                                                              AS opening_mrr,
        COALESCE(SUM(IF(movement_type = 'new', mrr, NULL)), 0)                     AS new_mrr,
        COALESCE(SUM(IF(movement_type = 'cross_sell', mrr, NULL)), 0)              AS cross_sell_mrr,
        COALESCE(SUM(IF(movement_type = 'category_reactivation', mrr, NULL)), 0)   AS reactivation_mrr,
        COALESCE(SUM(IF(movement_type = 'expansion', mrr_change, NULL)), 0)        AS expansion_mrr,
        COALESCE(SUM(IF(movement_type = 'contraction', mrr_change, NULL)), 0)      AS contraction_mrr,
        COALESCE(-SUM(IF(movement_type = 'churn', mrr_prev, NULL)), 0)             AS churned_mrr,
        SUM(mrr)                                                                   AS closing_mrr,
        COUNTIF(mrr > 0)                                                           AS paying_customers,
        COUNTIF(movement_type = 'cross_sell')                                      AS cross_sell_customers,
        COUNTIF(movement_type = 'churn')                                           AS churned_customers
    FROM x
    GROUP BY month, scope, product_category
)
SELECT
    agg.*,
    SAFE_DIVIDE(closing_mrr, paying_customers)                                     AS arpu,
    SAFE_DIVIDE(closing_mrr, SUM(closing_mrr) OVER (PARTITION BY month, scope))    AS share_of_total_mrr,
    SAFE_DIVIDE(-churned_mrr, opening_mrr)                                         AS category_mrr_churn_rate
FROM agg;

-- ---------------------------------------------------------------------------
-- 3. COHORT RETENTION: logo retention + net revenue retention by first-paid month
--    Only cohorts that start inside the window (earlier cohorts are left-censored).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE gold.mart_cohort_retention AS
WITH cohort_members AS (
    SELECT client_id, month AS cohort_month, mrr AS starting_mrr
    FROM gold.fct_customer_month
    WHERE movement_type = 'new' AND month BETWEEN DATE '{{REPORT_START}}' AND DATE '{{REPORT_END}}'
),
activity AS (
    SELECT cm.cohort_month, s.scope, f.month,
           DATE_DIFF(f.month, cm.cohort_month, MONTH) AS months_since_start,
           f.client_id, f.mrr, f.is_active
    FROM cohort_members cm
    JOIN gold.fct_customer_month f ON f.client_id = cm.client_id AND f.month >= cm.cohort_month
    JOIN gold.customer_scope s ON s.client_id = cm.client_id
    WHERE f.month <= DATE '{{REPORT_END}}'
),
sizes AS (
    SELECT cm.cohort_month, s.scope, COUNT(*) AS cohort_customers, SUM(cm.starting_mrr) AS cohort_starting_mrr
    FROM cohort_members cm
    JOIN gold.customer_scope s ON s.client_id = cm.client_id
    GROUP BY cm.cohort_month, s.scope
)
SELECT
    a.cohort_month, a.scope, a.months_since_start,
    z.cohort_customers, z.cohort_starting_mrr,
    COUNTIF(a.is_active)                                                    AS retained_customers,
    SUM(a.mrr)                                                              AS cohort_mrr,
    SAFE_DIVIDE(COUNTIF(a.is_active), z.cohort_customers)                   AS logo_retention,
    SAFE_DIVIDE(SUM(a.mrr), z.cohort_starting_mrr)                          AS net_revenue_retention
FROM activity a
JOIN sizes z ON z.cohort_month = a.cohort_month AND z.scope = a.scope
GROUP BY a.cohort_month, a.scope, a.months_since_start, z.cohort_customers, z.cohort_starting_mrr;

-- ---------------------------------------------------------------------------
-- 4. KPI MART: the 10 KPIs (docs/kpi_framework.md) per month and scope
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE gold.mart_kpi_monthly AS
WITH b AS (SELECT * FROM gold.mart_mrr_bridge_monthly),
nrr AS (
    -- customers active 12 months ago: what share of their MRR is still here?
    SELECT cur_m.month, s.scope,
           SUM(base.mrr)                                              AS base_mrr_12m_ago,
           SUM(COALESCE(cur.mrr, 0))                                  AS base_mrr_now,
           SUM(LEAST(COALESCE(cur.mrr, 0), base.mrr))                 AS base_mrr_now_capped,
           COUNT(*)                                                   AS base_customers_12m_ago,
           COUNTIF(cur.is_active)                                     AS base_customers_retained
    FROM (SELECT DISTINCT month FROM b
          WHERE month >= DATE_ADD(DATE '{{REPORT_START}}', INTERVAL 12 MONTH)) cur_m
    JOIN gold.fct_customer_month base
      ON base.month = DATE_SUB(cur_m.month, INTERVAL 12 MONTH) AND base.is_active
    LEFT JOIN gold.fct_customer_month cur
      ON cur.client_id = base.client_id AND cur.month = cur_m.month
    JOIN gold.customer_scope s ON s.client_id = base.client_id
    GROUP BY cur_m.month, s.scope
)
SELECT
    b.month, b.scope,
    -- Revenue growth
    b.closing_mrr                                                             AS mrr,                         -- KPI 1
    b.closing_mrr * 12                                                        AS arr,
    SAFE_DIVIDE(b.closing_mrr, b.opening_mrr) - 1                             AS mrr_growth_mom,
    SAFE_DIVIDE(b.closing_mrr, LAG(b.closing_mrr, 12) OVER w) - 1             AS mrr_growth_yoy,
    b.net_new_mrr,                                                                                            -- KPI 2
    b.new_mrr, b.reactivation_mrr, b.expansion_mrr, b.contraction_mrr, b.churned_mrr,
    -- Acquisition
    b.new_customers,                                                                                          -- KPI 3
    SAFE_DIVIDE(b.new_mrr, b.new_customers)                                   AS new_customer_arpu,
    -- Base & monetisation
    b.closing_customers                                                       AS active_customers,            -- KPI 4
    SAFE_DIVIDE(b.closing_mrr, b.closing_customers)                           AS arpu,                        -- KPI 5
    -- Retention
    SAFE_DIVIDE(b.churned_customers, b.opening_customers)                     AS logo_churn_rate,             -- KPI 6
    SAFE_DIVIDE(-(b.churned_mrr + b.contraction_mrr), b.opening_mrr)          AS gross_mrr_churn_rate,        -- KPI 7
    SAFE_DIVIDE(n.base_mrr_now, n.base_mrr_12m_ago)                           AS nrr_12m,                     -- KPI 8
    SAFE_DIVIDE(n.base_mrr_now_capped, n.base_mrr_12m_ago)                    AS grr_12m,
    SAFE_DIVIDE(n.base_customers_retained, n.base_customers_12m_ago)          AS logo_retention_12m,
    -- Cross-sell
    SAFE_DIVIDE(b.multi_category_customers, b.closing_customers)              AS multi_category_rate,         -- KPI 9
    SAFE_DIVIDE(b.paying_category_slots, b.closing_customers)                 AS categories_per_customer,
    b.cross_sell_customers, b.cross_sell_mrr,
    -- Growth efficiency
    SAFE_DIVIDE(b.new_mrr + b.reactivation_mrr + b.expansion_mrr,
                -(b.churned_mrr + b.contraction_mrr))                         AS quick_ratio,                 -- KPI 10
    -- Derived / supporting
    SAFE_DIVIDE(SAFE_DIVIDE(b.closing_mrr, b.closing_customers),
                SAFE_DIVIDE(b.churned_customers, b.opening_customers))        AS implied_ltv,
    -- Data confidence: share of MRR priced from a carried-forward/backfilled line
    SAFE_DIVIDE(b.imputed_mrr, b.closing_mrr)                                 AS imputed_mrr_share,
    SAFE_DIVIDE(b.mrr_with_payment_issue, b.closing_mrr)                      AS mrr_at_risk_share,
    b.legacy_base_mrr,
    SAFE_DIVIDE(b.legacy_base_mrr, LAG(b.legacy_base_mrr, 12) OVER w)         AS legacy_base_mrr_retention_12m,
    -- completeness flags: read acquisition-driven KPIs only where TRUE
    b.month <= DATE '{{ACQUISITION_COMPLETE_UNTIL}}'                          AS is_acquisition_complete,
    b.month <  DATE '{{REPORT_END}}'                                          AS is_price_observable
FROM b
LEFT JOIN nrr n ON n.month = b.month AND n.scope = b.scope
WINDOW w AS (PARTITION BY b.scope ORDER BY b.month);
