"""
ReconcileAI – Report Generator
Produces reconciliation reports as Excel files and summary statistics.
All numbers come from the database — LLM only generates the management summary text.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime
import json

import pandas as pd

from database.repositories import (
    get_all_transactions, get_all_invoices, get_all_review_cases,
    get_audit_logs, get_reconciliation_summary, get_all_remittances,
)


def export_reconciliation_report(output_path: str | None = None) -> str:
    """
    Export a full reconciliation report as an Excel workbook.
    Returns the path to the created file.
    """
    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(Path("data/processed") / f"reconciliation_report_{ts}.xlsx")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    transactions = get_all_transactions()
    invoices     = get_all_invoices()
    review_cases = get_all_review_cases()
    audit_logs   = get_audit_logs(limit=500)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        # Sheet 1: Transactions
        df_txn = pd.DataFrame(transactions)
        _write_sheet(writer, df_txn, "Transactions")

        # Sheet 2: Invoices
        df_inv = pd.DataFrame(invoices)
        _write_sheet(writer, df_inv, "Invoices")

        # Sheet 3: Review Cases
        df_rev = pd.DataFrame(review_cases)
        _write_sheet(writer, df_rev, "Review Cases")

        # Sheet 4: Audit Log
        df_audit = pd.DataFrame(audit_logs)
        _write_sheet(writer, df_audit, "Audit Log")

        # Sheet 5: Summary
        summary = get_reconciliation_summary()
        df_summary = pd.DataFrame([summary])
        _write_sheet(writer, df_summary, "Summary")

    return output_path


def _write_sheet(writer, df: pd.DataFrame, sheet_name: str) -> None:
    if df.empty:
        df = pd.DataFrame({"No data": []})
    df.to_excel(writer, sheet_name=sheet_name, index=False)
    ws = writer.sheets[sheet_name]
    # Auto-width columns
    for col in ws.columns:
        max_len = max((len(str(cell.value or "")) for cell in col), default=0)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 50)


def get_transaction_history(transaction_id: str) -> dict:
    """Return the complete audit history for a transaction."""
    logs = get_audit_logs(entity_id=transaction_id)
    return {"transaction_id": transaction_id, "audit_trail": logs}


def answer_finance_query(query: str) -> dict:
    """
    Route natural-language queries to controlled backend functions.
    Uses function calling pattern — LLM selects function, Python executes it.
    """
    from extraction.llm_extractor import answer_finance_query as llm_answer

    # Determine which backend function to call
    func_map = {
        "get_unmatched_transactions":    get_all_transactions,
        "get_partially_paid_invoices":   lambda: [i for i in get_all_invoices() if i["status"] == "partially_paid"],
        "get_open_review_cases":         get_all_review_cases,
        "get_reconciliation_summary":    get_reconciliation_summary,
        "get_all_invoices":              get_all_invoices,
        "get_all_transactions":          get_all_transactions,
    }

    # Simple keyword routing (replace with function-calling LLM for production)
    q_lower = query.lower()
    if "unmatched" in q_lower:
        backend_data = {"function": "get_unmatched_transactions",
                        "result": [t for t in get_all_transactions() if t["status"] == "unmatched"]}
    elif "partial" in q_lower:
        backend_data = {"function": "get_partially_paid_invoices",
                        "result": [i for i in get_all_invoices() if i["status"] == "partially_paid"]}
    elif "review" in q_lower or "exception" in q_lower:
        backend_data = {"function": "get_open_review_cases",
                        "result": get_all_review_cases()}
    elif "summary" in q_lower or "total" in q_lower or "how many" in q_lower:
        backend_data = {"function": "get_reconciliation_summary",
                        "result": get_reconciliation_summary()}
    elif "tds" in q_lower:
        rem = get_all_remittances()
        tds_records = [r for r in rem if float(r.get("tds_amount") or 0) > 0]
        backend_data = {"function": "get_tds_remittances", "result": tds_records}
    else:
        backend_data = {"function": "get_reconciliation_summary",
                        "result": get_reconciliation_summary()}

    try:
        answer = llm_answer(query, backend_data)
    except Exception:
        # If LLM unavailable, return raw data
        answer = f"Query: {query}\n\nData: {json.dumps(backend_data['result'], indent=2, default=str)}"

    return {"query": query, "backend_function": backend_data["function"],
            "answer": answer, "raw_data": backend_data["result"]}
