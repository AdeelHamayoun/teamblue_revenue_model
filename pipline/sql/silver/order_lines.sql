-- SILVER.ORDER_LINES
-- Grain: one row per distinct billing line (exact duplicates removed).
-- Business meaning (validated in profiling):
--   * ordercode   = the order / contract document; 'R'-prefixed codes are credit
--                   notes (refund documents) that reference the renewcode they refund
--   * renewcode   = the billing event (initial purchase or renewal)
--   * productprice= price for the WHOLE billing period (duration_months), not per month
--                   -> MRR contribution = productprice / duration_months
--   * quantity    = 0/1 only, and prices of qty=0 lines are as high as qty=1 lines;
--                   it does not behave like a multiplier -> NOT used in revenue
--                   (flagged in the memo as a question for the source owner)
--   * 0.01 EUR    = placeholder price for free / bundled / promotional periods
CREATE OR REPLACE TABLE silver.order_lines_all AS
WITH typed AS (
    SELECT
        TRIM(client_id)                                              AS client_id,
        TRIM(ordercode)                                              AS order_code,
        TRIM(renewcode)                                              AS renew_code,
        SAFE_CAST(TRIM(orderdate) AS DATE)                           AS order_date,
        SAFE_CAST(TRIM(expiredate) AS DATE)                          AS period_end_date,
        SAFE_CAST(NULLIF(TRIM(subscription_id), 'NULL') AS INT64)    AS subscription_id,
        SAFE_CAST(TRIM(product_id) AS INT64)                         AS product_id,
        SAFE_CAST(TRIM(quantity) AS INT64)                           AS quantity_raw,
        ROUND(SAFE_CAST(TRIM(productprice) AS NUMERIC), 4)           AS price,
        UPPER(TRIM(currency))                                        AS currency,
        SAFE_CAST(SAFE_CAST(TRIM(duration_months) AS FLOAT64) AS INT64) AS duration_months,
        SAFE_CAST(TRIM(flag_recurring) AS INT64) = 1                 AS flag_recurring,
        _row_hash,
        _ingested_at
    FROM bronze.orders
),
dedup AS (
    -- exact duplicates: 10,011 rows in the current extract (identical in every column)
    SELECT *, COUNT(*) OVER (PARTITION BY _row_hash) AS duplicate_count
    FROM typed
    WHERE TRUE
    QUALIFY ROW_NUMBER() OVER (PARTITION BY _row_hash ORDER BY _ingested_at) = 1
),
classified AS (
    SELECT
        *,
        CASE
            WHEN order_code LIKE 'R%'                           THEN 'refund_document'
            WHEN price < 0                                      THEN 'credit'
            WHEN NOT flag_recurring OR duration_months = 0      THEN 'one_off'
            ELSE 'recurring'
        END                                                     AS line_type,
        price BETWEEN 0 AND {{FREE_PRICE_THRESHOLD}}            AS is_free_placeholder
    FROM dedup
),
refund_net AS (
    -- net of all credit notes / credits / reversals against a billing event
    SELECT client_id, renew_code, product_id, SUM(price) AS refunded_amount
    FROM classified
    WHERE line_type IN ('refund_document', 'credit')
    GROUP BY client_id, renew_code, product_id
)
SELECT
    c._row_hash                                              AS order_line_key,
    c.client_id, c.order_code, c.renew_code, c.order_date,
    c.period_end_date, c.subscription_id, c.product_id, c.quantity_raw,
    c.price, c.currency, c.duration_months, c.flag_recurring,
    c.line_type, c.is_free_placeholder,
    COALESCE(r.refunded_amount, 0)                           AS refunded_amount,
    -- a recurring billing period that was (fully) credited back is void for MRR
    -- (only priced lines can be voided: a 0.01 placeholder is free, not refunded)
    (c.line_type = 'recurring' AND c.price > {{FREE_PRICE_THRESHOLD}}
        AND c.price + COALESCE(r.refunded_amount, 0) <= {{FREE_PRICE_THRESHOLD}}) AS is_voided,
    CASE WHEN c.line_type = 'recurring' AND NOT c.is_free_placeholder
         THEN SAFE_DIVIDE(c.price, c.duration_months) ELSE 0 END AS mrr_amount,
    DATE_TRUNC(c.order_date, MONTH)                          AS order_month,
    c.duplicate_count,
    -- quarantine reasons (NULL = clean)
    CASE
        WHEN c.order_date IS NULL OR c.price IS NULL              THEN 'unparseable_field'
        WHEN c.order_date > DATE '{{AS_OF_DATE}}'                 THEN 'order_date_in_future'
        WHEN c.line_type = 'recurring'
             AND c.period_end_date <= c.order_date                THEN 'invalid_billing_period'
        WHEN c.currency <> 'EUR'                                  THEN 'unsupported_currency'
    END                                                      AS reject_reason,
    c._ingested_at
FROM classified c
LEFT JOIN refund_net r
       ON r.client_id = c.client_id AND r.renew_code = c.renew_code
      AND r.product_id = c.product_id
      AND c.line_type = 'recurring';

CREATE OR REPLACE TABLE silver.order_lines AS
SELECT * EXCEPT (reject_reason) FROM silver.order_lines_all WHERE reject_reason IS NULL;

CREATE OR REPLACE TABLE silver.quarantine_order_lines AS
SELECT * FROM silver.order_lines_all WHERE reject_reason IS NOT NULL;
