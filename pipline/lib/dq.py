"""
Data quality engine.

A rule is a SQL query returning exactly one row: (failed_rows, total_rows). Each rule has:
  * severity  : 'error' blocks publication of gold when the failure rate is
                above `threshold`; 'warn' is logged and monitored only.
  * threshold : tolerated failure *rate* (0 = zero tolerance).
  * action    : what the pipeline does with offending rows (documentation of
                the remediation, e.g. 'quarantine', 'map to unknown member').

Results are appended to meta.dq_results on every run, so DQ is trended over
time (a rule creeping from 0.1% to 2% is a signal even if it never breaches).
"""
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Rule:
    rule_id: str
    layer: str
    table: str
    description: str
    sql: str            # must return exactly one row: (failed, total)
    severity: str = "error"
    threshold: float = 0.0
    action: str = "block"


class DQBlocked(Exception):
    pass


def run_rules(ctx, rules, layer, fail_on_error=True):
    from lib.bq import first_row    # imported here so Rule/DQBlocked stay importable offline
    from lib.runlog import now
    blocking = []
    todo = [r for r in rules if r.layer == layer]
    print(f"  [dq    ] running {len(todo)} {layer} rules")
    try:
        for r in todo:
            failed, total = first_row(ctx.client, r.sql)
            failed, total = int(failed or 0), int(total or 0)
            rate = failed / total if total else 0.0
            passed = rate <= r.threshold
            ctx.add_dq({
                "run_id": ctx.run_id, "layer": layer, "rule_id": r.rule_id, "table_name": r.table,
                "description": r.description, "severity": r.severity, "action": r.action,
                "failed_rows": failed, "total_rows": total, "failure_rate": rate,
                "threshold": r.threshold, "passed": passed, "checked_at": now().isoformat()})
            flag = "PASS" if passed else ("FAIL" if r.severity == "error" else "WARN")
            if failed or not passed:
                print(f"           {flag:4} {r.rule_id:38} {failed:>10,}/{total:<10,} ({rate:.2%})  -> {r.action}")
            if not passed and r.severity == "error":
                blocking.append(r.rule_id)
    finally:
        ctx.flush()   # results are persisted even when a rule blocks the run
    if blocking and fail_on_error:
        raise DQBlocked(f"{layer}: blocking DQ rules failed: {blocking}")

