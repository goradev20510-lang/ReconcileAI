"""
ReconcileAI – Dashboard Data Layer
Aggregates KPI data for the Streamlit dashboard.
All numbers come from the database — not the LLM.
"""
from __future__ import annotations
from datetime import date

from database.repositories import get_reconciliation_summary, get_all_transactions


def get_dashboard_kpis() -> dict:
    """Return all KPIs needed by the dashboard in one call."""
    s = get_reconciliation_summary()
    total = max(s["total_transactions"], 1)
    return {
        **s,
        "total_invoice_value":      s["invoice_value"],
        "total_outstanding":        s["outstanding_value"],
        "total_received":           s["amount_received"],
        "auto_reconciliation_rate": s["matched"] / total,
        "value_match_rate":         s["amount_matched"] / max(s["amount_received"], 1),
    }


def get_recent_transactions(n: int = 10) -> list[dict]:
    return list(reversed(get_all_transactions()[-n:]))


def get_aging_buckets(as_of: date | None = None) -> dict[str, float]:
    from reporting.report_generator import aging_report, AGING_BUCKETS
    df = aging_report(as_of)
    out = {b: 0.0 for b in AGING_BUCKETS}
    if not df.empty:
        for b, v in df.groupby("bucket")["outstanding_amount"].sum().items():
            out[b] = round(float(v), 2)
    return out
