# Architecture on Google Cloud

```
 CSV files (4)                 Cloud Storage                BigQuery                                   Looker Studio
 ─────────────   upload   ┌──────────────────────┐   load   ┌────────┐  ┌────────┐  ┌────────┐  audit  ┌───────────┐   ┌────────────┐
 customers.csv ─────────► │ gs://<project>-       │ ───────► │ bronze │─►│ silver │─►│  gold  │ ──────► │ analytics │──►│ dashboards │
 orders.csv               │ teamblue-raw/raw/     │  (job)   │ raw +  │  │ clean  │  │ facts, │  (DQ    │ published │   │  (6 pages) │
 subscriptions.csv        │ versioned, private    │          │ lineage│  │ typed  │  │ marts  │  gate)  │ tables    │   └────────────┘
 products.csv             └──────────────────────┘          └────────┘  └────────┘  └────────┘         └───────────┘
                                                                  ▲ meta: runs · model throughput · DQ results · ingestion log
 Cloud Scheduler ─► Cloud Run Job (python pipeline/run_pipeline.py --mode incremental) ─ identity: teamblue-pipeline service account
```

## Components

| Component | Service | Why |
|---|---|---|
| Landing zone | Cloud Storage bucket (EU, versioned, public access blocked) | cheap immutable drop zone; BigQuery loads straight from it |
| Warehouse | BigQuery, 5 datasets: `meta`, `bronze`, `silver`, `gold`, `analytics` | serverless, partitioned + clustered, scales to 200M+ rows without re-platforming |
| Transform | SQL files in `pipeline/sql/**`, executed by Python | transparent logic a reviewer can read; one file per model |
| Orchestration | Python (`run_pipeline.py`) in a Cloud Run Job, started by Cloud Scheduler | right-sized for 200k rows; swap for Dataform / Composer at scale (see memo §3) |
| Data quality | 40 SQL rules in `pipeline/dq_rules.py`, results in `meta.dq_results` | circuit breaker before publish; trended over time |
| BI | Looker Studio on the `analytics` dataset | free, shareable, no server to run |
| Infrastructure as code | Terraform (`terraform/`) | reproducible environment: `terraform apply` / `destroy` |

## Layers

| Layer | Dataset | Content | Write strategy |
|---|---|---|---|
| Landing | GCS | the four CSVs exactly as received | upload (versioned) |
| Bronze | `bronze` | every column as STRING + `_row_hash`, `_source_file`, `batch_id`, `_ingested_at`; table `<src>_all` holds all batches, view `<src>` = latest | content-hash `batch_id`; load job into staging, then one transaction (delete batch + insert); unchanged file => skipped |
| Silver | `silver` | typed, cleaned, deduplicated, classified order lines / customers / subscriptions / products + quarantine tables | `CREATE OR REPLACE` (deterministic) |
| Gold | `gold` | `dim_product`, `dim_customer`, three monthly facts, four marts | facts: partition overwrite by month; dims/marts: replace |
| Publish | `analytics` | audited copies of gold + BI helper objects | copy jobs after the DQ gate passes |

## Differences from the DuckDB prototype (same business logic, GCP-native mechanics)

| Topic | DuckDB original | GCP version |
|---|---|---|
| Storage | local Parquet + `.duckdb` file | GCS + BigQuery datasets |
| Bronze batch id | SHA-256 of file | GCS content hash (MD5) - same "same file = same batch" property, no download needed |
| Row-number lineage | `_source_row_number` | dropped (BigQuery loads cannot number rows deterministically); `_row_hash` + `_source_file` + `batch_id` identify any row |
| CSV quoting | DuckDB parser tolerated one malformed line | quote parsing disabled in the load job; silver repairs the line explicitly (product 164202) |
| Money type | DECIMAL(18,4) / double | NUMERIC (exact), rounded to 4 dp as in the original |
| Facts | `partition_overwrite` via temp table | partitioned table; incremental = one transaction (`DELETE` window + `INSERT`) |
| Publish | `CREATE TABLE AS` copy | BigQuery copy jobs (free) + `analytics` BI helpers |
| Logging | DML on DuckDB | DML for run rows, **batch load jobs** for model/DQ logs (no streaming buffer) |
| BI | matplotlib charts | Looker Studio |

## Security model (least privilege)

* The pipeline runs as **`teamblue-pipeline`**: BigQuery Job User on the project, Data Editor only on the five
  datasets, Object Admin only on the landing bucket. It has no project-wide data access.
* Dashboard users get **Data Viewer on `analytics` only** (or none at all if the data source uses the owner's
  credentials, which is the default in the guide).
* The bucket enforces public-access prevention and uniform bucket-level access; objects are versioned.
* No keys are created or stored: local runs use your own login (Application Default Credentials), Cloud Run uses
  the service account identity.

## Cost (this dataset: ~35 MB of CSV)

BigQuery's free tier (1 TB queries and 10 GB storage per month) covers a full rebuild many times over; Cloud Run
Jobs, Cloud Scheduler (3 free jobs) and a 35 MB bucket are effectively free. Expect well under 1 USD a month.
Cost is visible per model in `meta.model_runs.bytes_billed`.

## Failure behaviour

| Event | Behaviour |
|---|---|
| Same file uploaded again | bronze reports `skip_unchanged`; nothing is rewritten |
| Changed file | new `batch_id`; old batch kept; silver/gold rebuild from the latest |
| Error DQ rule breaches threshold | run status `blocked_by_dq`, exit code 2, `analytics` untouched |
| Any exception | run row set to `failed` with the message; partial fact windows roll back (transaction) |
| Re-run / backfill of a window | identical result (idempotent), proven by `tests/test_idempotency.py` |
