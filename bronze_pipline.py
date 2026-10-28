from google.cloud import storage, bigquery
from pathlib import Path
import os

# GCP Configuration
GCP_PROJECT_ID = "teambluerevenuemodel"
BUCKET_NAME = "teamblue_data_source"
REGION = "europe-west1"


def initialize_gcp_resources():
    """Initializes GCS bucket and BigQuery datasets if they don't exist."""
    storage_client = storage.Client(project=GCP_PROJECT_ID)
    bq_client = bigquery.Client(project=GCP_PROJECT_ID)

    # 1. Ensure GCS Bucket exists
    bucket = storage_client.bucket(BUCKET_NAME)
    if not bucket.exists():
        bucket = storage_client.create_bucket(bucket_name=BUCKET_NAME, location=REGION)
        print(f"Created GCS bucket: gs://{BUCKET_NAME}")
    else:
        print(f"GCS bucket gs://{BUCKET_NAME} already exists.")

    # 2. Ensure BigQuery Datasets exist
    for dataset_id in ["bronze", "silver", "gold"]:
        dataset_ref = bigquery.Dataset(f"{GCP_PROJECT_ID}.{dataset_id}")
        dataset_ref.location = REGION
        try:
            bq_client.create_dataset(dataset_ref, exists_ok=True)
            print(f"BigQuery dataset ready: {dataset_id}")
        except Exception as e:
            print(f"Error creating dataset {dataset_id}: {e}")


def upload_bronze_to_gcs(local_data_dir: str = "./data"):
    """Uploads raw CSV files from local landing zone to GCS Bronze path."""
    storage_client = storage.Client(project=GCP_PROJECT_ID)
    bucket = storage_client.bucket(BUCKET_NAME)

    # Map the clean target name to your local filename format
    sources = {
        "customers": "customers(in).csv",
        "orders": "orders(in).csv",
        "products": "products(in).csv",
        "subscriptions": "subscriptions(in).csv",
    }

    for table_name, filename in sources.items():
        local_path = Path(local_data_dir) / filename
        if local_path.exists():
            # Upload to GCS as clean table name (e.g. bronze/customers.csv)
            blob_name = f"bronze/{table_name}.csv"
            blob = bucket.blob(blob_name)
            blob.upload_from_filename(str(local_path))
            print(f"Uploaded {filename} -> gs://{BUCKET_NAME}/{blob_name}")
        else:
            print(f"Warning: Local file {local_path} not found.")


def load_gcs_to_bigquery_tables():
    """
    Loads CSV files from GCS bronze paths into BigQuery bronze tables
    using an atomic Delete-Insert (Idempotent Overwrite) pattern on every load.
    """
    bq_client = bigquery.Client(project=GCP_PROJECT_ID)

    tables = ["customers", "orders", "products", "subscriptions"]

    for table_name in tables:
        table_id = f"{GCP_PROJECT_ID}.bronze.{table_name}"
        uri = f"gs://{BUCKET_NAME}/bronze/{table_name}.csv"
        staging_table_id = f"{GCP_PROJECT_ID}.bronze.stg_{table_name}"

        # 1. Load data into a staging table first
        staging_job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.CSV,
            skip_leading_rows=1,
            autodetect=True,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        )

        try:
            load_job = bq_client.load_table_from_uri(uri, staging_table_id, job_config=staging_job_config)
            load_job.result()  # Wait for staging load to complete

            # 2. Check if target table already exists
            table_ref = bq_client.dataset("bronze", project=GCP_PROJECT_ID).table(table_name)
            try:
                bq_client.get_table(table_ref)
                table_exists = True
            except Exception:
                table_exists = False

            if table_exists:
                # 3. Perform transactional Delete + Insert (Idempotent Overwrite)
                sql_delete_insert = f"""
                BEGIN TRANSACTION;
                DELETE FROM `{table_id}` WHERE TRUE;
                INSERT INTO `{table_id}`
                SELECT * FROM `{staging_table_id}`;
                COMMIT;
                """
                query_job = bq_client.query(sql_delete_insert)
                query_job.result()  # Wait for transaction commit
                print(f"Successfully performed Delete-Insert on BigQuery table {table_id}")
            else:
                # First-time initialization: create table directly from staging
                sql_create = f"CREATE TABLE `{table_id}` AS SELECT * FROM `{staging_table_id}`"
                bq_client.query(sql_create).result()
                print(f"Created and populated BigQuery table {table_id}")

            # 4. Clean up staging table
            bq_client.delete_table(staging_table_id, not_found_ok=True)

            destination_table = bq_client.get_table(table_id)
            print(f"Table {table_id} now holds {destination_table.num_rows} rows.")

        except Exception as e:
            print(f"Error during delete-insert load for table {table_name}: {e}")


if __name__ == "__main__":
    print(f"Initializing pipeline for project: {GCP_PROJECT_ID}")
    initialize_gcp_resources()
    # 1. Upload files from local path /data to GCS
    upload_bronze_to_gcs("./data")
    # 2. Load data into BigQuery tables via Delete-Insert pattern
    load_gcs_to_bigquery_tables()
