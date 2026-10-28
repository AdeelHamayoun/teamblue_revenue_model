"""Thin BigQuery helpers: query execution and scalar reads."""
from google.cloud import bigquery

import config as C
from lib.sqlutil import qualify


def get_client() -> bigquery.Client:
    if not C.GCP_PROJECT_ID:
        raise SystemExit("GCP_PROJECT is not set. Run:  export GCP_PROJECT=teambluerevenuemodel")
    return bigquery.Client(project=C.GCP_PROJECT_ID, location=C.REGION)


def run(client, sql, params=None):
    """Run one statement or a multi-statement script and wait for it."""
    cfg = bigquery.QueryJobConfig(labels={"app": "teambluerevenuemodel"},
                                  query_parameters=params or [])
    job = client.query(qualify(sql), job_config=cfg)
    job.result()
    return job


def first_row(client, sql, params=None):
    cfg = bigquery.QueryJobConfig(labels={"app": "teambluerevenuemodel"},
                                  query_parameters=params or [])
    rows = list(client.query(qualify(sql), job_config=cfg).result())
    return tuple(rows[0].values()) if rows else None


def count(client, table, where=None):
    q = f"SELECT COUNT(*) FROM {table}" + (f" WHERE {where}" if where else "")
    return int(first_row(client, q)[0])


def table_exists(client, dataset, table) -> bool:
    from google.api_core.exceptions import NotFound
    try:
        client.get_table(f"{C.GCP_PROJECT_ID}.{dataset}.{table}")
        return True
    except NotFound:
        return False
