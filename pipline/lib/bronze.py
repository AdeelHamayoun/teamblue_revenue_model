from google.cloud import storage, bigquery
from pathlib import Path
# import config as C
from config import GCP_PROJECT_ID, BUCKET_NAME, REGION, LOCAL_DATA_DIR, SOURCES, LOCAL_PARQUET_DIR
import pandas as pd
import hashlib
import time
import os

def clean_col(name: str) -> str:
    """Sanitizes column names matching the project's cleaning logic."""
    return name.replace("\ufeff", "").strip().strip(",").strip('"').strip().lower()

def initialize_gcp_resources():
    """Initializes GCS bucket and BigQuery datasets (including meta and bronze)."""
    print(f"=== [GCP Setup] Initializing Project: {GCP_PROJECT_ID} ===")
    storage_client = storage.Client(project=GCP_PROJECT_ID)
    bq_client = bigquery.Client(project=GCP_PROJECT_ID)

    # 1. Ensure GCS Bucket exists
    bucket = storage_client.bucket(BUCKET_NAME)
    if not bucket.exists():
        storage_client.create_bucket(bucket_name=BUCKET_NAME, location=REGION)
        print(f"Created GCS bucket: gs://{BUCKET_NAME}")
    else:
        print(f"GCS bucket gs://{BUCKET_NAME} verified.")

    # 2. Ensure BigQuery Datasets exist
    for dataset_id in ["bronze", "silver", "gold", "meta"]:
        dataset_ref = bigquery.Dataset(f"{GCP_PROJECT_ID}.{dataset_id}")
        dataset_ref.location = REGION
        bq_client.create_dataset(dataset_ref, exists_ok=True)
        print(f"Verified BigQuery dataset: {dataset_id}")

    # 3. Create ingestion_log table in meta dataset if it doesn't exist
    log_ddl = f"""
    CREATE TABLE IF NOT EXISTS `{GCP_PROJECT_ID}.meta.ingestion_log` (
        source STRING, 
        batch_id STRING, 
        file_name STRING, 
        file_bytes INT64,
        row_count INT64, 
        ingested_at TIMESTAMP, 
        run_id STRING
    )
    """
    bq_client.query(log_ddl).result()
    print("Verified BigQuery table: meta.ingestion_log")

def run_pipeline(force: bool = False):
    start_time = time.time()
    initialize_gcp_resources()

    """
    Executes ingestion with content-addressed batch idempotency (`skip_unchanged` vs `partition_overwrite`).
    """
    storage_client = storage.Client(project=GCP_PROJECT_ID)
    bq_client = bigquery.Client(project=GCP_PROJECT_ID)
    bucket = storage_client.bucket(BUCKET_NAME)
    os.makedirs(LOCAL_PARQUET_DIR, exist_ok=True)

    run_id = f"run_{int(time.time())}"
    print(f"\n=== [Pipeline Execution] Run ID: {run_id} (Force: {force}) ===")

    for source_key, filename in SOURCES.items():
        local_path = Path(LOCAL_DATA_DIR) / filename
        if not local_path.exists():
            print(f"Warning: Local file {local_path} not found.")
            continue

        # 1. Compute file SHA-256 batch ID
        hasher = hashlib.sha256()
        with open(local_path, "rb") as f:
            while chunk := f.read(1 << 20):
                hasher.update(chunk)
        batch_id = hasher.hexdigest()[:16]
        file_bytes = local_path.stat().st_size

        # 2. Check if batch is already ingested in BigQuery meta.ingestion_log
        check_query = f"""
        SELECT COUNT(*) FROM `{GCP_PROJECT_ID}.meta.ingestion_log` 
        WHERE source = @source AND batch_id = @batch_id
        """
        query_job = bq_client.query(
            check_query,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("source", "STRING", source_key),
                    bigquery.ScalarQueryParameter("batch_id", "STRING", batch_id),
                ]
            )
        )
        already_logged = list(query_job.result())[0][0] > 0

        gcs_blob_name = f"lake/bronze/{source_key}/batch_id={batch_id}/part-0.parquet"
        blob_exists = bucket.blob(gcs_blob_name).exists()

        # 3. Determine strategy: skip_unchanged vs partition_overwrite
        strategy = "skip_unchanged" if (already_logged and blob_exists and not force) else "partition_overwrite"
        print(f"Source: {source_key} | Batch ID: {batch_id} | Strategy: {strategy}")

        if strategy == "skip_unchanged":
            print(f"--> Skipping ingestion for '{source_key}' (content unchanged).")
        else:
            # Robust manual line parsing: clean quotes and split strictly by semicolon
            rows = []
            with open(local_path, "r", encoding="utf-8-sig") as f:
                header_line = f.readline().strip()
                # Clean header columns
                header = [c.replace('\ufeff', '').strip(',').strip('"').strip().lower() for c in header_line.split(';')]
                header = [c for c in header if c and not c.startswith('unnamed')]

                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    # Strip wrapping quotes if the entire line is wrapped in them
                    if line.startswith('"') and line.endswith('"'):
                        line = line[1:-1]

                    # Split strictly by semicolon
                    parts = line.split(';')

                    # Clean each part (remove wrapping quotes, doubled quotes, extra whitespace)
                    cleaned_parts = []
                    for p in parts:
                        p = p.strip()
                        while p.startswith('"') and p.endswith('"'):
                            p = p[1:-1].strip()
                        p = p.replace('""', '"').replace('"""', '"').strip('"')
                        cleaned_parts.append(p)

                    rows.append(cleaned_parts[:len(header)])

            df = pd.DataFrame(rows, columns=header[:len(rows[0])])

            # Drop any trailing empty/unnamed columns from dataframe
            df = df.loc[:, ~df.columns.str.contains('^unnamed', case=False)]

            # Row hash generation
            row_str_values = df.fillna("").astype(str).agg('|'.join, axis=1)
            df['_row_hash'] = row_str_values.apply(lambda x: hashlib.md5(x.encode()).hexdigest())

            df['_source_row_number'] = range(1, len(df) + 1)
            df['_source_file'] = filename
            df['_ingested_at'] = pd.Timestamp.now()
            row_count = len(df)

            # Save locally as Parquet
            local_parquet_path = f"{LOCAL_PARQUET_DIR}/{source_key}.parquet"
            df.to_parquet(local_parquet_path, index=False)

            # Upload to GCS Data Lake partition & latest
            gcs_blob_name = f"lake/bronze/{source_key}/batch_id={batch_id}/part-0.parquet"
            latest_blob_name = f"lake/bronze/{source_key}/latest.parquet"

            bucket.blob(gcs_blob_name).upload_from_filename(local_parquet_path)
            bucket.blob(latest_blob_name).upload_from_filename(local_parquet_path)
            print(f"--> Uploaded partition & latest for '{source_key}' ({row_count:,} rows).")

            # Update meta ingestion log
            log_cleanup_sql = f"""
                    DELETE FROM `{GCP_PROJECT_ID}.meta.ingestion_log` 
                    WHERE source = @source AND batch_id = @batch_id
                    """
            bq_client.query(
                log_cleanup_sql,
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter("source", "STRING", source_key),
                        bigquery.ScalarQueryParameter("batch_id", "STRING", batch_id),
                    ]
                )
            ).result()

            log_insert_sql = f"""
                    INSERT INTO `{GCP_PROJECT_ID}.meta.ingestion_log` 
                    (source, batch_id, file_name, file_bytes, row_count, ingested_at, run_id)
                    VALUES (@source, @batch_id, @file_name, @file_bytes, @row_count, CURRENT_TIMESTAMP(), @run_id)
                    """
            bq_client.query(
                log_insert_sql,
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter("source", "STRING", source_key),
                        bigquery.ScalarQueryParameter("batch_id", "STRING", batch_id),
                        bigquery.ScalarQueryParameter("file_name", "STRING", filename),
                        bigquery.ScalarQueryParameter("file_bytes", "INT64", file_bytes),
                        bigquery.ScalarQueryParameter("row_count", "INT64", row_count),
                        bigquery.ScalarQueryParameter("run_id", "STRING", run_id),
                    ]
                )
            ).result()

        # 4. Sync GCS Lake latest Parquet file to BigQuery bronze table
        table_id = f"{GCP_PROJECT_ID}.bronze.{source_key}"
        uri = f"gs://{BUCKET_NAME}/lake/bronze/{source_key}/latest.parquet"

        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.PARQUET,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        )
        load_job = bq_client.load_table_from_uri(uri, table_id, job_config=job_config)
        load_job.result()
        dest_table = bq_client.get_table(table_id)
        print(f"--> BigQuery Table '{table_id}' synced successfully ({dest_table.num_rows:,} rows).\n")

    print(f"Pipeline finished successfully in {time.time() - start_time:.2f} seconds.")

# if __name__ == "__main__":
#     start_time = time.time()
#     initialize_gcp_resources()
#     # run_pipeline(force=False)

