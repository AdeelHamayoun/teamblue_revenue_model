# Looker Studio dashboard - build guide (about 45 minutes, no code)

Looker Studio (formerly Google Data Studio) is free. Reports are built in the browser, so this guide gives
you the exact data sources, fields, charts and filters to click together. Everything reads from the
**`analytics`** dataset that the pipeline publishes - never from bronze/silver/gold.

> Prerequisite: the pipeline has run successfully once (`python pipeline/run_pipeline.py --mode full`)
> and your Google account is in `analytics_viewers` (Terraform) or is project Owner.

---------------------------------------------------------------------------------------------------

## 1. Connect the data (once)

1. Open <https://lookerstudio.google.com> -> **Create** -> **Report**.
2. Pick the **BigQuery** connector (authorize if asked) -> **My projects** -> your project ->
   dataset **analytics** -> table **mart_kpi_monthly** -> **Add** -> **Add to report**.
3. Add the other sources the same way (menu **Resource -> Manage added data sources -> Add a data source**):

| Data source (table in `analytics`) | Used for |
|---|---|
| `mart_kpi_monthly` | headline KPIs and trends |
| `mart_mrr_bridge_monthly` | MRR movements (new / expansion / churn ...) |
| `mart_category_monthly` | product-category performance |
| `mart_cohort_retention` | cohort retention heat-map |
| `v_customer_month` | MRR by country / segment (customer-month grain) |


4. For each data source: **Edit** -> set **Data credentials** to *Owner's credentials* (viewers of the report then
   need no BigQuery access) and **Data freshness** to *12 hours*.
5. Fix field types once per data source (click the field type icon):
   * `month`, `cohort_month` -> **Date -> Year Month**
   * every ratio / rate / share (`*_rate`, `*_retention*`, `nrr_12m`, `grr_12m`, `*_share`, `mrr_growth_*`,
     `logo_retention`, `net_revenue_retention`, `share_of_total_mrr`) -> **Number -> Percent**
   * every MRR / ARR / ARPU field -> **Currency -> EUR (EUR)**

## 2. Global controls (top of every page, set to "Report-level")

| Control | Field | Default |
|---|---|---|
| Drop-down list | `scope` | **All excl. Registry** (the 36 wholesale registry accounts distort customer-count KPIs) |
| Date range | `month` | 2019-01-01 -> 2023-12-01 |

If your Looker Studio build does not apply the `scope` control to other data sources automatically, add
the same drop-down to each page (chart-level controls always work).

## 3. Pages

### Page 1 - Executive overview ("Is revenue growing?")
| Element | Type | Data source | Setup |
|---|---|---|---|
| MRR | Scorecard | mart_kpi_monthly | metric `mrr`, **Date range dimension** `month`, comparison: previous period |
| ARR | Scorecard | mart_kpi_monthly | `arr` |
| Active customers | Scorecard | mart_kpi_monthly | `active_customers` |
| ARPU | Scorecard | mart_kpi_monthly | `arpu` |
| NRR 12m | Scorecard | mart_kpi_monthly | `nrr_12m` |
| MRR trend | Time series | mart_kpi_monthly | dimension `month`; metric `mrr`; optional second axis `mrr_growth_yoy` |
| Like-for-like MRR | Time series | mart_kpi_monthly | `legacy_base_mrr` (customers already paying before 2019 - immune to the extract's under-sampling of recent sign-ups) |

For scorecards use filter **`month` = the latest month you trust** (see page 6) rather than "all months summed".

### Page 2 - MRR movements ("What drives the change?")
| Element | Type | Data source | Setup |
|---|---|---|---|
| MRR bridge by month | **Stacked column** | mart_mrr_bridge_monthly | dimension `month`; metrics `new_mrr`, `reactivation_mrr`, `expansion_mrr`, `contraction_mrr`, `churned_mrr` (the last two are negative and stack below zero) |
| Net new MRR | Line (same chart, second axis) | mart_mrr_bridge_monthly | `net_new_mrr` |
| Customer movements | Stacked column | mart_mrr_bridge_monthly | `new_customers`, `reactivated_customers`, `churned_customers` |
| Quick ratio | Time series | mart_kpi_monthly | `quick_ratio` (growth quality: >1 = adds more than it loses) |
| Single-month waterfall | **Waterfall** (optional) | mart_mrr_bridge_monthly | add a *month* control; metrics in order: `opening_mrr`, `new_mrr`, `reactivation_mrr`, `expansion_mrr`, `contraction_mrr`, `churned_mrr`, `closing_mrr` |

### Page 3 - Product categories ("Where does revenue come from?")
Data source `mart_category_monthly`.
| Element | Type | Setup |
|---|---|---|
| MRR by category | **Stacked area** | dimension `month`; breakdown dimension `product_category`; metric `closing_mrr` |
| Category mix | 100% stacked column | same, metric `share_of_total_mrr` |
| Category scorecard table | Table with heat-map bars | rows `product_category`; metrics `closing_mrr`, `paying_customers`, `arpu`, `category_mrr_churn_rate`, `cross_sell_mrr`; filter `month` = latest trusted month |
| Cross-sell by category | Bar | `cross_sell_customers`, `cross_sell_mrr` |

### Page 4 - Retention and churn ("Do customers stay?")
| Element | Type | Data source | Setup |
|---|---|---|---|
| Logo churn rate | Time series | mart_kpi_monthly | `logo_churn_rate` |
| Gross MRR churn | Time series | mart_kpi_monthly | `gross_mrr_churn_rate` |
| NRR / GRR / logo retention (12m) | Time series, 3 lines | mart_kpi_monthly | `nrr_12m`, `grr_12m`, `logo_retention_12m` (first value appears in Jan-2020 - the metric needs 12 months of history) |
| **Cohort heat-map** | **Pivot table** with heat-map | mart_cohort_retention | row `cohort_month` (Year Month); column `months_since_start`; metric `logo_retention` (or `net_revenue_retention`); **Style -> Heatmap** on the metric; add filter `months_since_start` <= 36 |

### Page 5 - Customers and cross-sell ("Are we increasing ARPU?")
| Element | Type | Data source | Setup |
|---|---|---|---|
| Multi-category rate | Time series | mart_kpi_monthly | `multi_category_rate` |
| Categories per customer | Time series | mart_kpi_monthly | `categories_per_customer` |
| Cross-sell customers / MRR | Combo | mart_kpi_monthly | `cross_sell_customers` (bars), `cross_sell_mrr` (line) |
| MRR by country | Geo chart or bar | v_customer_month | dimension `country_code`; metric `mrr` (SUM); filter `month` = latest trusted month, `is_active` = true |
| MRR by segment | Donut | v_customer_month | dimension `segment_group`; metric `mrr` |


## 5. Share
**Share -> Manage access** -> add viewers (Viewer role). Because data credentials are the owner's, viewers need
no GCP permissions. Use **Schedule email delivery** for a monthly PDF to the Director of Revenue.

## 6. Keep it fast and cheap
* The four `mart_*` tables are tiny (<1 MB each); queries cost fractions of a cent.
* Only `v_customer_month` (customer x month, about 2 million rows) is large - enable **Extract data** on it if a page feels slow.
* Never point Looker Studio at `gold`, `silver` or `bronze`: only `analytics` is audited and permissioned for BI.
