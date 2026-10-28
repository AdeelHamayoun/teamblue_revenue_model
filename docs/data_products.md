# The Data Product: Unified Revenue & Product Performance Model

## Design principles

1. **One grain per question.** Revenue questions are asked at three levels: subscription ("what is this contract worth?"), customer ("is this client growing or leaving?") and customer × category ("are they adopting more of the portfolio?"). Each level gets its own fact table, so no analyst has to aggregate or de-duplicate on the fly. Doing that by hand is how "two versions of MRR" usually appear.
2. **Movements are pre-classified in the warehouse, not in the BI tool.** Every customer-month carries exactly one movement type, and the parts of the MRR change add up exactly. That makes the MRR bridge a `SUM()`. It also means Finance, Sales and the Director all see the same definition of "churn".
3. **Every number is traceable to the line it came from.** Each subscription-month stores the billing line that priced it (`price_line_key`) and how that price was obtained (`mrr_source`). Any MRR figure can be audited down to the source order line.
4. **Reconciliation is enforced, not hoped for.** Gold data-quality rules assert:
   * subscription MRR = customer MRR = customer-category MRR, every month;
   * opening MRR + movements = closing MRR;
   * closing MRR of month M = opening MRR of month M+1.

   If any of these fail, nothing is published.

## Tables (schema `analytics`)

### Core facts

| Table | Grain | Rows | Purpose |
|---|---|---|---|
| `fct_subscription_month` | subscription × month (month-end snapshot) | 6.9M | Atomic MRR. Each row carries its price provenance, product, category and status group. |
| `fct_customer_category_month` | customer × category × month | 2.8M | Category performance and cross-sell detection. |
| **`fct_customer_month`** | customer × month | 2.4M | **The primary data product.** MRR, previous MRR, movement type and the parts of the change, active/churn flags, number of categories, cohort and tenure. |

`fct_customer_month` includes a row for the month a customer churns (MRR 0, previous MRR > 0). Churn is therefore an explicit, countable event. It is not something you infer from a row that has disappeared.

Key columns of `fct_customer_month`:

| Column | Meaning |
|---|---|
| `mrr`, `mrr_prev`, `mrr_change` | Month-end MRR, previous month's MRR, and the difference |
| `movement_type` | One of `new`, `reactivation`, `expansion`, `contraction`, `churn`, `flat` |
| `mrr_new` … `mrr_churn` | The parts of `mrr_change`, one non-zero per row. They sum exactly to `mrr_change`. |
| `is_active`, `was_active` | Paying this month / paying last month |
| `n_paying_categories`, `is_multi_category`, `has_cross_sell`, `cross_sell_mrr` | Breadth of the portfolio the customer holds, and cross-sell events |
| `cohort_month`, `months_since_first_paid` | Acquisition cohort and tenure |
| `imputed_mrr`, `has_payment_issue` | Data confidence and revenue at risk |

### Dimensions

| Table | Grain | Notes |
|---|---|---|
| `dim_customer` | client_id (92,042) | Segment, country, cohort, lifecycle status at the end of the reporting window, categories ever held, current MRR. Also carries flags: `is_registry_account`, `has_malformed_id`, `join_date_imputed`. |
| `dim_product` | product_id (1,463) | Cleaned 5-level product hierarchy, plus "unknown member" rows so revenue is never lost through a join. |

### Marts (small, pre-aggregated, ready for BI)

Each mart is computed for every **scope**: `All`, `All excl. Registry`, `Retail`, `Reseller`, `OBP` and `Registry`. A BI tool filters on one column and never re-implements segment logic.

| Mart | Grain | Answers |
|---|---|---|
| `mart_mrr_bridge_monthly` | month × scope | Opening MRR, then new, reactivation, expansion (with cross-sell shown separately), contraction and churn, to closing MRR. Customer counts for each movement. |
| `mart_category_monthly` | month × scope × category | MRR bridge per category, category ARPU, share of total MRR, category churn rate. |
| `mart_cohort_retention` | cohort month × scope × months since start | Logo retention and net revenue retention curves. |
| `mart_kpi_monthly` | month × scope | The 10 KPIs (see `kpi_framework.md`), plus data-confidence and completeness flags. |

## Why this structure

**A star schema with monthly snapshot facts, rather than one wide table.** A single wide "revenue table" at order-line grain would force every dashboard to work out activity, previous-month values and churn on its own. That logic is easy to get subtly wrong, and different answers spread across reports. Monthly snapshot facts are the standard shape for subscription analytics: they make month-over-month comparison a simple join and keep BI queries trivial.

**Customer × month is the main data product, not subscription × month.** The business goals (retention, churn, cross-sell, ARPU) are customer-level questions. A customer who swaps one hosting plan for another has *not* churned. At subscription grain that swap looks like a churn plus a new sale; at customer grain it correctly shows up as flat, expansion or contraction.

**Customer × category sits between them because cross-sell is the strategic objective.** Cross-sell has to be measured at the level where it happens: a customer starting to pay in a category they have never paid for before, while already paying in another. That is not visible at either of the other two grains.

**The marts make reading the data consistent.** The facts are flexible; the marts encode definitions once. Looker (or any BI tool) should point at the marts for executive dashboards, and at the facts only for exploration.

**Facts are partitioned by month.** The monthly facts are the only large tables. They are written with partition overwrite, so incremental runs and backfills touch only the months that changed.

## How it is served (GCP)

The model runs end to end on **BigQuery**. The monthly facts are partitioned by `month` and clustered by
`client_id` / `product_category`. After the data-quality gate passes, the audited gold tables are copied into the
`analytics` dataset, and **Looker Studio** reads only that dataset (see `looker_studio/DASHBOARD_GUIDE.md`).
Three helper objects live next to the marts for BI convenience: `v_customer_month` (customer-month fact joined to
country / segment), `dq_results_latest` and `pipeline_runs` (for the "Data confidence" page).
