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
| `terraform/` | Creates everything on Google Cloud: bucket, 5 BigQuery datasets, service account, permissions, optional daily schedule |
| `pipeline/` | The Python + SQL pipeline (`run_pipeline.py` is the entry point; SQL is in `pipeline/sql/`) |
| `looker_studio/DASHBOARD_GUIDE.md` | Click-by-click guide to build the 6-page dashboard |
| `tests/` | `test_static.py` (offline), `test_acceptance.py` (BigQuery vs reference), `test_idempotency.py` |
| `reference/` | Validated results of the same model, used as the answer key for the acceptance test |
| `docs/` | **Deliverables 2-4 of the brief**: `data_products.md`, `kpi_framework.md` (10 KPIs), `insights_memo.md`, plus `architecture_gcp.md` |
| `scripts/` | `upload_raw.py` (send CSVs to the bucket), `deploy_job.sh` (optional automation) |

> **Honest verification status.** In the environment where this package was assembled there was no internet and no
> Google Cloud account, so the pipeline could **not** be executed against live BigQuery here. What *was* checked:
> all 11 SQL models and 40 data-quality rules pass the offline static tests (`tests/test_static.py`: no DuckDB-only
> syntax, balanced SQL, correct model order, valid references), and the business logic is a line-by-line port of a
> model that was run and validated on this data. Your first full run, followed by `tests/test_acceptance.py`, is the
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
   * **Terraform** (1.5 or newer): <https://developer.hashicorp.com/terraform/install>
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

### Step 3 - Offline self-check (free, 2 seconds)
```bash
python tests/test_static.py
```
Expected last line: `ALL STATIC CHECKS PASSED`.

### Step 4 - Create the cloud infrastructure with Terraform
```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars      # Windows: copy terraform.tfvars.example terraform.tfvars
```
Open `terraform.tfvars` in any text editor and set `project_id` (your Project ID) and `analytics_viewers`
(your Google account, e.g. `["user:you@gmail.com"]`). Save, then:
```bash
terraform init
terraform plan          # shows what will be created: 8 APIs, 1 bucket, 5 datasets, 1 service account, permissions
terraform apply         # type "yes" when asked
cd ..
```
If a message says an API is not enabled yet, wait one minute and run `terraform apply` again.

### Step 5 - Tell the pipeline where things are
Mac/Linux:
```bash
eval "$(terraform -chdir=terraform output -raw env_exports)"
```
Windows PowerShell (use the values printed by `terraform output`):
```powershell
$env:GCP_PROJECT="YOUR_PROJECT_ID"; $env:BQ_LOCATION="EU"; $env:RAW_BUCKET="YOUR_PROJECT_ID-teamblue-raw"
```

### Step 6 - Upload the four CSV files
Use the folder that contains `customers(in).csv`, `orders(in).csv`, `subscriptions(in).csv`, `products(in).csv`:
```bash
python scripts/upload_raw.py "/path/to/Analytics Engineer assignment"
```
It stores them as `gs://<bucket>/raw/customers.csv`, etc.

### Step 7 - Run the pipeline
```bash
python pipeline/run_pipeline.py --mode full --export-dir outputs
```
Takes a few minutes. You will see a line per model and per layer of data-quality checks. What good looks like:

| Check | Expected |
|---|---|
| bronze rows | customers 92,042 - orders 210,000 - subscriptions 170,245 - products 1,462 |
| last line | `run <id> succeeded: ... total, ... rows written` |
| data-quality | all `error` rules pass. `WARN` lines are expected for known, accepted issues - in the validated reference run it was `gold_imputed_mrr_share` (the orders file is a sample, so many months are priced from the nearest billing line - reported, not hidden) |

If a blocking rule fails, the run stops with `BLOCKED: ...` and **nothing is published** (by design).

### Step 8 - Prove it is correct
```bash
python tests/test_acceptance.py
```
Expected: `PASS: BigQuery results match the reference model exactly.`
Optional (a few cents of BigQuery): `python tests/test_idempotency.py` proves that re-runs, incremental runs and
backfills give identical results to a full rebuild.

Spot-check numbers yourself in the BigQuery console (<https://console.cloud.google.com/bigquery>):
```sql
SELECT month, mrr, active_customers, arpu, nrr_12m
FROM `YOUR_PROJECT_ID.analytics.mart_kpi_monthly`
WHERE scope = 'All' AND month IN ('2019-01-01', '2023-12-01')
ORDER BY month;
-- expected: 2019-01  MRR 203,078.39  / 37,109 customers
--           2023-12  MRR 191,263.23  / 33,099 customers / NRR(12m) 85.18%
```

### Step 9 - Build the dashboard
Follow `looker_studio/DASHBOARD_GUIDE.md` (about 45 minutes, no code). Result: a 6-page report -
Executive overview, MRR movements, Product categories, Retention & cohorts, Customers & cross-sell, Data confidence.

### Step 10 (optional) - Run it automatically every morning
```bash
export GCP_PROJECT=YOUR_PROJECT_ID
./scripts/deploy_job.sh            # builds the container, creates the Cloud Run Job + Cloud Scheduler trigger
```
The job runs `--mode incremental --lookback 3` daily at 06:00 (Berlin time): it reloads files only if they changed and
recomputes the last 3 months. Run it on demand with
`gcloud run jobs execute teamblue-pipeline --region europe-west1 --wait`.

### Step 11 - Clean up (stops all cost)
Set `force_destroy = true` in `terraform.tfvars`, run `terraform apply`, then `terraform destroy`.

---

## Everyday commands

```bash
python pipeline/run_pipeline.py --mode full                              # rebuild everything
python pipeline/run_pipeline.py --mode incremental --lookback 3          # last 3 months (daily job)
python pipeline/run_pipeline.py --mode backfill --start 2020-01 --end 2020-06
python pipeline/run_pipeline.py --mode full --force-ingest               # reload CSVs even if unchanged
```

## Troubleshooting

| Message | Meaning / fix |
|---|---|
| `GCP_PROJECT is not set` / `RAW_BUCKET is not set` | Redo Step 5 in the same terminal window |
| `Could not automatically determine credentials` | Run `gcloud auth application-default login` |
| `... not found. Upload the CSVs first` | Redo Step 6 |
| `403 Access Denied: ... bigquery.jobs.create` | Your account lacks permission on the project: use the Owner account, or add yourself to `analytics_viewers` and ask an Owner for *BigQuery Admin* |
| `Not found: Dataset ... was not found in location EU` | Run Step 4; do not mix locations (`BQ_LOCATION` must equal Terraform `bq_location`) |
| `Error 403: ... has not been used in project ... before or it is disabled` | An API is still enabling; wait a minute and retry `terraform apply` |
| `BLOCKED: silver: blocking DQ rules failed: [...]` | The data failed an error-level check. Read the printed rule and `analytics.dq_results_latest`; fix the source file, re-upload, re-run |
| Cloud Build cannot push the image | Grant the Cloud Build service account *Artifact Registry Writer*, then re-run `deploy_job.sh` |
| Looker Studio: "no data / permission" | Data source credentials must be *Owner's credentials*, and the owner needs Data Viewer on `analytics` |

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
