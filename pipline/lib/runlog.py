"""
Run metadata + throughput instrumentation (BigQuery edition).

Every model execution is recorded in meta.model_runs with rows in/out,
wall-clock seconds, rows/second, bytes billed and write strategy. This is the
data used to reason about capacity and cost (see docs/architecture_gcp.md).

Write pattern (important in BigQuery):
  * meta.pipeline_runs  : DML INSERT at start, DML UPDATE at finish (small, low volume)
  * meta.model_runs     : buffered in memory, appended with a BATCH LOAD job
  * meta.dq_results     : buffered in memory, appended with a BATCH LOAD job
Batch loads (not streaming inserts) avoid the streaming-buffer restriction and are free.
"""
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from google.cloud import bigquery

import config as C
from lib.bq import run

DDL = """
CREATE TABLE IF NOT EXISTS meta.pipeline_runs (
    run_id STRING, started_at TIMESTAMP, finished_at TIMESTAMP,
    mode STRING,                 -- full | incremental | backfill
    window_start DATE, window_end DATE,
    status STRING,               -- running | success | failed | blocked_by_dq
    message STRING
);
CREATE TABLE IF NOT EXISTS meta.model_runs (
    run_id STRING, layer STRING, model STRING, started_at TIMESTAMP,
    seconds FLOAT64, rows_in INT64, rows_out INT64, rows_per_second FLOAT64,
    output_mb FLOAT64, bytes_billed INT64,
    write_strategy STRING        -- replace | partition_overwrite | skip_unchanged
);
CREATE TABLE IF NOT EXISTS meta.dq_results (
    run_id STRING, layer STRING, rule_id STRING, table_name STRING,
    description STRING, severity STRING, action STRING,
    failed_rows INT64, total_rows INT64, failure_rate FLOAT64,
    threshold FLOAT64, passed BOOL, checked_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS meta.ingestion_log (
    source STRING, batch_id STRING, file_name STRING, file_bytes INT64,
    row_count INT64, ingested_at TIMESTAMP, run_id STRING
);
"""

MODEL_RUNS_SCHEMA = [
    bigquery.SchemaField("run_id", "STRING"), bigquery.SchemaField("layer", "STRING"),
    bigquery.SchemaField("model", "STRING"), bigquery.SchemaField("started_at", "TIMESTAMP"),
    bigquery.SchemaField("seconds", "FLOAT64"), bigquery.SchemaField("rows_in", "INT64"),
    bigquery.SchemaField("rows_out", "INT64"), bigquery.SchemaField("rows_per_second", "FLOAT64"),
    bigquery.SchemaField("output_mb", "FLOAT64"), bigquery.SchemaField("bytes_billed", "INT64"),
    bigquery.SchemaField("write_strategy", "STRING"),
]
DQ_SCHEMA = [
    bigquery.SchemaField("run_id", "STRING"), bigquery.SchemaField("layer", "STRING"),
    bigquery.SchemaField("rule_id", "STRING"), bigquery.SchemaField("table_name", "STRING"),
    bigquery.SchemaField("description", "STRING"), bigquery.SchemaField("severity", "STRING"),
    bigquery.SchemaField("action", "STRING"), bigquery.SchemaField("failed_rows", "INT64"),
    bigquery.SchemaField("total_rows", "INT64"), bigquery.SchemaField("failure_rate", "FLOAT64"),
    bigquery.SchemaField("threshold", "FLOAT64"), bigquery.SchemaField("passed", "BOOL"),
    bigquery.SchemaField("checked_at", "TIMESTAMP"),
]


def now():
    return datetime.now(timezone.utc)


class RunContext:
    def __init__(self, client, mode, window_start, window_end):
        self.client = client
        self.mode = mode
        self.window_start = window_start
        self.window_end = window_end
        self.run_id = now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.total_seconds = 0.0
        self.total_rows = 0
        self._model_rows = []
        self._dq_rows = []
        run(client, DDL)
        run(client,
            "INSERT INTO meta.pipeline_runs (run_id, started_at, mode, window_start, window_end, status) "
            "VALUES (@run_id, @started, @mode, @ws, @we, 'running')",
            [bigquery.ScalarQueryParameter("run_id", "STRING", self.run_id),
             bigquery.ScalarQueryParameter("started", "TIMESTAMP", now()),
             bigquery.ScalarQueryParameter("mode", "STRING", mode),
             bigquery.ScalarQueryParameter("ws", "DATE", window_start),
             bigquery.ScalarQueryParameter("we", "DATE", window_end)])

    # ------------------------------------------------------------------ logs
    def add_dq(self, row: dict):
        self._dq_rows.append(row)

    def flush(self):
        """Append buffered logs with batch load jobs (idempotent per run: rows are only sent once)."""
        for rows, table, schema in ((self._model_rows, "model_runs", MODEL_RUNS_SCHEMA),
                                    (self._dq_rows, "dq_results", DQ_SCHEMA)):
            if not rows:
                continue
            cfg = bigquery.LoadJobConfig(
                schema=schema, write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON)
            self.client.load_table_from_json(
                rows, f"{C.GCP_PROJECT_ID}.meta.{table}", job_config=cfg).result()
            rows.clear()

    def finish(self, status, message=None):
        self.flush()
        run(self.client,
            "UPDATE meta.pipeline_runs SET finished_at=@f, status=@s, message=@m WHERE run_id=@run_id",
            [bigquery.ScalarQueryParameter("f", "TIMESTAMP", now()),
             bigquery.ScalarQueryParameter("s", "STRING", status),
             bigquery.ScalarQueryParameter("m", "STRING", message),
             bigquery.ScalarQueryParameter("run_id", "STRING", self.run_id)])

    # ----------------------------------------------------------------- model
    @contextmanager
    def model(self, layer, name, write_strategy="replace"):
        """Time a model and record throughput. The body fills stats['rows_in'/'rows_out'/
        'output_mb'/'bytes_billed']."""
        stats = {"rows_in": None, "rows_out": None, "output_mb": None, "bytes_billed": None}
        t0 = time.perf_counter()
        started = now()
        yield stats
        secs = time.perf_counter() - t0
        rows = stats["rows_out"] or 0
        rps = rows / secs if secs > 0 else None
        self.total_seconds += secs
        self.total_rows += rows
        self._model_rows.append({
            "run_id": self.run_id, "layer": layer, "model": name,
            "started_at": started.isoformat(), "seconds": round(secs, 3),
            "rows_in": stats["rows_in"], "rows_out": stats["rows_out"],
            "rows_per_second": rps, "output_mb": stats["output_mb"],
            "bytes_billed": stats["bytes_billed"], "write_strategy": write_strategy})
        print(f"  [{layer:6}] {name:38} {rows:>10,} rows  {secs:6.2f}s  "
              f"{(rps or 0):>12,.0f} rows/s  ({write_strategy})")