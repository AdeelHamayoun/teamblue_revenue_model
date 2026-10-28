"""
Orchestrator: Bronze -> Silver -> Gold -> (audit) -> Publish, on BigQuery.

Pattern: WRITE - AUDIT - PUBLISH
  * every layer is written to its own dataset (bronze / silver / gold)
  * data-quality rules run after each layer; an 'error' rule that breaches its
    threshold stops the run BEFORE anything is published
  * only audited gold tables are copied into the `analytics` dataset that BI reads,
    so Looker Studio keeps serving the last good version when a run is blocked

Usage
-----
  python pipeline/run_pipeline.py --mode full
  python pipeline/run_pipeline.py --mode incremental --lookback 3
  python pipeline/run_pipeline.py --mode backfill --start 2020-01 --end 2020-06
  python pipeline/run_pipeline.py --mode full --export-dir outputs     # also write CSV extracts

Environment: GCP_PROJECT, RAW_BUCKET (required); BQ_LOCATION (default EU), RAW_PREFIX (default raw).
Authentication: Application Default Credentials (`gcloud auth application-default login`)
or the Cloud Run service account.
"""
import argparse
import csv
import sys
from datetime import date
from pathlib import Path
from google.cloud import bigquery as bq

import config as C
from dq_rules import RULES
from lib.bq import first_row, get_client, run, table_exists
from lib.bronze import run_pipeline
from lib.dq import DQBlocked, run_rules
from lib.runlog import RunContext
from lib.sqlutil import add_months, month, render

SQL = Path(__file__).parent / "sql"

INCREMENTAL_FACTS = [  # order matters (dependencies)
    "fct_subscription_month",
    "fct_customer_category_month",
    "fct_customer_month",
]
CLUSTER_KEYS = {
    "fct_subscription_month": "client_id, subscription_key",
    "fct_customer_category_month": "client_id, product_category",
    "fct_customer_month": "client_id",
}
PUBLISHED = [
    "dim_customer", "dim_product",
    "fct_subscription_month", "fct_customer_category_month", "fct_customer_month",
    "mart_mrr_bridge_monthly", "mart_category_monthly", "mart_cohort_retention", "mart_kpi_monthly",
]
EXPORTED = ["mart_mrr_bridge_monthly", "mart_category_monthly", "mart_cohort_retention",
            "mart_kpi_monthly", "dim_customer", "dim_product"]


# ----------------------------------------------------------------- helpers --
def run_script(ctx, layer, name, sqlfile, out_table=None, **params):
    """Full-refresh model(s): CREATE OR REPLACE is idempotent by construction."""
    with ctx.model(layer, name, "replace") as st:
        job = run(ctx.client, render(SQL / layer / f"{sqlfile}.sql", **params))
        out = out_table or f"{layer}.{name}"
        st["rows_out"] = int(first_row(ctx.client, f"SELECT COUNT(*) FROM {out}")[0])
        st["bytes_billed"] = job.total_bytes_billed

def partition_overwrite(ctx, name, win_start: date, win_end: date, full: bool):
    """Monthly fact tables, partitioned by month.
    full / first build : one atomic CREATE OR REPLACE TABLE ... AS SELECT
    incremental / backfill : one transaction that deletes the window's partitions and
                             re-inserts them (atomic, idempotent, backfillable)."""
    client = ctx.client
    table = f"gold.{name}"
    params = dict(WIN_START=win_start, WIN_END=win_end, PREV_MONTH=add_months(win_start, -1))
    body = render(SQL / "gold" / f"{name}.sql", **params)
    where = f"month BETWEEN DATE '{win_start}' AND DATE '{win_end}'"
    rebuild = full or not table_exists(client, "gold", name)
    strategy = "replace" if rebuild else f"partition_overwrite[{win_start:%Y-%m}..{win_end:%Y-%m}]"
    with ctx.model("gold", name, strategy) as st:
        if rebuild:
            job = run(client, f"CREATE OR REPLACE TABLE {table}\n"
                              f"PARTITION BY DATE_TRUNC(month, MONTH)\nCLUSTER BY {CLUSTER_KEYS[name]}\nAS\n{body}")
        else:
            job = run(client, f"""
                BEGIN
                  BEGIN TRANSACTION;
                  DELETE FROM {table} WHERE {where};
                  INSERT INTO {table}
                  {body};
                  COMMIT TRANSACTION;
                EXCEPTION WHEN ERROR THEN
                  ROLLBACK TRANSACTION;
                  RAISE;
                END;""")
        st["rows_out"] = st["rows_in"] = int(first_row(client, f"SELECT COUNT(*) FROM {table} WHERE {where}")[0])
        st["bytes_billed"] = job.total_bytes_billed

def seed_status_map(client):
    """Business mapping lives in config.py (versioned with the code) and is copied to meta.status_map."""
    def lit(v):
        return "CAST(NULL AS STRING)" if v is None else "'" + v.replace("\\", "\\\\").replace("'", "\\'") + "'"
    selects = [f"SELECT {lit(k)} AS status, {lit(v)} AS status_group" if i == 0
               else f"SELECT {lit(k)}, {lit(v)}"
               for i, (k, v) in enumerate(C.STATUS_GROUPS.items())]
    run(client, "CREATE OR REPLACE TABLE meta.status_map AS\n" + "\nUNION ALL\n".join(selects))


# -------------------------------------------------------------------- main --
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["full", "incremental", "backfill"], default="full")
    ap.add_argument("--start", help="YYYY-MM (backfill)")
    ap.add_argument("--end", help="YYYY-MM (backfill)")
    ap.add_argument("--lookback", type=int, default=3, help="months recomputed in incremental mode")
    ap.add_argument("--force-ingest", action="store_true", default="FALSE", help="reload CSVs even if unchanged")
    ap.add_argument("--export-dir", help="also write CSV extracts of the marts to this folder")
    a = ap.parse_args()

    if not C.BUCKET_NAME:
        sys.exit("BUCKET_NAME is not set")

    opening, report_end = month(C.OPENING_MONTH), month(C.REPORT_END)
    if a.mode == "full":
        win_start, win_end = opening, report_end
    elif a.mode == "incremental":
        win_start, win_end = max(opening, add_months(report_end, -(a.lookback - 1))), report_end
    else:
        if not (a.start and a.end):
            ap.error("--mode backfill requires --start and --end")
        win_start, win_end = max(opening, month(a.start)), min(report_end, month(a.end))

    client = get_client()
    ctx = RunContext(client, a.mode, win_start, win_end)
    print(f"\nrun {ctx.run_id}  project={C.GCP_PROJECT_ID}  mode={a.mode}  window={win_start:%Y-%m}..{win_end:%Y-%m}\n")

    try:
        if a.mode != "full" and not table_exists(client, "gold", "fct_subscription_month"):
            raise SystemExit("No gold history yet: run --mode full first.")

        seed_status_map(client)

        # ------------------------------------------------------------ BRONZE
        # run_pipeline(force=a.force_ingest)
        # run_rules(ctx, RULES, "bronze", C.DQ_FAIL_ON_ERROR)

        # ------------------------------------------------------------ SILVER
        # for m in ["customers", "products", "subscriptions", "order_lines"]:
        #     run_script(ctx, "silver", m, m)
        # run_rules(ctx, RULES, "silver", C.DQ_FAIL_ON_ERROR)

        # ------------------------------------------------------------ GOLD
        run_script(ctx, "gold", "dim_product", "dim_product")
        run_script(ctx, "gold", "int_subscriptions", "int_subscriptions")
        full = a.mode == "full"
        # month M+1 movements depend on month M -> propagate one dependent month
        dep_end = win_end if full else min(report_end, add_months(win_end, 1))
        partition_overwrite(ctx, "fct_subscription_month", win_start, win_end, full)
        partition_overwrite(ctx, "fct_customer_category_month", win_start, dep_end, full)
        partition_overwrite(ctx, "fct_customer_month", win_start, dep_end, full)
        run_script(ctx, "gold", "dim_customer", "dim_customer")
        run_script(ctx, "gold", "marts", "marts", out_table="gold.mart_kpi_monthly")
        run_rules(ctx, RULES, "gold", C.DQ_FAIL_ON_ERROR)

    except DQBlocked as e:
        ctx.finish("blocked_by_dq", str(e))
        print(f"\nBLOCKED: {e}\nanalytics.* was NOT refreshed (last good version still served).")
        sys.exit(2)
    except BaseException as e:  # incl. SystemExit, so the run row never stays 'running'
        try:
            ctx.finish("failed", str(e)[:500])
        except Exception:
            pass
        raise

    print(f"\nrun {ctx.run_id} succeeded: {ctx.total_seconds:.1f}s total, {ctx.total_rows:,} rows written\n")


if __name__ == "__main__":
    main()