"""
ReconcileAI – Dashboard Data Layer
Aggregates KPI data for the Streamlit dashboard.
All numbers come from the database — not the LLM.
"""
from __future__ import annotations
from database.repositories import get_reconciliation_summary, get_all_transactions, get_all_invoices


def get_dashboard_kpis() -> dict:
    """Return all KPIs needed by the dashboard in one call."""
    summary  = get_reconciliation_summary()
    txns     = get_all_transactions()
    invoices = get_all_invoices()

    total_inv_value  = sum(float(inv.get("total_amount", 0)) for inv in invoices)
    total_outstanding = sum(float(inv.get("outstanding_amount", 0)) for inv in invoices)
    total_received   = sum(float(t.get("credit_amount", 0)) for t in txns)
    auto_rate        = summary["matched"] / max(summary["total_transactions"], 1)

    return {
        **summary,
        "total_invoice_value":   total_inv_value,
        "total_outstanding":     total_outstanding,
        "total_received":        total_received,
        "auto_reconciliation_rate": auto_rate,
    }
