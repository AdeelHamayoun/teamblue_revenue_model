-- ANALYTICS: BI-facing helper objects (created after the audited gold tables are copied in).
-- Everything here lives INSIDE the analytics dataset, so a Looker Studio user only
-- needs access to that one dataset.

-- Customer-month fact enriched with the customer attributes people slice by
-- (country, segment, registry flag). Use this when you need "MRR by country".
CREATE OR REPLACE VIEW analytics.v_customer_month AS
SELECT
    f.*,
    c.country_code,
    c.is_domestic,
    c.segment_group,
    c.is_registry_account,
    c.cohort_year
FROM analytics.fct_customer_month f
JOIN analytics.dim_customer c ON c.client_id = f.client_id;

-- Data-quality results of the most recent pipeline run (for the "Data confidence" dashboard page).
CREATE OR REPLACE TABLE analytics.dq_results_latest AS
SELECT *
FROM meta.dq_results
WHERE run_id = (SELECT run_id FROM meta.pipeline_runs ORDER BY started_at DESC LIMIT 1);

-- Run history (last 100 runs).
CREATE OR REPLACE TABLE analytics.pipeline_runs AS
SELECT *
FROM meta.pipeline_runs
ORDER BY started_at DESC
LIMIT 100;