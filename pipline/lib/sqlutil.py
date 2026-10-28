"""Pure helpers (no Google libraries) so they can be unit-tested offline."""
import re
from datetime import date
from pathlib import Path

import config as C

_DS = "|".join(C.DATASETS)
# dataset.table  ->  `project.dataset.table`
# (not preceded by a word char, '.' or backtick, so `p.bronze`.INFORMATION_SCHEMA is untouched)
_REF = re.compile(rf"(?<![\w.`])({_DS})\.([A-Za-z_][A-Za-z0-9_]*)")


def qualify(sql: str) -> str:
    """SQL files are written as `silver.customers`; BigQuery views and scripts are
    safest with fully-qualified names, so we expand them here in one place."""
    sql = sql.replace("{{PROJECT}}", C.GCP_PROJECT_ID)
    return _REF.sub(lambda m: f"`{C.GCP_PROJECT_ID}.{m.group(1)}.{m.group(2)}`", sql)


def month(s: str) -> date:
    y, m = map(int, s[:7].split("-"))
    return date(y, m, 1)


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def render(path: Path, **params) -> str:
    """Fill {{PLACEHOLDERS}} from config. Money thresholds become exact NUMERIC literals."""
    sql = Path(path).read_text()
    base = dict(
        EPS=f"NUMERIC '{C.MRR_EPSILON}'",
        FREE_PRICE_THRESHOLD=f"NUMERIC '{C.FREE_PRICE_THRESHOLD}'",
        REPORT_START=C.REPORT_START, REPORT_END=C.REPORT_END, AS_OF_DATE=C.AS_OF_DATE,
        ACQUISITION_COMPLETE_UNTIL=C.ACQUISITION_COMPLETE_UNTIL)
    base.update(params)
    for k, v in base.items():
        sql = sql.replace("{{" + k + "}}", str(v))
    if "{{" in sql:
        raise ValueError(f"unresolved placeholder in {Path(path).name}: {sql[sql.index('{{'):][:40]}")
    return sql
