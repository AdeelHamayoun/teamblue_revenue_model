import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LOCAL_DATA_DIR = "../data"
LOCAL_PARQUET_DIR = "../temp_parquet"
OUTPUT_DIR = "../outputs"

# GCP Configuration
GCP_PROJECT_ID = "teambluerevenuemodel"
BUCKET_NAME = "teamblue_data_source"
REGION = "europe-west1"
RAW_BUCKET = os.environ.get("RAW_BUCKET", "")           # landing bucket (no gs:// prefix)
RAW_PREFIX = os.environ.get("RAW_PREFIX", "raw")        # folder inside the bucket


SOURCES = {
    "customers": "customers(in).csv",
    "orders": "orders(in).csv",
    "products": "products(in).csv",
    "subscriptions": "subscriptions(in).csv",
}

# BigQuery datasets
DATASETS = ("meta", "bronze", "silver", "gold", "analytics")

OPENING_MONTH = "2018-12-01"
REPORT_START = "2019-01-01"
REPORT_END = "2023-12-01"
AS_OF_DATE = "2024-05-09"  # latest date observed in source data (customer join_date)


# --------------------------------------------------------------------------
# Subscription status -> lifecycle group. Status is a *current* snapshot
# attribute; activity in a given month is driven by start/expire dates.
# --------------------------------------------------------------------------
STATUS_GROUPS = {
    None: "Standard",
    "AWAITING PAYMENT": "Payment issue",
    "UNPAID": "Payment issue",
    "PRE-WAIVED": "Pending",
    "TO VERIFY": "Pending",
    "OWNER CHANGE IN PROGRESS": "Pending",
    "SUSPENDED": "Terminated",
    "CANCELLED": "Terminated",
    "TO DELETE": "Terminated",
    "LOST": "Terminated",
    "MAINTENANCE CHANGED": "Transferred out",
    "OWNER CHANGED": "Owner changed",
    "TOWEPANEL": "Migrated",
}


# Extract-selection bias: a subscription only appears if one of its billing lines
# (expiring 2019-01..2023-12) was sampled. Recently started subscriptions have
# fewer eligible lines, so acquisitions after this month are under-represented.
ACQUISITION_COMPLETE_UNTIL = "2020-12-01"

# Prices at or below this are placeholder / promotional (0.01 EUR) lines.
FREE_PRICE_THRESHOLD = 0.01

# MRR movements smaller than this (EUR) are treated as flat (rounding noise).
MRR_EPSILON = 0.005


# DQ: an 'error' rule failing above its threshold stops the pipeline before
# gold is published (circuit breaker). 'warn' rules are logged only.
DQ_FAIL_ON_ERROR = True