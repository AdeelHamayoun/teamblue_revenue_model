# team.blue - Unified Revenue & Product Performance Model (GCP edition)

**Python + BigQuery + Terraform + Looker Studio.** Turns the four raw CSV files (customers, orders,
subscriptions, products) into a trusted revenue model - MRR, growth drivers, churn, retention, cross-sell -
and a dashboard for the Director of Revenue.

```
CSVs -> Cloud Storage -> BigQuery bronze -> silver -> gold -> (quality gate) -> analytics -> Looker Studio
```

## What is in this folder

| Path | What it is |
|---|---|

| `pipeline/` | The Python + SQL pipeline (`run_pipeline.py` is the entry point; SQL is in `pipeline/sql/`) |
| `looker_studio/DASHBOARD_GUIDE.md` | Click-by-click guide to build the 5-page dashboard |
| `data/` | raw data folder, contains source files |
| `outputs/` | Validated results of the same model, used as the answer key for the acceptance test |
| `docs/` | **Deliverables 2-4 of the brief**: `data_products.md`, `kpi_framework.md` (10 KPIs), `insights_memo.md`, plus `architecture_gcp.md` |


> **Honest verification status.** In the environment where this package was assembled there was no internet and no
> Google Cloud account, so the pipeline could **not** be executed against live BigQuery here. What *was* checked:
> all 11 SQL models and 40 data-quality rules pass 
> syntax, balanced SQL, correct model order, valid references), and the business logic is a line-by-line port of a
> model that was run and validated on this data. Your first full run, is the
> proof: it compares thousands of numbers (every month, scope and category) with the validated reference. If anything differs, the test names it exactly.

---

## Step-by-step setup (for a non-engineer)

Time: about 45 minutes the first time. Cost: well under 1 USD a month for this dataset.

### Step 0 - What you need
1. A **Google account**.
2. A **Google Cloud project with billing enabled**: <https://console.cloud.google.com> -> project picker -> *New project*
   -> then *Billing* -> link a billing account. Note the **Project ID** (e.g. `teamblue-revenue-123456`) - not the display name.
3. Install three tools (each has a normal installer):
   * **Google Cloud CLI** (`gcloud`): <https://cloud.google.com/sdk/docs/install>
   * **Python 3.10 or newer**: <https://www.python.org/downloads/> (Windows: tick "Add Python to PATH")

Open a terminal (Mac: *Terminal*; Windows: *PowerShell*) for the steps below. Commands are for Mac/Linux; the
Windows variants are shown where they differ.

### Step 1 - Log in to Google Cloud
```bash
gcloud auth login
gcloud auth application-default login      # lets Python and Terraform use your login (no keys to download)
gcloud config set project YOUR_PROJECT_ID
```

### Step 2 - Unzip and prepare Python
```bash
cd team_blue_gcp
python3 -m venv .venv
source .venv/bin/activate                  # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Step 3 - Upload the four CSV files
Use the folder that contains `customers(in).csv`, `orders(in).csv`, `subscriptions(in).csv`, `products(in).csv`:
It stores them as `./data/customers.csv`, etc.

### Step 4 - Run the pipeline
```bash
python pipeline/run_pipeline.py --mode full 
```
Takes a few minutes. You will see a line per model and per layer of data-quality checks. What good looks like:

| Check | Expected |
|---|---|
| bronze rows | customers 92,042 - orders 210,000 - subscriptions 170,245 - products 1,462 |
| last line | `run <id> succeeded: ... total, ... rows written` |
| data-quality | all `error` rules pass. `WARN` lines are expected for known, accepted issues - in the validated reference run it was `gold_imputed_mrr_share` (the orders file is a sample, so many months are priced from the nearest billing line - reported, not hidden) |

If a blocking rule fails, the run stops with `BLOCKED: ...` and **nothing is published** (by design).

### Step 5 - Prove it is correct
once all the DQ rules passed then it populates analytics layer marts for reporting.
Spot-check numbers yourself in the BigQuery console (<https://console.cloud.google.com/bigquery>):
```sql
SELECT month, mrr, active_customers, arpu, nrr_12m
FROM `YOUR_PROJECT_ID.analytics.mart_kpi_monthly`
WHERE scope = 'All' AND month IN ('2019-01-01', '2023-12-01')
ORDER BY month;
-- expected: 2019-01  MRR 203,078.39  / 37,109 customers
--           2023-12  MRR 191,263.23  / 33,099 customers / NRR(12m) 85.18%
```

### Step 6 - Build the dashboard
Follow `looker_studio/DASHBOARD_GUIDE.md` (about 45 minutes, no code). Result: a 6-page report -
Executive overview, MRR movements, Product categories, Retention & cohorts, Customers & cross-sell, Data confidence.

## Everyday commands

```bash
python pipeline/run_pipeline.py --mode full                              # rebuild everything
python pipeline/run_pipeline.py --mode incremental --lookback 3          # last 3 months (daily job)
python pipeline/run_pipeline.py --mode backfill --start 2020-01 --end 2020-06
python pipeline/run_pipeline.py --mode full --force-ingest               # reload CSVs even if unchanged
```

## How the brief is answered

| Deliverable | Where |
|---|---|
| 1. Transformation logic | `pipeline/` (Python orchestration, `sql/silver`, `sql/gold`, `dq_rules.py`) |
| 2. Data product definition | `docs/data_products.md` |
| 3. KPI framework (10 KPIs) | `docs/kpi_framework.md`, implemented in `gold.mart_kpi_monthly` |
| 4. Insights memo (data quality, churn/active definition, 200k -> 200M rows) | `docs/insights_memo.md` |
| Presentation | Looker Studio dashboard (`looker_studio/`) + `docs/architecture_gcp.md` |

## Key modelling decisions (details in the memo)

* **Active** = customer MRR > 0 at month end; **Churn** = active last month, not active this month.
  Lifecycle comes from the subscription master (start/expire dates), price from the order lines - because the
  orders file is a sample and order gaps do not mean churn.
* **MRR** = billing-line price / duration in months; 0.01 EUR placeholder lines are free (MRR 0); fully refunded
  periods are voided; exact duplicate rows are removed (conservation-checked).
* **Month-end snapshot** convention; every MRR change is classified exactly once
  (new, reactivation, expansion, contraction, churn) so the bridge always balances (asserted).
* **Known limits, surfaced not hidden:** the extract under-samples recent acquisitions (flag
  `is_acquisition_complete`), and most prices are imputed from the nearest billing line (`imputed_mrr_share`).
