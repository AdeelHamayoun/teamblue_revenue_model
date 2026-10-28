# KPI Framework: 10 KPIs for a Subscription Business

All KPIs are materialised in `analytics.mart_kpi_monthly` (grain: month × scope). Values below are for scope `All excl. Registry` at December of each year. Each KPI is tied to one of four questions:

* **Growth:** are we getting bigger?
* **Acquisition:** are we winning new customers?
* **Monetisation:** are we earning more per customer?
* **Retention:** are we keeping what we have?

No single KPI describes health. The set is designed so that each one guards against the blind spots of another. For example, MRR can grow while retention collapses, if acquisition is buying the growth.

| # | KPI | Family | 2019 | 2020 | 2021 | 2022 | 2023 |
|---|---|---|---|---|---|---|---|
| 1 | MRR (and YoY growth) | Growth | €221.9k | €238.2k (+7.4%) | €239.7k (+0.6%) | €222.3k | €191.3k |
| 2 | Net new MRR (annual sum) | Growth | +€24.0k | +€16.4k | +€1.5k | −€17.5k | −€31.0k |
| 3 | New paying customers (annual sum) | Acquisition | 6,470 | 6,131 | 3,985 | 1,981\* | 32\* |
| 4 | Active paying customers | Acquisition / base | 40,342 | 42,594 | 41,966 | 38,986 | 33,099 |
| 5 | ARPU (€/month) | Monetisation | 5.50 | 5.59 | 5.71 | 5.70 | 5.78 |
| 6 | Logo churn rate (annualised) | Retention | 5.6% | 7.9% | 10.1% | 10.9% | 16.3% |
| 7 | Gross MRR churn rate (annualised) | Retention | 5.3% | 6.6% | 9.6% | 12.6% | 16.5% |
| 8 | Net revenue retention, 12 months | Retention / expansion | – | 97.9% | 94.7% | 90.3% | 85.9% |
| 9 | Multi-category rate (cross-sell penetration) | Monetisation / loyalty | 13.1% | 12.9% | 12.8% | 12.8% | 13.2% |
| 10 | Quick ratio | Growth efficiency | 2.87 | 1.93 | 1.02 | 0.24 | 0.00\* |

\* The extract under-represents acquisitions after 2020 (memo §1.1). Read KPIs 3 and 10 only where `is_acquisition_complete = true`. Retention KPIs (6, 7 and 8) are unaffected.

---

### 1. Monthly Recurring Revenue (MRR)
**Definition:** the sum, at month end, of `price / duration_months` for every alive subscription. Free and placeholder periods count as €0. One-off fees and refunds are excluded. ARR = MRR × 12.
**Why it matters:** MRR is the size of the recurring revenue engine and the base for every other ratio. It removes the lumpiness of annual prepaid invoicing, which would otherwise show a January spike and a quiet August.
**Health signal:** the *trend*, not the level. MoM and YoY growth are both in the mart. A flat MRR line can hide strong acquisition offset by strong churn, which is why KPIs 2 and 10 exist.

### 2. Net New MRR (MRR bridge)
**Definition:** new + reactivation + expansion − contraction − churn. The full bridge is in `mart_mrr_bridge_monthly`, and it balances exactly (enforced by a data-quality rule).
**Why it matters:** it explains *why* MRR moved. This is the question the Director of Revenue could not answer.
**Health signal:** look at the composition. In 2019, expansion (+€16.1k) almost matched new business (+€19.4k), a sign of healthy land-and-expand. By 2021, churn plus contraction (−€22.9k) cancelled almost all gross additions (+€24.4k).

### 3. New Paying Customers (and new-customer ARPU)
**Definition:** customers whose first-ever paid subscription starts this month (`movement_type = 'new'`). Customers returning after a lapse are counted separately as reactivations.
**Why it matters:** acquisition is the top of the funnel. Dividing new MRR by new customers shows whether acquisition is bringing in high- or low-value customers. Paired with marketing spend, it gives CAC payback.
**Health signal:** volume should be stable or rising, and new-customer ARPU should not decline. A falling new-customer ARPU means growth is being bought with discounting.

### 4. Active Paying Customers
**Definition:** customers with MRR > 0 at month end. Free-only customers are tracked separately in `dim_customer` (35.4k of them).
**Why it matters:** this is the base that churn rates and ARPU are calculated on, and it is the "logo" view of the business.
**Health signal:** the customer base and MRR moving in opposite directions points to price or mix effects rather than volume. That is a different conversation for the business.

### 5. ARPU (Average Revenue Per paying User)
**Definition:** MRR / active paying customers.
**Why it matters:** it is the stated strategic lever ("cross-sell to increase ARPU"). It also separates monetisation from volume.
**Health signal:** ARPU rose from €5.50 to €5.78. Part of that is mix: low-ARPU single-domain customers churn the most, which lifts the average of those who remain. ARPU should therefore always be read alongside KPI 4 and per segment. ARPU per segment is very different: Retail €4.17, Reseller €17.56, OBP €39.50.

### 6. Logo Churn Rate
**Definition:** customers that churned this month / customers active at the end of last month. A customer churns when their *last* paid subscription ends. Losing one of several products is contraction, not churn.
**Why it matters:** it measures loyalty and product-market fit from the customer's side, independent of how much each customer pays.
**Health signal:** it doubled from 5.6% to roughly 10% a year between 2019 and 2021. For a domains and hosting base, which is sticky by nature (switching registrars is a hassle), this is the clearest warning sign in the dataset.

### 7. Gross MRR Churn Rate
**Definition:** −(churned MRR + contraction MRR) / opening MRR. This is revenue lost from the existing base, ignoring expansion.
**Why it matters:** logo churn treats a €2 customer and a €200 customer the same; this KPI does not. If gross MRR churn is below logo churn, the business is losing mostly small customers. That is the pattern here in 2019–2020, and it reversed by 2022.
**Health signal:** it rose from 5.3% to 12.6% a year (2019 → 2022). Larger customers have started to leave too.

### 8. Net Revenue Retention (NRR, 12-month) and GRR
**Definition:** the MRR today from customers who were active 12 months ago, divided by their MRR 12 months ago. GRR is the same calculation with each customer capped at their starting MRR, so it ignores expansion.
**Why it matters:** NRR is the single best indicator of compounding. Above 100%, the existing base grows on its own. The gap between NRR and GRR shows how much expansion and cross-sell are contributing.
**Health signal:** NRR fell from 97.9% (2020) to 90.3% (2022). The NRR–GRR gap shrank from 5.0 points (2020) to 1.4 points (2022), so expansion now barely offsets losses. The cross-sell engine is underpowered compared with the ambition.

### 9. Multi-Category Rate (Cross-sell Penetration)
**Definition:** the share of active paying customers who pay in two or more product categories. Also available: categories per customer (1.15) and monthly cross-sell events (`cross_sell_customers`, `cross_sell_mrr`).
**Why it matters:** it is the direct measure of the stated strategy. It is also the strongest leading indicator of retention in this data: 12-month survival is 88.2% for single-category customers, versus 98.7% for two categories and 99.9% for three or more.
**Health signal:** penetration is flat at about 13% for five years. The strategy is not yet moving this needle. Resellers reach 58.6% while Retail is at 10.2%, which shows where the headroom is.

### 10. SaaS Quick Ratio
**Definition:** (new + reactivation + expansion MRR) / (churned + contraction MRR).
**Why it matters:** it summarises growth efficiency in one number. Above 4 is efficient, 1 means treading water, and below 1 means shrinking. It also shows whether growth is being "bought" through acquisition while the base leaks.
**Health signal:** it fell from 2.87 (2019) to 1.02 (2021). The business went from growing efficiently to running in place within two years.

---

## Supporting metrics (in the same mart)

| Metric | Use |
|---|---|
| `implied_ltv` = ARPU / monthly logo churn | Rough customer lifetime value (€1,170 in 2019 → €678 in 2021) to set against acquisition cost |
| `grr_12m`, `logo_retention_12m` | Retention without the expansion effect |
| `legacy_base_mrr_retention_12m` | Like-for-like retention of the base that paid before 2019; immune to the extract's sampling bias |
| `mrr_at_risk_share` | Share of MRR on subscriptions in payment-issue status (about 0.4%) |
| `imputed_mrr_share` | Data confidence: share of MRR priced from a carried-forward or backfilled line |
| `is_acquisition_complete`, `is_price_observable` | Completeness flags; dashboards should grey out months where these are false |

## Suggested executive view

* **Row 1:** MRR with YoY growth, Net New MRR bridge, Quick Ratio.
* **Row 2:** New customers, ARPU, Multi-category rate.
* **Row 3:** Logo churn, Gross MRR churn, NRR/GRR.

Each tile has segment and category filters (the `scope` column). Months outside the completeness flags are greyed out.
