# Insights Memo

**To:** Director of Revenue  **Re:** Unified Revenue & Product Performance Model

This memo answers the three questions in the brief: how data quality was handled (§1), how "Active" and "Churn" are defined (§2), and how the model scales to 200M rows (§3). It then sets out what the data says about the business (§4).

---

## 1. How did you handle data quality issues?

I profiled every source before writing any transformation. Each issue got a deliberate, documented remediation, applied in the layer where it is cheapest to fix. Every rule is executable: it lives in `pipeline/dq_rules.py`, runs on every pipeline run, and its result is logged to `meta.dq_results` so it can be tracked over time.

Rules have one of two severities:

* **error:** the pipeline stops before publishing. This is the "circuit breaker", and the BI layer keeps serving the last good version.
* **warn:** a known, accepted issue. It is monitored for drift; for example, an alert fires if the duplicate rate moves from 5% to 15%.

### 1.1 Structural findings that shaped the model

These matter more than any single dirty field.

**(a) The orders file is a sample, not a complete ledger.** Of the 82,933 subscriptions alive throughout 2020, only 22% have a 2020 billing line. Most subscriptions appear with just one billing line, even when they have been alive for years.

* *Consequence:* churn derived from gaps between orders would be badly overstated.
* *Remediation:* lifecycle comes from the subscription master, and price from the orders (§2).
* *Transparency:* months without an observed billing line inherit the nearest observed price (carried forward, otherwise backfilled), and each row is tagged with that provenance. In a typical month, about 27% of MRR is priced from a line covering that exact month, 33% is carried forward and 40% is backfilled. The model reports this share every month as `imputed_mrr_share`.

**(b) Recent acquisitions are under-represented.** A subscription only appears in the extract if one of its billing lines expiring between 2019-01 and 2023-12 was sampled. Recently started subscriptions have fewer eligible lines, so they are less likely to be sampled. Subscriptions by start year:

| Start year | 2019 | 2020 | 2021 | 2022 | 2023 |
|---|---|---|---|---|---|
| Subscriptions in extract | 27.1k | 27.2k | 21.7k | 15.9k | 1.3k |

* *Consequence:* acquisition KPIs after 2020 are understated, and part of the 2022–23 MRR decline is an artifact of the extract.
* *Remediation:* a completeness flag (`is_acquisition_complete`), plus a like-for-like metric on the pre-2019 customer base, which is a fixed population not affected by this bias.
* *Recommended next step:* request the full billing ledger, or the sampling rule used, so the data can be reweighted.

**(c) The reporting window is Jan 2019 – Dec 2023.** Billing periods in the extract expire between 2019-01 and 2023-12. December 2018 is computed only as an opening balance. Without it, every customer would look "new" in January 2019.

### 1.2 Field-level issues

| Issue | Evidence | Remediation | Layer |
|---|---|---|---|
| Exact duplicate order rows | 10,011 rows (4.8%) identical in all 12 columns | Deduplicated on a row hash. A conservation check confirms that bronze = silver + quarantine + removed duplicates. | Silver |
| Credit notes / refunds | 1,693 `R…` documents and 399 negative lines; they reference the renewcode they refund | Separated as their own line types. Net refunds are attached to the original billing event, and fully refunded periods are voided for MRR (74 lines). | Silver |
| 0.01 EUR placeholder prices | 66,598 lines (33%), mostly bundled Applications such as e-mail with a domain | Treated as free: MRR = €0. These customers are kept, so the 35.4k free-only customers stay visible as a conversion pool. | Silver |
| `quantity` behaves inconsistently | Only 0 or 1; priced lines with qty 0 have the same price distribution as qty 1 | Not used as a revenue multiplier. Raised with the source owner. | Silver |
| Price covers the whole period | `productprice` matches period length (e.g. €26.50 for 12 months) | MRR = price / duration_months. One-off and 0-month lines are excluded from MRR. | Silver |
| Malformed product row | Product 164202's line is wrapped in an extra layer of CSV quotes, corrupting its ID; 24 order lines looked orphaned | Caught by the product primary-key uniqueness rule, which blocked the first run. Fixed by stripping the stray quotes: no revenue dropped. | Silver |
| Trailing `,,,,` / `"` on product category | 7 category spellings for 4 real categories | Cleaned in Silver. A domain rule checks that only the 4 accepted values remain. | Silver |
| Registry maintainer accounts | 36 `-EUROMNT` IDs, 22.3k lines, no subscription ID, no join date | Kept as revenue but flagged (`is_registry_account`). Their billing period is used as the lifecycle. The default executive scope excludes them. | Silver / Gold |
| Missing `subscription_id` | 17% of paid recurring lines (almost all Registry) | Lifecycle source = `order_period` | Gold |
| Subscription IDs missing from the master | 4 IDs | Same as above | Gold |
| Ownership changes | 366 subscriptions are billed to more than one client | Current owner = client on the most recent line | Gold |
| Invalid periods | 29 subscriptions with expiry before start; 68 order lines with a future order date or an end date before the start | Quarantined to `silver.quarantine_*` for audit, excluded from Gold | Silver |
| Literal `'NULL'` strings and unknown country | 856 customers | Converted to real NULLs; country mapped to `ZZ` (ISO "unknown"). Join date imputed from first subscription for Registry accounts. | Silver |
| Malformed client IDs (`--2736-EURO`) | 88 IDs | **Flagged, not rewritten.** They join consistently across sources, so "fixing" them would break referential integrity. | Silver |
| A new, unmapped subscription status | (guard) | Any status not in `config.STATUS_GROUPS` blocks the run, so lifecycle logic can never silently mis-classify | Silver |

### 1.3 Reconciliation (Gold): "single version of truth"

These rules have zero tolerance:

* Subscription MRR = customer MRR = customer-category MRR, for every month.
* MRR bridge: opening MRR + movements = closing MRR.
* Continuity: closing MRR of month M = opening MRR of month M+1.
* Unique grain in every fact table.
* Every month in the reporting window is present.

Two warnings provide anomaly detection: month-over-month MRR moving more than ±15%, and the share of imputed MRR.

During development these checks also caught my own bug. A refund-voiding condition matched the 0.01 placeholder lines. It now has its own regression rule.

---

## 2. What logic did you use to define "Churn" and "Active"?

### Principle: lifecycle comes from contracts, value from billing

Activity is **not** inferred from orders. The orders extract is a sample (§1.1a), and even in a complete ledger, annual prepaid billing means a customer can go up to 11 months with no order and still be fully active. Instead:

* **A subscription is alive in month M** if `start_date <= last_day(M)` and `expire_date > last_day(M)`. This is a month-end snapshot, the standard MRR convention; it prevents double counting when a plan is replaced mid-month. `expire_date` is the paid-through date from the subscription master.
* **Its MRR** comes from the billing line covering that month-end. If there is none, it comes from the latest earlier line, otherwise the earliest later line. Placeholder 0.01 lines give €0, and fully refunded periods are voided.

### Customer-level definitions (`fct_customer_month`)

| Status | Rule |
|---|---|
| **Active (paying)** | Customer MRR > 0 at month end, i.e. at least one alive subscription with a paid price |
| Active (free only) | Alive subscriptions, but all at €0. Tracked in `dim_customer`, **not** counted as paying. |
| **Churned** (event in month M) | Active in M−1 and not active in M: their *last* paid subscription ended |
| New | Becomes active, and the customer's first-ever paid subscription starts this month (full subscription history back to 1996 is used, not just the reporting window) |
| Reactivation | Becomes active after a lapse, having paid at some point in the past |
| Expansion / Contraction | Active in both months, with MRR up or down (more than €0.005) |

### Deliberate choices, and why

1. **Churn is a customer concept, not a subscription concept.** Ending one of several products is *contraction*. Swapping a hosting plan is flat, expansion or contraction. This avoids inflating churn with plan changes, which is very common for hosting upgrades.
2. **The expiry date is the churn date, not the status.** `status` is a current-state snapshot with no history and no change dates. A subscription marked `CANCELLED` stays active until its paid-through date, because the customer has already paid for that period and it is still MRR. Status is still used, as a dimension: statuses are grouped (Standard, Payment issue, Terminated, Transferred out, Owner changed, Migrated, Pending). This powers *revenue at risk* (MRR on `AWAITING PAYMENT` / `UNPAID`, about 0.4%) and lets churn be broken down by cause, e.g. `MAINTENANCE CHANGED` = transferred to another registrar.
3. **No artificial grace period.** In a contract-based lifecycle, a renewal pushes the expiry date forward, so there is no false gap to bridge. A customer who returns after a genuine lapse is a reactivation. These are reported separately (about €350–630 MRR per year in 2019–2022) so the business can judge whether win-back works.
4. **Free is not active.** 35.4k customers (38.5%) have only €0 subscriptions. Counting them as active would hide that they never converted, and would dilute ARPU and churn.

---

## 3. If this dataset grew from 200k to 200 million rows, what would change?

This version of the model already runs on **BigQuery** (storage and compute are separate, serverless and
columnar), so the "move to a distributed warehouse" step is done. What remains is making the *design* scale.
Per-model rows, seconds and bytes billed are logged on every run in `meta.model_runs` (and surfaced in the
Looker Studio "Data confidence" page), so each claim below can be checked against your own run.

### Where the cost is

The reference single-node run on this data (DuckDB, same SQL logic) showed that the bottleneck is not the
orders. It is the **subscription x month expansion** (`gold.fct_subscription_month`: 6.9M rows from 170k
subscriptions). At 1,000x the volume that is about 7 billion rows per 5 years. BigQuery absorbs the compute
with slots, but cost and runtime still grow with the number of rows scanned, so the changes below target the
expansion and keep work incremental.

### What is already in place for scale

| Concern | In this repo |
|---|---|
| Partitioning | Monthly facts: `PARTITION BY DATE_TRUNC(month, MONTH)`; a daily run touches 2-3 of 60 partitions |
| Clustering | `client_id` (+ `subscription_key` / `product_category`) so customer lookups and joins prune blocks |
| Incremental, idempotent writes | Partition overwrite in one transaction (`DELETE` window + `INSERT`), proven by `tests/test_idempotency.py` |
| Late data | `--lookback N`; backfill by month range, safe to re-run |
| Immutable raw layer | Bronze keeps every file version (`batch_id`), BigQuery long-term storage pricing applies after 90 days |
| Write-audit-publish | BI reads `analytics` only; a blocked DQ run leaves the last good tables untouched |
| Cost visibility | `bytes_billed` per model in `meta.model_runs`; jobs labelled `app=teamblue-revenue` |

### Changes, in priority order

**1. Make Bronze and Silver incremental too (Gold already is).**

* **Bronze:** replace full-snapshot files with CDC / append-only increments (Datastream, Fivetran, or files per
  day) landed in date-partitioned GCS and loaded by partition. The content-hash `batch_id` extends naturally.
* **Silver:** `MERGE` on the natural key (`order_line_key`) instead of `CREATE OR REPLACE`; partition
  `silver.order_lines` by `order_date` (month) and cluster by `subscription_id`, `client_id`. Deduplicate within
  arriving partitions plus a lookback instead of a window over all history.
* **Partition filters:** set `require_partition_filter = true` on the facts so nobody (or no dashboard) can
  scan 7B rows by accident.

**2. Replace the dense monthly explosion with an event model at the base.**

* Store **MRR change events** per subscription (start, price change, end) - about as many rows as order lines.
* Derive monthly snapshots with a cumulative sum of deltas per customer: cost proportional to *events*, not
  *subscriptions x months*.
* Materialise the dense `fct_subscription_month` only for recent months, or drop it in favour of the
  customer-level facts, which are ~3x smaller and sparse (paying months plus the churn month).
* Replace the range join (`start_date <= month_end < end_date`) with an interval/event approach; at scale the
  non-equi join is the single most expensive step.

**3. Pre-aggregate for BI.** Dashboards read the marts (~10k rows even at scale). Use **BI Engine** (in-memory
acceleration) on the `analytics` dataset, and rebuild cohort / NRR marts incrementally: only affected cohort
and month cells change.

**4. Scale data quality with the data.** Uniqueness / conservation / not-null checks run on *arriving
partitions*; reconciliation compares aggregates (sum of MRR per month), not row-level joins; volume and MRR
anomaly checks use a trailing distribution instead of fixed thresholds; quarantine tables are partitioned with a
retention policy.

**5. Orchestration, cost and operations.**

* Dataform (native to BigQuery) or dbt for models and tests; Cloud Composer (Airflow) or Cloud Workflows for
  scheduling and backfill fan-out (one task per month partition). Today a Cloud Run Job + Cloud Scheduler is the
  right-sized choice for 200k rows.
* Move from on-demand pricing to **capacity (slot) reservations** once the monthly bill justifies it; set
  per-project and per-user bytes-billed quotas and budget alerts.
* Replace the table copy in the publish step with zero-copy **table clones / snapshots**.
* Use `INFORMATION_SCHEMA.JOBS` together with `meta.model_runs` for per-model cost attribution.

**6. Storage.** At 200M rows the raw CSV is about 22 GB; BigQuery stores it compressed in columnar format,
and partitions untouched for 90 days drop to long-term storage pricing automatically. Bronze stays immutable
for replay and audit; Gold can always be rebuilt from it.

---

## 4. What the data says: insights and recommendations

1. **The growth problem is retention, not acquisition.**
   * On the like-for-like pre-2019 base, which is unaffected by sampling, 12-month MRR retention fell from 98.2% (2020) to 89.2% (2023).
   * Annual logo churn went from 5.6% (2019) to 10.1% (2021), even while acquisition was still complete in the extract.
   * Gross MRR churn now exceeds logo churn, so higher-value customers have started to leave.

   *Action:* break churn down by status group to separate competitive loss (`MAINTENANCE CHANGED` = transferred out to another registrar) from involuntary loss (suspended for non-payment). These need different fixes: pricing and retention offers for the first, dunning and auto-renew for the second.

2. **Churn is concentrated at renewal.**
   * Cohort retention drops in steps at months 12, 24 and 36.
   * 17–25% of new customers fail their first renewal (12-month logo retention: 82.6% / 79.3% / 75.5% for the 2019 / 2020 / 2021 cohorts), and the cohorts are getting worse.

   *Action:* run renewal-window campaigns (T−60 to T−0 days), push auto-renew adoption, and offer multi-year prepay discounts.

3. **Cross-sell is the strongest retention lever available, and it is under-used.**
   * 12-month survival: 88.2% with one category, 98.7% with two, 99.9% with three or more. With tenure held constant: 94.3% vs 99.2%.
   * This is correlational, since engaged customers buy more, but the effect is large and consistent.
   * Penetration is flat at 13% for five years: Retail 10.2%, Resellers 58.6%.

   *Action:* target single-category Retail domain holders, the largest and highest-churn group. Run a controlled experiment to measure the causal effect on retention.

4. **Hosting is the high-value cross-sell; Domains are the entry point.**
   * Domains: 50% of MRR, lowest category churn (about 4.0% a year).
   * Hosting: highest ARPU (€7.3–7.9 a month), and each Hosting cross-sell adds about €8.2 MRR, against €3.1 for Applications and €2.1 for Domains.
   * Applications are largely bundled free (a third of order lines are 0.01 EUR placeholders).

   *Action:* "domain → hosting" is the priority journey. Track bundled-to-paid conversion for Applications.

5. **35.4k free-only customers (38.5% of the base) are an already-acquired conversion pool.** Converting even 5% at the Retail ARPU (€4.17) adds about €7.4k MRR, which would offset about two-thirds of a full year's churned MRR at the 2020 rate (€10.9k).

6. **Segment economics differ by an order of magnitude.**

   | Segment | ARPU (€/month) | Annual logo churn |
   |---|---|---|
   | OBP | 39.50 | 2.6% |
   | Reseller | 17.56 | 2.1% |
   | Retail | 4.17 | 10.0% |

   Blended KPIs are dominated by Retail volume. Executive dashboards should always show the segment split.

## 5. Open questions for source-system owners

1. What sampling rule produced the orders extract? With it, the data can be reweighted instead of only flagged.
2. What does `quantity` mean (0 vs 1 on priced lines)?
3. Is there a status history, with change dates, behind `subscriptions.status`? It would allow cause-of-churn analysis by date.
4. Is 0.01 EUR a system convention for "included in bundle"? Can bundle membership be exposed directly?
5. What are the business definitions of the segment codes (`Ret-E`, `Ret-N`, `obp`)?
