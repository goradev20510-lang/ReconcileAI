"""
ReconcileAI – Report Generator
Aging, customer balances, TDS register, Excel export and the Finance Assistant.
All numbers come from the database — the LLM only phrases answers and summaries.
"""
from __future__ import annotations
import io
import json
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from config.settings import DATA_PROCESSED_DIR
from database.repositories import (
    get_all_transactions, get_all_invoices, get_all_review_cases, get_open_review_cases,
    get_audit_logs, get_reconciliation_summary, get_all_remittances, get_all_allocations,
    get_all_settlement_components, get_all_customers, get_tds_components, get_invoice,
    get_transaction, get_allocations_for_transaction,
)

AGING_BUCKETS = ["Not due", "1–30", "31–60", "61–90", "90+"]


# ── Aging & balances ──────────────────────────────────────────────────────────

def _as_date(v) -> date | None:
    if not v:
        return None
    try:
        from dateutil import parser as dp
        return dp.parse(str(v)).date()
    except Exception:
        return None


def _bucket(days_past_due: int | None) -> str:
    if days_past_due is None or days_past_due <= 0:
        return "Not due"
    if days_past_due <= 30:
        return "1–30"
    if days_past_due <= 60:
        return "31–60"
    if days_past_due <= 90:
        return "61–90"
    return "90+"


def aging_report(as_of: date | None = None) -> pd.DataFrame:
    """One row per invoice with money still owed, bucketed by days past due."""
    as_of = as_of or date.today()
    rows = []
    for inv in get_all_invoices():
        if inv["status"] not in ("open", "partially_paid") or float(inv["outstanding_amount"] or 0) <= 0.01:
            continue
        due = _as_date(inv.get("due_date"))
        dpd = (as_of - due).days if due else None
        rows.append({
            "invoice_id": inv["invoice_id"], "customer_name": inv.get("customer_name"),
            "invoice_date": inv.get("invoice_date"), "due_date": inv.get("due_date"),
            "total_amount": float(inv["total_amount"]), "outstanding_amount": float(inv["outstanding_amount"]),
            "days_past_due": dpd if dpd is not None else 0, "bucket": _bucket(dpd), "status": inv["status"],
        })
    df = pd.DataFrame(rows, columns=["invoice_id", "customer_name", "invoice_date", "due_date", "total_amount",
                                     "outstanding_amount", "days_past_due", "bucket", "status"])
    return df.sort_values(["days_past_due", "outstanding_amount"], ascending=[False, False]) if not df.empty else df


def aging_summary(as_of: date | None = None) -> pd.DataFrame:
    """Customer × bucket pivot of outstanding amounts."""
    df = aging_report(as_of)
    if df.empty:
        return pd.DataFrame(columns=["customer_name", *AGING_BUCKETS, "Total"])
    pv = df.pivot_table(index="customer_name", columns="bucket", values="outstanding_amount",
                        aggfunc="sum", fill_value=0.0)
    for b in AGING_BUCKETS:
        if b not in pv.columns:
            pv[b] = 0.0
    pv = pv[AGING_BUCKETS]
    pv["Total"] = pv.sum(axis=1)
    return pv.sort_values("Total", ascending=False).reset_index()


def customer_balances() -> pd.DataFrame:
    """Invoiced vs received vs outstanding per customer."""
    invoices = get_all_invoices()
    approved = [a for a in get_all_allocations("approved")]
    paid_by_invoice: dict[str, float] = {}
    for a in approved:
        paid_by_invoice[a["invoice_id"]] = paid_by_invoice.get(a["invoice_id"], 0.0) + float(a["allocated_amount"])
    by_cust: dict[str, dict] = {}
    for inv in invoices:
        name = inv.get("customer_name") or "Unknown"
        c = by_cust.setdefault(name, {"customer_name": name, "invoices": 0, "invoiced": 0.0, "received": 0.0,
                                      "outstanding": 0.0, "open_invoices": 0, "overpaid_by": 0.0})
        c["invoices"] += 1
        c["invoiced"] += float(inv["total_amount"])
        paid = paid_by_invoice.get(inv["invoice_id"], 0.0)
        c["received"] += paid
        if inv["status"] in ("open", "partially_paid"):
            c["outstanding"] += float(inv["outstanding_amount"])
            c["open_invoices"] += 1
        if paid > float(inv["total_amount"]):
            c["overpaid_by"] += paid - float(inv["total_amount"])
    df = pd.DataFrame(list(by_cust.values()))
    if df.empty:
        return pd.DataFrame(columns=["customer_name", "invoices", "invoiced", "received", "outstanding",
                                     "open_invoices", "overpaid_by"])
    return df.round(2).sort_values("outstanding", ascending=False)


def tds_register() -> pd.DataFrame:
    rows = get_tds_components()
    cols = ["component_id", "invoice_id", "customer_name", "transaction_id", "amount", "tds_section",
            "tds_status", "invoice_total", "supporting_doc", "notes", "created_at"]
    return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)


def transactions_with_allocations() -> pd.DataFrame:
    allocs = get_all_allocations()
    by_txn: dict[str, list[str]] = {}
    for a in allocs:
        if a["allocation_status"] in ("approved", "proposed"):
            by_txn.setdefault(a["transaction_id"], []).append(
                f"{a['invoice_id']} ({'✓' if a['allocation_status'] == 'approved' else '?'})")
    rows = []
    for t in get_all_transactions():
        rows.append({**t, "allocated_to": ", ".join(by_txn.get(t["transaction_id"], []))})
    return pd.DataFrame(rows)


# ── Excel export ──────────────────────────────────────────────────────────────

_MONEY_COLS = {"credit_amount", "debit_amount", "total_amount", "outstanding_amount", "taxable_amount",
               "gst_amount", "allocated_amount", "amount", "payment_amount", "tds_amount", "invoiced",
               "received", "outstanding", "overpaid_by", "invoice_total", *AGING_BUCKETS, "Total"}


def build_report_workbook(as_of: date | None = None) -> bytes:
    """Build the full reconciliation workbook in memory and return its bytes."""
    summary = get_reconciliation_summary()
    total = summary["total_transactions"] or 1
    summary_rows = [
        ("Generated at", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("Bank credits", summary["total_transactions"]),
        ("Auto/approved matched", summary["matched"]),
        ("Needs review", summary["needs_review"]),
        ("Unmatched", summary["unmatched"]),
        ("Duplicates", summary["duplicates"]),
        ("Excluded (debits / non-receivable)", summary["excluded"]),
        ("Match rate", f"{summary['matched'] / total:.1%}"),
        ("Amount received (₹)", summary["amount_received"]),
        ("Amount matched (₹)", summary["amount_matched"]),
        ("Amount in review (₹)", summary["amount_in_review"]),
        ("Amount unmatched (₹)", summary["amount_unmatched"]),
        ("Invoices", summary["invoices_total"]),
        ("Invoices open / partial / settled / overpaid",
         f"{summary['invoices_open']} / {summary['invoices_partial']} / {summary['invoices_settled']} / "
         f"{summary['invoices_overpaid']}"),
        ("Outstanding receivables (₹)", summary["outstanding_value"]),
        ("TDS awaiting certificate (₹)", summary["tds_pending"]),
        ("Open review cases", summary["open_review_cases"]),
    ]
    sheets = {
        "Summary":        pd.DataFrame(summary_rows, columns=["Metric", "Value"]),
        "Transactions":   transactions_with_allocations(),
        "Invoices":       pd.DataFrame(get_all_invoices()),
        "Allocations":    pd.DataFrame(get_all_allocations()),
        "Settlement":     pd.DataFrame(get_all_settlement_components()),
        "Review Cases":   pd.DataFrame(get_all_review_cases()),
        "Aging":          aging_report(as_of),
        "Aging Summary":  aging_summary(as_of),
        "Customers":      customer_balances(),
        "TDS Register":   tds_register(),
        "Remittances":    pd.DataFrame(get_all_remittances()),
        "Audit Log":      pd.DataFrame(get_audit_logs(limit=5000)),
    }
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, df in sheets.items():
            _write_sheet(writer, df, name)
    return buf.getvalue()


def export_reconciliation_report(output_path: str | None = None) -> str:
    """Write the workbook to disk (data/processed by default) and return its path."""
    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(DATA_PROCESSED_DIR / f"reconciliation_report_{ts}.xlsx")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_bytes(build_report_workbook())
    return output_path


def _write_sheet(writer, df: pd.DataFrame, sheet_name: str) -> None:
    from openpyxl.styles import Font, PatternFill
    if df is None or df.empty:
        df = pd.DataFrame({"No data": []})
    df.to_excel(writer, sheet_name=sheet_name, index=False)
    ws = writer.sheets[sheet_name]
    header_fill = PatternFill("solid", fgColor="1F3A68")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
    ws.freeze_panes = "A2"
    for idx, col in enumerate(df.columns, start=1):
        letter = ws.cell(row=1, column=idx).column_letter
        values = [str(col)] + [str(v) for v in df[col].head(500).tolist()]
        ws.column_dimensions[letter].width = min(max(len(v) for v in values) + 3, 60)
        if col in _MONEY_COLS:
            for row in ws.iter_rows(min_row=2, min_col=idx, max_col=idx):
                for cell in row:
                    if isinstance(cell.value, (int, float)):
                        cell.number_format = "#,##0.00"
    if sheet_name == "Summary":
        for row in ws.iter_rows(min_row=2, min_col=2, max_col=2):
            for cell in row:
                if isinstance(cell.value, float):
                    cell.number_format = "#,##0.00"


def get_transaction_history(transaction_id: str) -> dict:
    """Return the complete audit history for a transaction."""
    return {"transaction_id": transaction_id, "audit_trail": get_audit_logs(entity_id=transaction_id)}


# ── Finance Assistant ─────────────────────────────────────────────────────────

def _inr(v: float) -> str:
    return f"₹{float(v):,.2f}"


def _route(query: str) -> dict:
    """Pick a backend function from the question. Python fetches the data; nothing is invented."""
    q = query.lower()
    inv_ref = re.search(r"\binv[\s\-_/]?\d{2,8}\b", query, re.IGNORECASE)
    txn_ref = re.search(r"\btxn[\s\-_]?[0-9a-f]{3,12}\b", query, re.IGNORECASE)

    if inv_ref:
        from matching.exact_match import find_exact_invoice_match
        hits = find_exact_invoice_match(inv_ref.group(0), get_all_invoices())
        if hits:
            inv = hits[0]
            allocs = [a for a in get_all_allocations() if a["invoice_id"] == inv["invoice_id"]]
            return {"function": "get_invoice", "result": {"invoice": inv, "allocations": allocs}}
        return {"function": "get_invoice", "result": None, "note": f"No invoice {inv_ref.group(0)}"}
    if txn_ref:
        tid = txn_ref.group(0).upper().replace(" ", "")
        txn = get_transaction(tid) or get_transaction(tid.replace("TXN", "TXN-"))
        if txn:
            return {"function": "get_transaction",
                    "result": {"transaction": txn,
                               "allocations": get_allocations_for_transaction(txn["transaction_id"])}}
    if "unmatched" in q or "unidentified" in q or "unknown" in q:
        return {"function": "get_unmatched_transactions", "result": get_all_transactions("unmatched")}
    if "duplicate" in q:
        return {"function": "get_duplicate_transactions", "result": get_all_transactions("duplicate")}
    if "review" in q or "exception" in q or ("pending" in q and "tds" not in q):
        return {"function": "get_open_review_cases", "result": get_open_review_cases()}
    if "tds" in q:
        return {"function": "get_tds_register", "result": tds_register().to_dict("records")}
    if "partial" in q:
        return {"function": "get_partially_paid_invoices", "result": get_all_invoices("partially_paid")}
    if any(w in q for w in ("overdue", "aging", "ageing", "past due", "late")):
        return {"function": "get_aging", "result": aging_report().to_dict("records")}
    if any(w in q for w in ("customer", "owe", "owes", "balance", "who")):
        # customer named in the question?
        from matching.fuzzy_match import fuzzy_match_customer
        for cust in fuzzy_match_customer(query, get_all_customers(), threshold=80)[:1]:
            invs = [i for i in get_all_invoices() if i.get("customer_id") == cust["customer_id"]]
            return {"function": "get_customer_invoices", "result": {"customer": cust["name"], "invoices": invs}}
        return {"function": "get_customer_balances", "result": customer_balances().to_dict("records")}
    if "settled" in q or "paid" in q:
        return {"function": "get_settled_invoices", "result": get_all_invoices("settled")}
    return {"function": "get_reconciliation_summary", "result": get_reconciliation_summary()}


def _offline_answer(query: str, backend: dict) -> str:
    """Deterministic plain-language rendering of backend data (no LLM)."""
    fn, res = backend["function"], backend.get("result")
    if res is None:
        return backend.get("note", "No matching record found.")
    if fn == "get_reconciliation_summary":
        s = res
        return (f"{s['total_transactions']} bank credits: {s['matched']} matched, {s['needs_review']} in review, "
                f"{s['unmatched']} unmatched, {s['duplicates']} duplicates. Received {_inr(s['amount_received'])}, "
                f"of which {_inr(s['amount_matched'])} is matched. Outstanding receivables "
                f"{_inr(s['outstanding_value'])} across {s['invoices_open'] + s['invoices_partial']} open invoices. "
                f"{s['open_review_cases']} review cases are open.")
    if fn == "get_invoice":
        inv = res["invoice"]
        paid = sum(float(a["allocated_amount"]) for a in res["allocations"] if a["allocation_status"] == "approved")
        prop = [a for a in res["allocations"] if a["allocation_status"] == "proposed"]
        return (f"{inv['invoice_id']} — {inv.get('customer_name')}: total {_inr(inv['total_amount'])}, "
                f"received {_inr(paid)}, outstanding {_inr(inv['outstanding_amount'])}, status {inv['status']}, "
                f"due {inv.get('due_date') or 'n/a'}." + (f" {len(prop)} payment(s) awaiting review." if prop else ""))
    if fn == "get_transaction":
        t = res["transaction"]
        al = ", ".join(f"{a['invoice_id']} {_inr(a['allocated_amount'])} ({a['allocation_status']})"
                       for a in res["allocations"]) or "no allocations"
        return f"{t['transaction_id']} ({t.get('narration')}) {_inr(t['credit_amount'])} on {t.get('transaction_date')}: {t['status']} — {al}."
    if fn == "get_customer_invoices":
        invs = res["invoices"]
        out = sum(float(i["outstanding_amount"]) for i in invs if i["status"] in ("open", "partially_paid"))
        lines = [f"- {i['invoice_id']}: {_inr(i['total_amount'])}, outstanding {_inr(i['outstanding_amount'])} ({i['status']})"
                 for i in invs[:15]]
        return f"{res['customer']} has {len(invs)} invoice(s), {_inr(out)} outstanding:\n" + "\n".join(lines)
    rows = res if isinstance(res, list) else []
    titles = {
        "get_unmatched_transactions": "unmatched bank credit(s)", "get_duplicate_transactions": "duplicate credit(s)",
        "get_open_review_cases": "open review case(s)", "get_tds_register": "TDS deduction(s)",
        "get_partially_paid_invoices": "partially paid invoice(s)", "get_aging": "invoice(s) with a balance",
        "get_customer_balances": "customer(s)", "get_settled_invoices": "settled invoice(s)",
    }
    if not rows:
        return f"There are no {titles.get(fn, 'records')}."
    head = f"{len(rows)} {titles.get(fn, 'record(s)')}"
    lines = []
    for r in rows[:12]:
        if fn in ("get_unmatched_transactions", "get_duplicate_transactions"):
            lines.append(f"- {r['transaction_id']} {_inr(r['credit_amount'])} · {r.get('narration')} · {r.get('transaction_date')}")
        elif fn == "get_open_review_cases":
            lines.append(f"- {r['case_id']} · {r['exception_type'].replace('_', ' ')} · {r['transaction_id']}"
                         + (f" → {r['invoice_id']}" if r.get("invoice_id") else ""))
        elif fn == "get_tds_register":
            lines.append(f"- {r['invoice_id']} ({r.get('customer_name')}): {_inr(r['amount'])} "
                         f"{r.get('tds_section') or ''} · {r.get('tds_status')}")
        elif fn == "get_aging":
            lines.append(f"- {r['invoice_id']} ({r['customer_name']}): {_inr(r['outstanding_amount'])}, "
                         f"{r['days_past_due']} days past due ({r['bucket']})")
        elif fn == "get_customer_balances":
            lines.append(f"- {r['customer_name']}: outstanding {_inr(r['outstanding'])} on {r['open_invoices']} invoice(s)")
        else:
            lines.append(f"- {r.get('invoice_id')} ({r.get('customer_name')}): total {_inr(r.get('total_amount', 0))}, "
                         f"outstanding {_inr(r.get('outstanding_amount', 0))}")
    total_note = ""
    if fn == "get_aging":
        total_note = f" totalling {_inr(sum(r['outstanding_amount'] for r in rows))}"
    elif fn in ("get_unmatched_transactions", "get_duplicate_transactions"):
        total_note = f" totalling {_inr(sum(r['credit_amount'] for r in rows))}"
    more = f"\n…and {len(rows) - 12} more." if len(rows) > 12 else ""
    return f"{head}{total_note}:\n" + "\n".join(lines) + more


def answer_finance_query(query: str) -> dict:
    """
    Route a natural-language question to a controlled backend function, then phrase
    the answer. The LLM (if configured) only rewords the data; offline we render it.
    """
    backend = _route(query)
    offline = _offline_answer(query, backend)
    answer, used_llm = offline, False
    from extraction.llm_extractor import llm_available, answer_finance_query as llm_answer
    if llm_available():
        payload = dict(backend)
        if isinstance(payload.get("result"), list):
            payload["result"] = payload["result"][:40]
        try:
            answer = llm_answer(query, {**payload, "deterministic_answer": offline}).strip() or offline
            used_llm = True
        except Exception:
            answer = offline
    return {"query": query, "backend_function": backend["function"], "answer": answer,
            "raw_data": backend.get("result"), "used_llm": used_llm}
