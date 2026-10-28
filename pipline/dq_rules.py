from lib.dq import Rule
from config import REPORT_END, REPORT_START

R = []
PRICE_FREE = "NUMERIC '0.01'"
# ---------------------------------------------------------------- BRONZE ----
for src, min_rows in [("customers", 1000), ("orders", 1000), ("subscriptions", 1000), ("products", 100)]:
    R.append(Rule(f"bronze_{src}_not_empty", "bronze", f"bronze.{src}",
                  f"{src} snapshot has at least {min_rows} rows",
                  f"SELECT CAST(COUNT(*) < {min_rows} AS INT64), 1 FROM bronze.{src}"))

R.append(Rule("bronze_orders_schema", "bronze", "bronze.orders",
              "orders has all 12 expected columns",
              """SELECT 12 - COUNT(*), 12
                 FROM `{{PROJECT}}.bronze`.INFORMATION_SCHEMA.COLUMNS
                 WHERE table_name = 'orders' AND column_name IN
                 ('client_id','ordercode','renewcode','orderdate','expiredate','subscription_id',
                  'product_id','quantity','productprice','currency','duration_months','flag_recurring')"""))

R.append(Rule("bronze_single_latest_batch", "bronze", "bronze.orders",
              "the latest-batch views return exactly one batch per source",
              """SELECT 4 - COUNT(*), 4 FROM (
                   SELECT 'customers' AS s, COUNT(DISTINCT batch_id) AS n FROM bronze.customers UNION ALL
                   SELECT 'orders', COUNT(DISTINCT batch_id) FROM bronze.orders UNION ALL
                   SELECT 'subscriptions', COUNT(DISTINCT batch_id) FROM bronze.subscriptions UNION ALL
                   SELECT 'products', COUNT(DISTINCT batch_id) FROM bronze.products
                 ) WHERE n = 1"""))

# ---------------------------------------------------------------- SILVER ----
def unique(rule_id, table, key):
    return Rule(rule_id, "silver", table, f"{key} is unique in {table}",
                f"SELECT COUNT(*) - COUNT(DISTINCT {key}), COUNT(*) FROM {table}")

R += [
    unique("silver_customers_pk_unique", "silver.customers", "client_id"),
    unique("silver_products_pk_unique", "silver.products", "product_id"),
    unique("silver_subscriptions_pk_unique", "silver.subscriptions", "subscription_id"),
    unique("silver_order_lines_pk_unique", "silver.order_lines", "order_line_key"),

    Rule("silver_orders_exact_duplicates", "silver", "bronze.orders",
         "exact duplicate order rows in bronze (removed in silver)",
         "SELECT COUNT(*) - COUNT(DISTINCT _row_hash), COUNT(*) FROM bronze.orders",
         severity="warn", threshold=0.10, action="deduplicate (keep 1)"),
    Rule("silver_row_conservation", "silver", "silver.order_lines",
         "bronze rows = silver + quarantined + removed duplicates",
         """SELECT ABS((SELECT COUNT(*) FROM bronze.orders)
                - (SELECT COUNT(*) FROM silver.order_lines_all)
                - (SELECT SUM(duplicate_count - 1) FROM silver.order_lines_all)), 1"""),
    Rule("silver_orders_quarantined", "silver", "silver.quarantine_order_lines",
         "order lines quarantined (future date / invalid period / unparseable)",
         "SELECT (SELECT COUNT(*) FROM silver.quarantine_order_lines), (SELECT COUNT(*) FROM silver.order_lines_all)",
         severity="warn", threshold=0.01, action="quarantine"),
    Rule("silver_orders_not_null_keys", "silver", "silver.order_lines",
         "client_id, product_id, order_date, price are populated",
         """SELECT COUNTIF(client_id IS NULL OR product_id IS NULL
                           OR order_date IS NULL OR price IS NULL), COUNT(*) FROM silver.order_lines"""),
    Rule("silver_orders_currency_eur", "silver", "silver.order_lines",
         "single currency (EUR); multi-currency would need FX conversion",
         "SELECT COUNTIF(currency <> 'EUR'), COUNT(*) FROM silver.order_lines"),
    Rule("silver_orders_recurring_duration", "silver", "silver.order_lines",
         "recurring lines have a positive duration (MRR denominator)",
         "SELECT COUNTIF(line_type = 'recurring' AND COALESCE(duration_months, 0) <= 0), COUNT(*) FROM silver.order_lines"),
    Rule("silver_orders_fk_customer", "silver", "silver.order_lines",
         "every order line's client exists in customers",
         """SELECT COUNTIF(c.client_id IS NULL), COUNT(*)
            FROM silver.order_lines o LEFT JOIN silver.customers c ON c.client_id = o.client_id"""),
    Rule("silver_orders_fk_product", "silver", "silver.order_lines",
         "every order line's product exists in products",
         """SELECT COUNTIF(p.product_id IS NULL), COUNT(*)
            FROM silver.order_lines o LEFT JOIN silver.products p ON p.product_id = o.product_id""",
         severity="warn", threshold=0.001, action="map to 'Unknown' member in dim_product"),
    Rule("silver_orders_fk_subscription", "silver", "silver.order_lines",
         "subscription_id (when present) exists in subscription master",
         """SELECT COUNTIF(o.subscription_id IS NOT NULL AND s.subscription_id IS NULL),
                   COUNTIF(o.subscription_id IS NOT NULL)
            FROM silver.order_lines o LEFT JOIN silver.subscriptions s ON s.subscription_id = o.subscription_id""",
         severity="warn", threshold=0.001, action="treat billing period as lifecycle ('order_period')"),
    Rule("silver_orders_missing_subscription", "silver", "silver.order_lines",
         "recurring paid lines without subscription_id",
         f"SELECT COUNTIF(subscription_id IS NULL), COUNT(*) FROM silver.order_lines "
         f"WHERE line_type = 'recurring' AND price > {PRICE_FREE}",
         severity="warn", threshold=0.20, action="treat billing period as lifecycle ('order_period')"),
    Rule("silver_orders_price_placeholder", "silver", "silver.order_lines",
         "0.01 EUR placeholder prices (free / bundled periods)",
         "SELECT COUNTIF(is_free_placeholder), COUNT(*) FROM silver.order_lines",
         severity="warn", threshold=0.40, action="MRR = 0 for that period"),
    Rule("silver_orders_quantity_unreliable", "silver", "silver.order_lines",
         "quantity = 0 on priced lines (quantity not used as multiplier)",
         f"SELECT COUNTIF(quantity_raw = 0 AND price > {PRICE_FREE}), COUNT(*) FROM silver.order_lines "
         f"WHERE price > {PRICE_FREE}",
         severity="warn", threshold=0.50, action="ignore quantity; escalate to source owner"),
    Rule("silver_orders_refunds", "silver", "silver.order_lines",
         "credit notes / negative lines",
         "SELECT COUNTIF(line_type IN ('refund_document', 'credit')), COUNT(*) FROM silver.order_lines",
         severity="warn", threshold=0.02, action="net against billing event; void fully refunded periods"),
    Rule("silver_orders_void_only_priced", "silver", "silver.order_lines",
         "regression: free placeholder lines are never marked voided",
         "SELECT COUNTIF(is_voided AND is_free_placeholder), COUNT(*) FROM silver.order_lines"),
    Rule("silver_customers_country_known", "silver", "silver.customers",
         "country populated",
         "SELECT COUNTIF(country_code = 'ZZ'), COUNT(*) FROM silver.customers",
         severity="warn", threshold=0.02, action="map to 'ZZ' (unknown)"),
    Rule("silver_customers_malformed_id", "silver", "silver.customers",
         "client_id matches expected pattern",
         "SELECT COUNTIF(has_malformed_id), COUNT(*) FROM silver.customers",
         severity="warn", threshold=0.01, action="flag only (key is consistent across sources)"),
    Rule("silver_subscriptions_valid_period", "silver", "silver.subscriptions",
         "expire_date >= start_date",
         "SELECT COUNTIF(has_invalid_period), COUNT(*) FROM silver.subscriptions",
         severity="warn", threshold=0.001, action="quarantine"),
    Rule("silver_subscriptions_status_mapped", "silver", "silver.subscriptions",
         "every status maps to a lifecycle group (new statuses must be classified)",
         "SELECT COUNTIF(status_group = 'Unmapped'), COUNT(*) FROM silver.subscriptions"),
    Rule("silver_products_category_domain", "silver", "silver.products",
         "product_category in the accepted set after cleaning",
         """SELECT COUNTIF(product_category NOT IN ('Domains','Hosting','Applications','Other')
                           OR product_category IS NULL), COUNT(*) FROM silver.products"""),
]


# ------------------------------------------------------------------ GOLD ----
W = f"month BETWEEN DATE '{REPORT_START}' AND DATE '{REPORT_END}'"
R += [
    Rule("gold_sub_month_grain", "gold", "gold.fct_subscription_month",
         "one row per subscription_key x month",
         """SELECT COUNT(*) - COUNT(DISTINCT CONCAT(subscription_key, '|', CAST(month AS STRING))), COUNT(*)
            FROM gold.fct_subscription_month"""),
    Rule("gold_cust_month_grain", "gold", "gold.fct_customer_month",
         "one row per client_id x month",
         """SELECT COUNT(*) - COUNT(DISTINCT CONCAT(client_id, '|', CAST(month AS STRING))), COUNT(*)
            FROM gold.fct_customer_month"""),
    Rule("gold_cust_cat_month_grain", "gold", "gold.fct_customer_category_month",
         "one row per client_id x category x month",
         """SELECT COUNT(*) - COUNT(DISTINCT CONCAT(client_id, '|', product_category, '|', CAST(month AS STRING))), COUNT(*)
            FROM gold.fct_customer_category_month"""),
    Rule("gold_mrr_non_negative", "gold", "gold.fct_subscription_month",
         "MRR is never negative",
         "SELECT COUNTIF(mrr < 0), COUNT(*) FROM gold.fct_subscription_month"),
    Rule("gold_mrr_reconciles_sub_to_customer", "gold", "gold.fct_customer_month",
         "sum of subscription MRR = sum of customer MRR, every month",
         f"""SELECT COUNTIF(ABS(a.m - COALESCE(b.m, 0)) > 0.01), COUNT(*) FROM
               (SELECT month, SUM(mrr) AS m FROM gold.fct_subscription_month WHERE {W} GROUP BY month) a
               LEFT JOIN (SELECT month, SUM(mrr) AS m FROM gold.fct_customer_month WHERE {W} GROUP BY month) b
                 ON b.month = a.month"""),
    Rule("gold_mrr_reconciles_customer_to_category", "gold", "gold.fct_customer_category_month",
         "sum of customer-category MRR = sum of customer MRR, every month",
         f"""SELECT COUNTIF(ABS(a.m - COALESCE(b.m, 0)) > 0.01), COUNT(*) FROM
               (SELECT month, SUM(mrr) AS m FROM gold.fct_customer_month WHERE {W} GROUP BY month) a
               LEFT JOIN (SELECT month, SUM(mrr) AS m FROM gold.fct_customer_category_month WHERE {W} GROUP BY month) b
                 ON b.month = a.month"""),
    Rule("gold_bridge_balances", "gold", "gold.mart_mrr_bridge_monthly",
         "opening + net new = closing (MRR bridge balances)",
         """SELECT COUNTIF(ABS(opening_mrr + net_new_mrr - closing_mrr) > 0.01), COUNT(*)
            FROM gold.mart_mrr_bridge_monthly"""),
    Rule("gold_bridge_continuity", "gold", "gold.mart_mrr_bridge_monthly",
         "closing MRR of month M = opening MRR of month M+1",
         """SELECT COUNTIF(ABS(closing_mrr - lead_open) > 0.01), COUNT(*) FROM (
              SELECT closing_mrr, LEAD(opening_mrr) OVER (PARTITION BY scope ORDER BY month) AS lead_open
              FROM gold.mart_mrr_bridge_monthly) t WHERE lead_open IS NOT NULL"""),
    Rule("gold_window_complete", "gold", "gold.fct_customer_month",
         "every month in the reporting window has data",
         f"""SELECT (DATE_DIFF(DATE '{REPORT_END}', DATE '{REPORT_START}', MONTH) + 1)
                    - COUNT(DISTINCT month), 1 FROM gold.fct_customer_month WHERE {W}"""),
    Rule("gold_imputed_mrr_share", "gold", "gold.mart_kpi_monthly",
         "months where >60% of MRR is priced from imputed (carried/backfilled) lines",
         "SELECT COUNTIF(imputed_mrr_share > 0.60), COUNT(*) FROM gold.mart_kpi_monthly WHERE scope = 'All'",
         severity="warn", threshold=0.0, action="monitor: sample-based pricing"),
    Rule("gold_mom_mrr_anomaly", "gold", "gold.mart_kpi_monthly",
         "MoM MRR change beyond +/-15% (anomaly detection)",
         "SELECT COUNTIF(ABS(mrr_growth_mom) > 0.15), COUNT(*) FROM gold.mart_kpi_monthly WHERE scope = 'All'",
         severity="warn", threshold=0.0, action="alert analyst"),
]

RULES = R

