"""
ReconcileAI – Streamlit UI

Pages: Overview · Review queue · Bank credits · Import · Reports · Audit trail · Ask the data

UI rules this file follows
- Theme (colour, type, radius, borders) lives in .streamlit/config.toml. The only CSS here
  is for the few custom figures Streamlit has no component for.
- Every number on screen is read from the database. Text written by the model is labelled
  as such and never carries a figure the tables above it don't also show.
- Money uses Indian digit grouping (₹1,06,200.00). Timestamps are shown in IST.
- Status is never colour alone: every colour comes with a label (and an icon where it fits).
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
from collections import Counter
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
import streamlit as st

from database.db import initialise_database
from database.repositories import (
    get_reconciliation_summary, get_all_transactions, get_all_invoices,
    get_open_review_cases, get_all_review_cases, get_audit_logs, get_audit_actions,
    get_allocations_for_transaction, get_all_documents, get_transaction, get_invoice,
    get_settlement_components, get_remittance_by_utr, get_tds_components,
)
from config.settings import ALLOWED_EXTENSIONS
from config.thresholds import TDS_SECTION_RATES

st.set_page_config(
    page_title="ReconcileAI",
    page_icon=":material/account_balance:",
    layout="wide",
    initial_sidebar_state="auto",   # open on desktop, collapsed on phones
)

initialise_database()

# Only what the theme can't express: the hero figure, the value-split bar, key/value rows.
st.html("""
<style>
.ra-brand{font-weight:600;font-size:1.125rem;letter-spacing:-.01em;line-height:1.3;margin:.25rem 0 .5rem}
.ra-brand small{display:block;font-weight:400;font-size:.8125rem;opacity:.68;letter-spacing:0}
.ra-eyebrow{font-size:.8125rem;font-weight:500;opacity:.72;margin:0}
.ra-hero{font-size:3rem;font-weight:600;line-height:1.1;letter-spacing:-.015em;margin:.25rem 0}
.ra-sub{font-size:.875rem;opacity:.72;margin:0}
.ra-split{display:flex;gap:2px;height:8px;margin:1.25rem 0 .75rem}
.ra-split span{display:block;min-width:3px}
.ra-split span:first-child{border-radius:4px 0 0 4px}
.ra-split span:last-child{border-radius:0 4px 4px 0}
.ra-split span:only-child{border-radius:4px}
.ra-rows{width:100%;border-collapse:collapse;font-size:.875rem}
.ra-rows th{font-weight:500;opacity:.68;text-align:left;padding:.25rem 0}
.ra-rows td{padding:.45rem 0;border-top:1px solid rgba(127,127,127,.2)}
.ra-rows .num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;padding-left:1rem}
.ra-dot{display:inline-block;width:.625rem;height:.625rem;border-radius:2px;margin-right:.5rem}
.ra-kv{width:100%;border-collapse:collapse;font-size:.875rem;margin-top:.25rem}
.ra-kv th{font-weight:400;opacity:.68;text-align:left;vertical-align:top;padding:.3rem 1rem .3rem 0;white-space:nowrap;width:1%}
.ra-kv td{padding:.3rem 0;vertical-align:top;overflow-wrap:anywhere}
.ra-kv tr+tr th,.ra-kv tr+tr td{border-top:1px solid rgba(127,127,127,.16)}
.ra-kv code{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:.8125rem}
</style>
""")


# ── Vocabulary ────────────────────────────────────────────────────────────────

TXN_STATUS = {   # label, badge colour, icon
    "matched":   ("Matched",   "green",  ":material/check_circle:"),
    "review":    ("In review", "orange", ":material/pending:"),
    "unmatched": ("Unmatched", "red",    ":material/error:"),
    "duplicate": ("Duplicate", "gray",   ":material/content_copy:"),
    "excluded":  ("Excluded",  "gray",   ":material/block:"),
}
INV_STATUS = {
    "open":           ("Open",        "blue"),
    "partially_paid": ("Part-paid",   "orange"),
    "settled":        ("Settled",     "green"),
    "overpaid":       ("Overpaid",    "violet"),
}
CASE_STATUS = {
    "open": "Open", "approved": "Approved", "rejected": "Rejected", "corrected": "Reassigned",
    "escalated": "Escalated", "superseded": "Superseded by re-run",
}
EXCEPTIONS = {
    "multiple_candidate_invoices": "Several invoices fit",
    "possible_tds_deduction":      "Short-paid, possible TDS",
    "partial_payment":             "Part-payment",
    "overpayment":                 "Overpayment",
    "invoice_already_settled":     "Invoice already settled",
    "bulk_payment":                "One credit, several invoices",
    "low_confidence_match":        "Weak match",
    "missing_invoice_reference":   "No invoice reference",
    "unknown_customer":            "Unknown payer",
    "possible_duplicate":          "Possible duplicate",
    "amount_discrepancy":          "Amount differs",
}
# Status palette from the dataviz reference (fixed across light/dark, always shown with a label)
SPLIT_COLOURS = {"matched": "#0CA30C", "review": "#FAB219", "unmatched": "#D03B3B", "duplicate": "#8A8F98"}


def exc_label(kind: str | None) -> str:
    return EXCEPTIONS.get(kind or "", (kind or "—").replace("_", " ").capitalize())


def txn_label(status: str | None) -> str:
    return TXN_STATUS.get(status or "", ((status or "—").title(), "", ""))[0]


def inv_label(status: str | None) -> str:
    return INV_STATUS.get(status or "", ((status or "—").replace("_", " ").title(), ""))[0]


# ── Formatting ────────────────────────────────────────────────────────────────

def inr(value, decimals: int = 2) -> str:
    """₹ with Indian digit grouping: 1234567.5 -> ₹12,34,567.50."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    amount = float(value)
    whole, _, frac = f"{abs(amount):.{decimals}f}".partition(".")
    if len(whole) > 3:
        head, groups = whole[:-3], []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join(([head] if head else []) + groups + [whole[-3:]])
    return ("-" if amount < 0 else "") + "₹" + whole + (f".{frac}" if frac else "")


def inr_short(value) -> str:
    """Compact lakh/crore form for tiles; the exact figure goes in the tooltip."""
    amount = float(value or 0)
    if abs(amount) >= 1e7:
        return f"₹{amount / 1e7:.2f} Cr"
    if abs(amount) >= 1e5:
        return f"₹{amount / 1e5:.2f} L"
    return inr(amount, 0)


def fmt_date(value) -> str:
    if not value:
        return "—"
    try:
        d = pd.to_datetime(value)
    except (ValueError, TypeError):
        return str(value)
    return f"{d.day} {d:%b %Y}"


def fmt_ts(value) -> str:
    if not value:
        return "—"
    try:
        d = pd.to_datetime(value, utc=True).tz_convert("Asia/Kolkata")
    except (ValueError, TypeError):
        return str(value)
    return f"{d.day} {d:%b %Y}, {d:%H:%M} IST"


def to_dates(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


def to_ist(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)


_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]<>()#+\-!|$~])")


def md_escape(text) -> str:
    """Render engine/DB text literally (no accidental bold, links or LaTeX from '$')."""
    return _MD_SPECIAL.sub(r"\\\1", str(text or "—"))


def no_math(text) -> str:
    """Keep the model's markdown formatting but stop '$' amounts turning into LaTeX."""
    return str(text or "").replace("$", "\\$")


def money(df: pd.DataFrame, cols: list[str]):
    """Styler that shows Indian-grouped ₹ while keeping the column numeric (sortable, right-aligned)."""
    return df.style.format({c: inr for c in cols if c in df.columns}, na_rep="—")


def table_height(rows: int, max_rows: int = 12) -> int:
    return 36 + 35 * max(1, min(rows, max_rows)) + 2


def kv_table(rows: list[tuple]) -> None:
    """Label/value rows. A third tuple item of True renders the value as code (IDs, UTRs)."""
    out = []
    for row in rows:
        label, value, as_code = (row + (False,))[:3]
        shown = html.escape(str(value)) if value not in (None, "") else "—"
        if as_code and value not in (None, "", "—"):
            shown = f"<code>{shown}</code>"
        out.append(f"<tr><th>{html.escape(label)}</th><td>{shown}</td></tr>")
    st.html(f'<table class="ra-kv">{"".join(out)}</table>')


def file_types(document_type: str) -> list[str]:
    """Upload extensions the importer accepts, so the widget and the backend never disagree."""
    return sorted(e.lstrip(".") for e in ALLOWED_EXTENSIONS[document_type])


def page_header(title: str, subtitle: str) -> None:
    st.title(title, anchor=False)
    st.caption(subtitle)


# ── Session helpers ───────────────────────────────────────────────────────────

def flash(message: str, icon: str = ":material/check_circle:") -> None:
    st.session_state["_flash"] = (message, icon)


def show_flash() -> None:
    pending = st.session_state.pop("_flash", None)
    if pending:
        st.toast(pending[0], icon=pending[1])


def current_reviewer() -> str:
    return (st.session_state.get("reviewer") or "").strip()


def review_queue(cases: list[dict]) -> list[dict]:
    """
    One entry per bank credit: its newest open case, older open ones under `stale`. The engine
    supersedes old cases itself; this only keeps the queue readable if any are left over.
    """
    by_txn: dict[str, list[dict]] = {}
    for c in cases:
        by_txn.setdefault(c["transaction_id"], []).append(c)
    queue = []
    for group in by_txn.values():
        group.sort(key=lambda c: c.get("created_at") or "")
        queue.append({**group[-1], "stale": group[:-1]})
    return queue


def run_batch() -> None:
    from decision.decision_engine import run_batch_reconciliation
    bar = st.progress(0.0, text="Starting…")
    try:
        result = run_batch_reconciliation(
            progress=lambda k, n, _r: bar.progress(k / max(n, 1), text=f"Credit {k} of {n}"))
    except Exception as e:
        bar.empty()
        st.error(f"Reconciliation stopped: {e}")
        return
    bar.empty()
    if not result.get("total"):
        flash("Nothing to reconcile — no unmatched credits.", ":material/info:")
    else:
        flash(f"{result['total']} credits processed: {result['matched']} matched, "
              f"{result['needs_review']} to review, {result['unmatched']} unmatched, "
              f"{result.get('duplicates', 0)} duplicates.")
    st.rerun()


def load_demo() -> None:
    from extraction.ingestion import load_synthetic_dataset
    try:
        with st.spinner("Loading synthetic dataset…"):
            result = load_synthetic_dataset()
    except Exception as e:
        st.error(f"Could not load the demo dataset: {e}")
        return
    if result.get("already_loaded"):
        flash("The demo dataset is already loaded.", ":material/info:")
    else:
        flash(f"Loaded {result['invoices']} invoices and {result['transactions']} bank credits (synthetic).")
    st.rerun()


def empty_state(title: str, body: str, key: str) -> None:
    with st.container(border=True):
        st.markdown(f"**{title}**")
        st.caption(body)
        with st.container(horizontal=True):
            if st.button("Load demo dataset", icon=":material/dataset:", key=f"empty_demo_{key}"):
                load_demo()
            st.page_link(PAGES["import"], label="Import documents", icon=":material/upload_file:")


# ── Page: Overview ────────────────────────────────────────────────────────────

def _split_card(total: float, parts: list[tuple[str, float, int]], n_credits: int) -> str:
    segments, rows, spoken = [], [], []
    for status, amount, count in parts:
        label, colour = txn_label(status), SPLIT_COLOURS[status]
        share = amount / total if total else 0.0
        spoken.append(f"{label} {share:.0%}")
        if amount > 0:
            segments.append(f'<span style="flex:{amount:.2f} 1 0;background:{colour}" '
                            f'title="{label}: {inr(amount)} ({share:.0%})"></span>')
        rows.append(f'<tr><td><span class="ra-dot" style="background:{colour}"></span>{label}</td>'
                    f'<td class="num">{count}</td><td class="num">{inr(amount)}</td>'
                    f'<td class="num">{share:.0%}</td></tr>')
    return (f'<p class="ra-eyebrow">Bank credits received</p>'
            f'<div class="ra-hero">{inr(total, 0)}</div>'
            f'<p class="ra-sub">{n_credits} credit lines from imported statements</p>'
            f'<div class="ra-split" role="img" aria-label="Share of value: {", ".join(spoken)}">'
            f'{"".join(segments)}</div>'
            f'<table class="ra-rows"><thead><tr><th>Status</th><th class="num">Credits</th>'
            f'<th class="num">Amount</th><th class="num">Share</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')


def page_overview() -> None:
    page_header("Overview", "Bank credits matched against open receivables. Every figure is computed "
                            "from the ledger — the model only reads documents.")
    summary = get_reconciliation_summary()
    txns = get_all_transactions()
    if not txns:
        empty_state("No bank credits yet", "Import a bank statement and invoices, or load the synthetic "
                                           "demo dataset to explore the workflow.", "overview")
        return

    dup_amount = sum(float(t["credit_amount"] or 0) for t in txns if t["status"] == "duplicate")
    parts = [
        ("matched",   summary["amount_matched"],   summary["matched"]),
        ("review",    summary["amount_in_review"], summary["needs_review"]),
        ("unmatched", summary["amount_unmatched"], summary["unmatched"]),
        ("duplicate", dup_amount,                  summary["duplicates"]),
    ]

    left, right = st.columns([3, 2], gap="large")
    with left, st.container(border=True, height="stretch"):
        st.html(_split_card(summary["amount_received"], parts, summary["total_transactions"]))
    queue = review_queue(get_open_review_cases())
    with right:
        st.metric("Outstanding receivables", inr_short(summary["outstanding_value"]), border=True,
                  help=f"{inr(summary['outstanding_value'])} across open and part-paid invoices.")
        st.metric("Open invoices", summary["invoices_open"] + summary["invoices_partial"], border=True,
                  help=f"{summary['invoices_open']} open, {summary['invoices_partial']} part-paid, "
                       f"{summary['invoices_settled']} settled.")
        st.metric("TDS awaiting certificate", inr_short(summary["tds_pending"]), border=True,
                  help=f"{inr(summary['tds_pending'])} deducted by customers on approved matches, "
                       "not yet verified against Form 16A.")
        st.metric("Credits waiting for review", len(queue), border=True,
                  help="Bank credits a person has to decide on.")

    if queue:
        by_id = {t["transaction_id"]: t for t in txns}
        rows = [{
            "Transaction": c["transaction_id"],
            "Amount": float((by_id.get(c["transaction_id"]) or {}).get("credit_amount") or 0),
            "Reason": exc_label(c["exception_type"]),
            "Suggested invoice": c.get("invoice_id") or "—",
        } for c in queue]
        df = pd.DataFrame(rows).sort_values("Amount", ascending=False).head(5)
        with st.container(horizontal=True, vertical_alignment="bottom"):
            st.subheader("Largest credits waiting for review", anchor=False)
            st.page_link(PAGES["review"], label="Open the review queue", icon=":material/arrow_forward:")
        st.dataframe(money(df, ["Amount"]), hide_index=True, height=table_height(len(df)))

    st.subheader("Latest bank credits", anchor=False)
    df = pd.DataFrame(txns)
    df["date"] = to_dates(df["transaction_date"])
    df = df.sort_values(["date", "transaction_id"], ascending=False).head(8)
    view = pd.DataFrame({
        "Value date": df["date"], "Transaction": df["transaction_id"], "Narration": df["narration"],
        "UTR": df["utr"], "Amount": df["credit_amount"].astype(float),
        "Status": df["status"].map(txn_label),
    })
    st.dataframe(money(view, ["Amount"]), hide_index=True, height=table_height(len(view)),
                 column_config={"Value date": st.column_config.DateColumn(format="D MMM YYYY")})
    st.page_link(PAGES["transactions"], label="All bank credits", icon=":material/arrow_forward:")


# ── Page: Review queue ────────────────────────────────────────────────────────

def _invoice_choices(credit: float) -> dict[str, str]:
    """Open invoices, closest outstanding balance to the credit first."""
    open_invs = [i for i in get_all_invoices() if i["status"] in ("open", "partially_paid")]
    open_invs.sort(key=lambda i: abs(float(i["outstanding_amount"] or 0) - credit))
    return {i["invoice_id"]: f"{i['invoice_id']} · {i.get('customer_name') or '—'} · "
                             f"{inr(i['outstanding_amount'])} due" for i in open_invs}


def _allocation_rows(allocs: list[dict]) -> pd.DataFrame:
    rows = []
    for a in allocs:
        comps = get_settlement_components(a["allocation_id"])
        tds = [c for c in comps if c["component_type"] == "tds"]
        rows.append({
            "Invoice": a["invoice_id"],
            "Bank credit": sum(float(c["amount"]) for c in comps if c["component_type"] == "bank_credit"),
            "TDS": sum(float(c["amount"]) for c in tds),
            "TDS section": ", ".join(sorted({c["tds_section"] for c in tds if c.get("tds_section")})) or "—",
            "Applied": float(a["allocated_amount"] or 0),
            "Score": float(a.get("confidence_score") or 0),
            "Status": (a.get("allocation_status") or "").capitalize(),
            "Decided by": a.get("approved_by") or "—",
        })
    return pd.DataFrame(rows)


ALLOC_COLUMNS = {
    "Score": st.column_config.ProgressColumn("Match score", min_value=0.0, max_value=1.0, format="percent",
                                             help="Engine's matching score, not a probability."),
    "Applied": st.column_config.NumberColumn("Applied to invoice", help="Bank credit plus TDS."),
}


def _case_detail(case: dict, txn: dict) -> None:
    cid, tid, kind = case["case_id"], case["transaction_id"], case["exception_type"]
    credit = float(txn.get("credit_amount") or 0)
    proposed = [a for a in get_allocations_for_transaction(tid) if a["allocation_status"] == "proposed"]
    inv = get_invoice(case["invoice_id"]) if case.get("invoice_id") else {}

    st.markdown(f"### {tid} &nbsp;:orange-badge[:material/flag: {exc_label(kind)}]")
    stale = case.get("stale", [])
    st.caption(f"Case {cid} · opened {fmt_ts(case.get('created_at'))}"
               + (f" · {len(stale)} older open case{'s' if len(stale) > 1 else ''} for this credit will be "
                  "closed with your decision" if stale else ""))

    c1, c2 = st.columns(2)
    with c1, st.container(border=True, height="stretch"):
        st.metric("Bank credit", inr(credit))
        kv_table([("Value date", fmt_date(txn.get("transaction_date"))), ("Bank", txn.get("bank_name")),
                  ("UTR", txn.get("utr"), True), ("Narration", txn.get("narration"), True)])
    with c2, st.container(border=True, height="stretch"):
        if inv:
            st.metric(f"Outstanding on {inv['invoice_id']}", inr(inv.get("outstanding_amount")))
            kv_table([("Customer", inv.get("customer_name")), ("Invoice date", fmt_date(inv.get("invoice_date"))),
                      ("Due", fmt_date(inv.get("due_date"))), ("Invoice total", inr(inv.get("total_amount"))),
                      ("Taxable value", inr(inv.get("taxable_amount"))), ("Status", inv_label(inv.get("status")))])
        else:
            st.metric("Suggested invoice", "None")
            st.caption("The engine could not point to an invoice. Check the remittance advice or ask the "
                       "payer, then assign one below.")

    if inv and len(proposed) <= 1:
        outstanding = float(inv.get("outstanding_amount") or 0)
        taxable = float(inv.get("taxable_amount") or 0)
        diff = round(credit - outstanding, 2)
        if abs(diff) < 0.01:
            st.success(f"The credit equals the {inr(outstanding)} outstanding on {inv['invoice_id']}.",
                       icon=":material/check:")
        elif diff < 0:
            share = f" — {-diff / taxable:.2%} of the taxable value ({inr(taxable)})" if taxable else ""
            st.warning(f"Short of the outstanding balance by **{inr(-diff)}**{share}. Whether that is TDS, "
                       "a part-payment or an error is the reviewer's call.", icon=":material/difference:")
        else:
            st.info(f"More than the outstanding balance by **{inr(diff)}**.", icon=":material/add_circle:")

    if proposed:
        st.markdown("**What approving will post**")
        df = _allocation_rows(proposed)
        st.dataframe(money(df, ["Bank credit", "TDS", "Applied"]), hide_index=True,
                     column_order=["Invoice", "Bank credit", "TDS", "TDS section", "Applied", "Score"],
                     column_config=ALLOC_COLUMNS, height=table_height(len(df), 6))

    with st.container(border=True):
        st.markdown("**Why it's here**")
        st.markdown(md_escape(case.get("exception_summary")))
        if case.get("suggested_action"):
            st.caption("Suggested next step: " + md_escape(case["suggested_action"]))

    rem = get_remittance_by_utr(txn.get("utr"))
    with st.expander("Remittance advice · " + ("linked by UTR" if rem else "none with this UTR"),
                     icon=":material/mail:"):
        if rem:
            refs = sorted(set(json.loads(rem.get("invoice_references") or "[]")))
            tds = float(rem.get("tds_amount") or 0)
            kv_table([("Payer", rem.get("customer_name")), ("Invoices quoted", ", ".join(refs) or "—"),
                      ("Amount paid", inr(rem.get("payment_amount"))),
                      ("TDS stated", inr(tds) + (f" under {rem['tds_section']}" if rem.get("tds_section") else "")),
                      ("Payment date", fmt_date(rem.get("payment_date"))), ("UTR", rem.get("utr"), True)])
            excerpts = json.loads(rem.get("evidence_text") or "[]")
            if excerpts:
                st.caption("Verbatim excerpt the figures were extracted from")
                for text in excerpts:
                    st.code(text, language=None, wrap_lines=True)
        else:
            st.caption("No imported payment email quotes this UTR.")
    if proposed and proposed[0].get("decision_reason"):
        with st.expander("Engine explanation", icon=":material/notes:"):
            st.markdown(md_escape(proposed[0]["decision_reason"]))

    _decision_box(case, txn, proposed, rem)


def _decision_box(case: dict, txn: dict, proposed: list[dict], rem: dict | None) -> None:
    from decision import review_manager as rm

    cid, tid, kind = case["case_id"], case["transaction_id"], case["exception_type"]
    credit = float(txn.get("credit_amount") or 0)
    who = current_reviewer()
    pressed: dict[str, bool] = {}
    with st.container(border=True):
        st.markdown("**Decision**")
        if not who:
            st.info("Enter your name under **Reviewer** in the sidebar. Every decision is recorded against it.",
                    icon=":material/badge:")
        note = st.text_area("Note for the audit trail", key=f"note_{cid}", height=80,
                            placeholder="Required for anything except approving, e.g. “Payer confirmed TDS "
                                        "under 194J; Form 16A requested.”").strip()

        if kind == "possible_duplicate":
            with st.container(horizontal=True):
                pressed["dup_confirm"] = st.button("Confirm duplicate", type="primary", icon=":material/content_copy:",
                                                   key=f"dupok_{cid}", disabled=not who)
                pressed["dup_release"] = st.button("Not a duplicate — reconcile it", icon=":material/call_split:",
                                                   key=f"dupno_{cid}", disabled=not who)
        else:
            why_not = None if proposed else "There is no proposed allocation on this case. Apply it to an invoice below."
            with st.container(horizontal=True):
                pressed["approve"] = st.button("Approve", type="primary", icon=":material/check:", key=f"approve_{cid}",
                                               disabled=not (who and proposed), help=why_not)
                pressed["reject"] = st.button("Reject", icon=":material/close:", key=f"reject_{cid}",
                                              disabled=not (who and proposed), help=why_not)
                pressed["dismiss"] = st.button("Not a customer payment", type="tertiary", icon=":material/block:",
                                               key=f"dismiss_{cid}", disabled=not who,
                                               help="Interest, refund or internal transfer — excluded from receivables.")

            st.markdown("**Or apply it to other invoices**")
            choices = _invoice_choices(credit)
            chosen = st.multiselect("Invoices, in the order to settle them", list(choices), format_func=choices.get,
                                    key=f"pick_{cid}", placeholder="Type an invoice number or customer",
                                    help="Each invoice's balance is cleared in turn; any excess stays on the last one.")
            proposed_tds = sum(float(c["amount"]) for a in proposed for c in get_settlement_components(a["allocation_id"])
                               if c["component_type"] == "tds")
            stated_tds = float((rem or {}).get("tds_amount") or 0) or float(proposed_tds)
            sections = [s for s in TDS_SECTION_RATES if s != "DEFAULT"]
            stated_section = (rem or {}).get("tds_section")
            t1, t2 = st.columns(2)
            tds_amount = t1.number_input("TDS deducted by the customer (₹)", min_value=0.0, step=100.0, format="%.2f",
                                         value=stated_tds, key=f"tds_{cid}",
                                         help="Pre-filled from the remittance advice when it states one.")
            tds_section = t2.selectbox("TDS section", sections, key=f"sec_{cid}", index=sections.index(stated_section)
                                       if stated_section in sections else None, placeholder="None",
                                       format_func=lambda s: f"{s} · {TDS_SECTION_RATES[s]:.0%}")
            with st.container(horizontal=True, vertical_alignment="center"):
                pressed["assign"] = st.button("Apply to selected invoices", icon=":material/playlist_add_check:",
                                              key=f"assign_{cid}", disabled=not (who and chosen))
                if chosen:
                    st.caption(f"{inr(credit + tds_amount)} will be applied: {inr(credit)} bank credit"
                               + (f" + {inr(tds_amount)} TDS" if tds_amount else "") + ".")

    action = next((k for k, v in pressed.items() if v), None)
    if not action:
        return
    if action != "approve" and action != "dup_confirm" and not note:
        st.error("Add a note for the audit trail first — it's required for this decision.",
                 icon=":material/edit_note:")
        return
    try:
        if action == "approve":
            res = rm.approve_match(cid, reviewer=who, notes=note)
            message = f"Approved — {tid} applied to {', '.join(res.get('invoices') or [])}."
        elif action == "reject":
            rm.reject_match(cid, reviewer=who, notes=note)
            message = f"Rejected — {tid} is unmatched again and those invoices won't be proposed for it."
        elif action == "dismiss":
            rm.dismiss_transaction(cid, reviewer=who, notes=note)
            message = f"{tid} marked as not a customer payment."
        elif action == "dup_confirm":
            rm.confirm_duplicate(cid, reviewer=who, notes=note)
            message = f"{tid} confirmed as a duplicate."
        elif action == "dup_release":
            res = rm.release_duplicate(cid, reviewer=who, notes=note)
            outcome = (res.get("reconcile_result") or {}).get("outcome")
            message = f"{tid} released" + (f" and reconciled: {txn_label(outcome).lower()}." if outcome else ".")
        else:
            if tds_amount > 0 and not tds_section:
                st.error("Pick the TDS section the customer deducted under.", icon=":material/rule:")
                return
            res = rm.correct_match(cid, tid, chosen, reviewer=who, notes=note,
                                   tds_amount=tds_amount, tds_section=tds_section if tds_amount > 0 else None)
            message = f"{tid} applied to {', '.join(res.get('new_invoice_ids') or chosen)}."
    except rm.ReviewError as e:
        st.error(str(e), icon=":material/block:")
        return
    except Exception as e:
        st.error(f"The decision was not saved: {e}")
        return
    flash(message)
    st.rerun()


def _resolved_cases() -> None:
    done = [c for c in get_all_review_cases() if c["status"] != "open"]
    with st.expander(f"Resolved cases · {len(done)}", icon=":material/inventory_2:"):
        if not done:
            st.caption("None yet.")
            return
        df = pd.DataFrame(done)
        view = pd.DataFrame({
            "Decided (IST)": to_ist(df["reviewed_at"]), "Transaction": df["transaction_id"],
            "Reason": df["exception_type"].map(exc_label), "Outcome": df["status"].map(lambda s: CASE_STATUS.get(s, s)),
            "Invoice": df["invoice_id"].fillna("—"), "Reviewer": df["reviewer"].fillna("—"),
            "Note": df["resolution_notes"].fillna(""),
        }).sort_values("Decided (IST)", ascending=False)
        st.dataframe(view, hide_index=True, height=table_height(len(view), 10),
                     column_config={"Decided (IST)": st.column_config.DatetimeColumn(format="D MMM YYYY, HH:mm")})


def page_review() -> None:
    page_header("Review queue", "Credits the engine would not settle on its own. A named reviewer approves, "
                                "rejects or reassigns each one, and every decision goes to the audit trail.")
    cases = review_queue(get_open_review_cases())
    if not cases:
        st.success("Nothing waiting for review.", icon=":material/task_alt:")
        _resolved_cases()
        return

    txns = {t["transaction_id"]: t for t in get_all_transactions()}
    amount_of = lambda c: float((txns.get(c["transaction_id"]) or {}).get("credit_amount") or 0)
    counts = Counter(c["exception_type"] for c in cases)
    kinds = sorted(counts, key=lambda k: (-counts[k], k))
    pick = st.pills("Filter by reason", ["all", *kinds], default="all", required=True, key="review_filter",
                    label_visibility="collapsed",
                    format_func=lambda k: f"All · {len(cases)}" if k == "all" else f"{exc_label(k)} · {counts[k]}")
    shown = sorted((c for c in cases if pick in (None, "all") or c["exception_type"] == pick),
                   key=lambda c: -amount_of(c))

    left, right = st.columns([5, 7], gap="large")
    with left:
        st.caption(f"{len(shown)} credits, largest first. Select a row to work on it.")
        df = pd.DataFrame([{"Transaction": c["transaction_id"], "Amount": amount_of(c),
                            "Reason": exc_label(c["exception_type"])} for c in shown])
        event = st.dataframe(money(df, ["Amount"]), hide_index=True, on_select="rerun",
                             selection_mode="single-row", key=f"queue_{pick}",
                             height=table_height(len(df), 16))
        picked = event.selection.rows
    case = shown[picked[0] if picked and picked[0] < len(shown) else 0]
    with right:
        _case_detail(case, txns.get(case["transaction_id"]) or get_transaction(case["transaction_id"]))

    st.divider()
    _resolved_cases()


# ── Page: Bank credits ────────────────────────────────────────────────────────

def _txn_detail(txn: dict) -> None:
    tid = txn["transaction_id"]
    label, colour, icon = TXN_STATUS.get(txn["status"], (txn["status"], "gray", ":material/help:"))
    with st.container(border=True):
        st.markdown(f"### {tid} &nbsp;:{colour}-badge[{icon} {label}]")
        c1, c2 = st.columns([1, 2], gap="large")
        with c1:
            st.metric("Credit", inr(txn.get("credit_amount")))
            kv_table([("Value date", fmt_date(txn.get("transaction_date"))), ("Bank", txn.get("bank_name")),
                      ("Account", txn.get("account_number"), True), ("UTR", txn.get("utr"), True),
                      ("Narration", txn.get("narration"), True)])
        with c2:
            allocs = get_allocations_for_transaction(tid)
            st.markdown("**Applied to**")
            if allocs:
                df = _allocation_rows(allocs)
                st.dataframe(money(df, ["Bank credit", "TDS", "Applied"]), hide_index=True,
                             column_config=ALLOC_COLUMNS, height=table_height(len(df), 6))
                reason = allocs[-1].get("decision_reason")
                if reason:
                    st.caption("Match explanation (may be model-written; the table above is authoritative)")
                    st.markdown(md_escape(reason))
            else:
                st.caption("Not applied to any invoice yet.")
            if txn["status"] == "unmatched" and st.button("Reconcile this credit", icon=":material/play_arrow:",
                                                          key=f"rec_{tid}"):
                from decision.decision_engine import reconcile_transaction
                try:
                    with st.spinner("Matching…"):
                        res = reconcile_transaction(tid)
                except Exception as e:
                    st.error(f"Reconciliation failed: {e}")
                    return
                outcome = res.get("outcome", "?")
                flash(f"{tid}: {txn_label(outcome) if outcome in TXN_STATUS else outcome}"
                      + (f" — {res['invoice_id']}" if res.get("invoice_id") else ""))
                st.rerun()
            if txn["status"] == "matched":
                _undo_match(tid)


def _undo_match(tid: str) -> None:
    from decision import review_manager as rm
    who = current_reviewer()
    with st.popover("Undo this match", icon=":material/undo:", disabled=not who,
                    help=None if who else "Enter your name under Reviewer in the sidebar first."):
        st.caption("Reverses every approved allocation on this credit and re-opens the invoices. "
                   "The credit goes back to unmatched.")
        note = st.text_input("Reason", key=f"undo_note_{tid}", placeholder="e.g. Customer says this paid INV-021").strip()
        if st.button("Undo match", type="primary", key=f"undo_{tid}", disabled=not note):
            try:
                rm.reverse_transaction(tid, reviewer=who, notes=note)
            except rm.ReviewError as e:
                st.error(str(e))
                return
            flash(f"Match on {tid} reversed.", ":material/undo:")
            st.rerun()


def page_transactions() -> None:
    page_header("Bank credits", "Every credit line from imported statements and the invoices it was applied to.")
    txns = get_all_transactions()
    if not txns:
        empty_state("No bank credits yet", "Import a bank statement to see its credit lines here.", "txns")
        return

    counts = Counter(t["status"] for t in txns)
    order = [s for s in ("unmatched", "review", "matched", "duplicate", "excluded") if counts.get(s)]
    with st.container(horizontal=True, vertical_alignment="center"):
        pick = st.pills("Status", ["all", *order], default="all", required=True, key="txn_filter",
                        label_visibility="collapsed",
                        format_func=lambda s: f"All · {len(txns)}" if s == "all" else f"{txn_label(s)} · {counts[s]}")
        query = st.text_input("Search", placeholder="Transaction, UTR or narration", key="txn_search",
                              label_visibility="collapsed", icon=":material/search:").strip().lower()

    rows = [t for t in txns if pick in (None, "all") or t["status"] == pick]
    if query:
        rows = [t for t in rows if query in " ".join(str(t.get(k) or "") for k in
                                                     ("transaction_id", "utr", "narration")).lower()]
    if not rows:
        st.info("No credits match this filter.", icon=":material/filter_alt_off:")
        return
    rows.sort(key=lambda t: (t.get("transaction_date") or "", t["transaction_id"]), reverse=True)
    df = pd.DataFrame(rows)
    view = pd.DataFrame({
        "Value date": to_dates(df["transaction_date"]), "Transaction": df["transaction_id"],
        "Narration": df["narration"], "UTR": df["utr"], "Amount": df["credit_amount"].astype(float),
        "Status": df["status"].map(txn_label),
    })
    event = st.dataframe(money(view, ["Amount"]), hide_index=True, on_select="rerun", selection_mode="single-row",
                         key=f"txns_{pick}_{query}", height=table_height(len(view), 12),
                         column_config={"Value date": st.column_config.DateColumn(format="D MMM YYYY")})
    picked = event.selection.rows
    if picked and picked[0] < len(rows):
        _txn_detail(rows[picked[0]])
    else:
        st.caption("Select a row to see which invoices it was applied to and why.")


# ── Page: Import ──────────────────────────────────────────────────────────────

def _import_result(result: dict, ok_message: str) -> None:
    if result.get("success"):
        st.success(ok_message, icon=":material/check_circle:")
    elif result.get("duplicate_file"):
        st.info(result["reason"], icon=":material/content_copy:")
    else:
        st.error(result.get("reason", "Import failed."), icon=":material/error:")


def page_import() -> None:
    from extraction.ingestion import ingest_bank_statement, ingest_invoice, ingest_remittance

    page_header("Import", "Bring in the three sources reconciliation needs. Re-uploading the same file is "
                          "detected and skipped.")
    bank_tab, inv_tab, mail_tab = st.tabs(["Bank statement", "Invoices", "Remittance advice"])

    with bank_tab:
        st.caption("CSV or Excel. Columns are parsed directly, with no model involved.")
        bank = st.text_input("Bank", value="AXIS Bank", key="bank_name")
        up = st.file_uploader("Statement file", type=file_types("bank_statement"), key="bank_upload")
        if up and st.button("Import statement", type="primary", icon=":material/upload:", key="import_bank"):
            with st.spinner("Parsing statement…"):
                res = ingest_bank_statement(up.name, up.getvalue(), bank_name=bank)
            _import_result(res, f"Imported {res.get('transactions_imported', 0)} credit lines from {up.name}.")

    with inv_tab:
        st.caption("PDF, CSV or Excel register. Spreadsheets are parsed directly; PDF text is structured (by the model "
                   "when one is configured) and validated before it is saved.")
        up = st.file_uploader("Invoice file", type=file_types("invoice"), key="invoice_upload")
        if up and st.button("Import invoices", type="primary", icon=":material/upload:", key="import_invoice"):
            with st.spinner("Reading invoices…"):
                res = ingest_invoice(up.name, up.getvalue())
            ids = ", ".join(res.get("invoice_ids") or [])
            _import_result(res, f"Imported {res.get('invoices_imported', 0)} invoice(s){': ' + ids if ids else ''}.")

    with mail_tab:
        st.caption("A payment email (.eml, .txt or PDF) or pasted text. Invoice references, amounts, TDS and UTR "
                   "are extracted (by the model when one is configured, otherwise by rules), and the sentence each "
                   "value came from is kept as evidence.")
        up = st.file_uploader("Email file", type=file_types("remittance"), key="email_upload")
        pasted = st.text_area("Or paste the email", height=180, key="email_paste",
                              placeholder="From: accounts@customer.example\nSubject: Payment against INV-005 …")
        if st.button("Import remittance", type="primary", icon=":material/upload:", key="import_email",
                     disabled=not (up or pasted.strip())):
            name, content = (up.name, up.getvalue()) if up else ("pasted_email.txt", pasted.encode("utf-8"))
            with st.spinner("Extracting remittance details…"):
                res = ingest_remittance(name, content)
            _import_result(res, f"Remittance extracted from {name}.")
            if res.get("extracted"):
                with st.expander("Extracted fields", icon=":material/data_object:"):
                    st.json(res["extracted"])

    st.subheader("Imported documents", anchor=False)
    docs = get_all_documents()
    if not docs:
        st.caption("Nothing imported yet.")
        return
    df = pd.DataFrame(docs)
    view = pd.DataFrame({"Uploaded (IST)": to_ist(df["upload_time"]), "File": df["filename"],
                         "Type": df["document_type"].fillna("unknown").str.replace("_", " ").str.capitalize(),
                         "Document": df["document_id"]}).sort_values("Uploaded (IST)", ascending=False)
    st.dataframe(view, hide_index=True, height=table_height(len(view), 10),
                 column_config={"Uploaded (IST)": st.column_config.DatetimeColumn(format="D MMM YYYY, HH:mm")})


# ── Page: Reports ─────────────────────────────────────────────────────────────

AGEING_LABELS = {"Not due": "Not yet due", "1–30": "1–30 days overdue", "31–60": "31–60 days overdue",
                 "61–90": "61–90 days overdue", "90+": "Over 90 days overdue"}
TDS_STATUS = {"claimed": "Claimed", "evidence_pending": "Awaiting Form 16A", "verified": "Verified",
              "rejected": "Rejected"}


def _reports_summary(summary: dict, txns: list[dict], invoices: list[dict]) -> None:
    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.markdown("**Bank credits by status**")
        if txns:
            g = pd.DataFrame(txns).groupby("status")["credit_amount"].agg(["count", "sum"]).reset_index()
            view = pd.DataFrame({"Status": g["status"].map(txn_label), "Credits": g["count"], "Amount": g["sum"]})
            st.dataframe(money(view, ["Amount"]), hide_index=True)
        else:
            st.caption("No credits yet.")
    with c2:
        st.markdown("**Invoices by status**")
        if invoices:
            g = pd.DataFrame(invoices).groupby("status").agg(
                n=("invoice_id", "count"), total=("total_amount", "sum"), out=("outstanding_amount", "sum")).reset_index()
            view = pd.DataFrame({"Status": g["status"].map(inv_label), "Invoices": g["n"],
                                 "Invoice value": g["total"], "Outstanding": g["out"]})
            st.dataframe(money(view, ["Invoice value", "Outstanding"]), hide_index=True)
        else:
            st.caption("No invoices yet.")

    with st.container(border=True):
        st.markdown("**Management summary**")
        st.caption("A draft written by the model from the figures above. Check every number against the "
                   "tables before you send it.")
        if st.button("Draft summary", icon=":material/edit_note:", key="draft_summary"):
            try:
                from extraction.llm_extractor import generate_reconciliation_report_summary
                with st.spinner("Drafting…"):
                    st.session_state["ai_summary"] = generate_reconciliation_report_summary(summary)
            except Exception as e:
                st.session_state["ai_summary"] = None
                st.warning(f"Model unavailable ({e}). Showing the template summary instead.",
                           icon=":material/cloud_off:")
        text = st.session_state.get("ai_summary")
        total = summary["total_transactions"] or 1
        st.markdown(no_math(text) if text else
                    f"{summary['total_transactions']} credits processed, {summary['matched']} matched "
                    f"({summary['matched'] / total:.0%}), {summary['needs_review']} awaiting review and "
                    f"{summary['unmatched']} unmatched. Outstanding receivables stand at "
                    f"{inr(summary['outstanding_value'])}.")


def _reports_ageing() -> None:
    from reporting.report_generator import AGING_BUCKETS, aging_report, aging_summary
    today = date.today()
    st.caption(f"Open and part-paid invoices as of {fmt_date(today)}. Days overdue = today minus due date.")
    detail = aging_report(today)
    if detail.empty:
        st.success("Nothing outstanding.", icon=":material/task_alt:")
        return
    g = detail.groupby("bucket")["outstanding_amount"].agg(["count", "sum"]).reindex(AGING_BUCKETS, fill_value=0)
    grand = float(g["sum"].sum()) or 1.0
    buckets = pd.DataFrame({"Age": [AGEING_LABELS.get(b, b) for b in g.index], "Invoices": g["count"].astype(int).values,
                            "Outstanding": g["sum"].values, "Share": (g["sum"] / grand).values})
    st.dataframe(money(buckets, ["Outstanding"]), hide_index=True,
                 column_config={"Share": st.column_config.NumberColumn(format="percent")})

    st.markdown("**By customer**")
    pivot = aging_summary(today).rename(columns={"customer_name": "Customer", **AGEING_LABELS})
    st.dataframe(money(pivot, [*AGEING_LABELS.values(), "Total"]), hide_index=True,
                 height=table_height(len(pivot), 12))

    with st.expander(f"Invoices · {len(detail)}", icon=":material/receipt_long:"):
        view = pd.DataFrame({
            "Invoice": detail["invoice_id"], "Customer": detail["customer_name"], "Due": to_dates(detail["due_date"]),
            "Days overdue": detail["days_past_due"].clip(lower=0), "Outstanding": detail["outstanding_amount"],
            "Age": detail["bucket"].map(lambda b: AGEING_LABELS.get(b, b)),
        })
        st.dataframe(money(view, ["Outstanding"]), hide_index=True, height=table_height(len(view), 12),
                     column_config={"Due": st.column_config.DateColumn(format="D MMM YYYY")})


def _reports_customers() -> None:
    from reporting.report_generator import customer_balances
    df = customer_balances()
    if df.empty:
        st.caption("No invoices yet.")
        return
    st.caption("Invoiced against what approved matches have applied. Received includes TDS the customer deducted.")
    view = pd.DataFrame({"Customer": df["customer_name"], "Invoices": df["invoices"], "Invoiced": df["invoiced"],
                         "Received": df["received"], "Outstanding": df["outstanding"],
                         "Open invoices": df["open_invoices"], "Overpaid by": df["overpaid_by"]})
    st.dataframe(money(view, ["Invoiced", "Received", "Outstanding", "Overpaid by"]), hide_index=True,
                 height=table_height(len(view), 15))


def _reports_tds() -> None:
    from decision import review_manager as rm
    from extraction.ingestion import store_supporting_document

    rows = get_tds_components()
    st.caption("TDS customers deducted on approved matches. The deduction only counts once the Form 16A (or 26AS "
               "entry) is checked; a rejected claim re-opens the invoice for that amount.")
    if not rows:
        st.info("No TDS deductions on approved matches yet.", icon=":material/receipt:")
        return
    df = pd.DataFrame(rows)
    pending = df[~df["tds_status"].isin(["verified", "rejected"])]["amount"].sum()
    with st.container(horizontal=True):
        st.metric("Awaiting certificate", inr(pending), border=True)
        st.metric("Verified", inr(df[df["tds_status"] == "verified"]["amount"].sum()), border=True)
        st.metric("Rejected", inr(df[df["tds_status"] == "rejected"]["amount"].sum()), border=True)
    view = pd.DataFrame({"Invoice": df["invoice_id"], "Customer": df["customer_name"], "Credit": df["transaction_id"],
                         "TDS": df["amount"].astype(float), "Section": df["tds_section"].fillna("—"),
                         "Status": df["tds_status"].map(lambda s: TDS_STATUS.get(s, s or "Claimed")),
                         "Certificate": df["supporting_doc"].fillna("—")})
    event = st.dataframe(money(view, ["TDS"]), hide_index=True, on_select="rerun", selection_mode="single-row",
                         key="tds_table", height=table_height(len(view), 10))
    picked = event.selection.rows
    if not (picked and picked[0] < len(rows)):
        st.caption("Select a row to verify it against a certificate or reject the claim.")
        return
    comp = rows[picked[0]]
    cid = comp["component_id"]
    who = current_reviewer()
    with st.container(border=True):
        st.markdown(f"**{inr(comp['amount'])} TDS on {comp['invoice_id']}** · {md_escape(comp.get('customer_name'))}"
                    f" · section {comp.get('tds_section') or 'not stated'}")
        if comp.get("tds_status") in ("verified", "rejected"):
            st.caption(f"Already {TDS_STATUS[comp['tds_status']].lower()}.")
            return
        if not who:
            st.info("Enter your name under **Reviewer** in the sidebar first.", icon=":material/badge:")
        cert = st.file_uploader("Form 16A (optional)", type=file_types("tds_certificate"), key=f"cert_{cid}")
        note = st.text_input("Note", key=f"tdsnote_{cid}", placeholder="e.g. Matched 26AS entry for Q3").strip()
        with st.container(horizontal=True):
            verify = st.button("Mark verified", type="primary", icon=":material/verified:", key=f"tdsok_{cid}",
                               disabled=not who)
            reject = st.button("Reject claim", icon=":material/block:", key=f"tdsno_{cid}", disabled=not who,
                               help="The customer still owes this amount; the invoice re-opens for it.")
    try:
        if verify:
            doc_id = None
            if cert:
                stored = store_supporting_document(cert.name, cert.getvalue())
                if not stored.get("success"):
                    st.error(stored.get("reason", "Could not store the certificate."))
                    return
                doc_id = stored["document_id"]
            rm.verify_tds(cid, reviewer=who, certificate_document_id=doc_id, notes=note)
            flash(f"TDS on {comp['invoice_id']} verified.")
            st.rerun()
        if reject:
            if not note:
                st.error("Add a note explaining why the claim is rejected.", icon=":material/edit_note:")
                return
            res = rm.reject_tds(cid, reviewer=who, notes=note)
            flash(f"TDS claim rejected — {res.get('invoice_id', comp['invoice_id'])} now has "
                  f"{inr(res.get('new_outstanding'))} outstanding.", ":material/undo:")
            st.rerun()
    except rm.ReviewError as e:
        st.error(str(e), icon=":material/block:")


def _reports_invoices(invoices: list[dict]) -> None:
    if not invoices:
        st.caption("No invoices loaded.")
        return
    statuses = [s for s in INV_STATUS if any(i["status"] == s for i in invoices)]
    chosen = st.pills("Status", statuses, selection_mode="multi", default=statuses, key="inv_filter",
                      format_func=inv_label, label_visibility="collapsed")
    idf = pd.DataFrame([i for i in invoices if i["status"] in (chosen or statuses)])
    view = pd.DataFrame({
        "Invoice": idf["invoice_id"], "Customer": idf["customer_name"],
        "Invoice date": to_dates(idf["invoice_date"]), "Due": to_dates(idf["due_date"]),
        "Total": idf["total_amount"].astype(float), "Outstanding": idf["outstanding_amount"].astype(float),
        "Status": idf["status"].map(inv_label),
    })
    st.dataframe(money(view, ["Total", "Outstanding"]), hide_index=True, height=table_height(len(view), 15),
                 column_config={"Invoice date": st.column_config.DateColumn(format="D MMM YYYY"),
                                "Due": st.column_config.DateColumn(format="D MMM YYYY")})


def page_reports() -> None:
    page_header("Reports", "Ledger figures for month-end: where the cash went, what is still owed, how old it is, "
                           "and which tax deductions still need evidence.")
    summary = get_reconciliation_summary()
    txns, invoices = get_all_transactions(), get_all_invoices()
    tabs = st.tabs(["Summary", "Receivables ageing", "Customers", "TDS register", "Invoices", "Export"])
    with tabs[0]:
        _reports_summary(summary, txns, invoices)
    with tabs[1]:
        _reports_ageing()
    with tabs[2]:
        _reports_customers()
    with tabs[3]:
        _reports_tds()
    with tabs[4]:
        _reports_invoices(invoices)
    with tabs[5]:
        st.caption("One Excel workbook: summary, transactions, invoices, allocations, settlement, review cases, "
                   "ageing, customers, TDS register, remittances and the audit log.")
        if st.button("Build Excel report", type="primary", icon=":material/table_view:", key="build_report"):
            try:
                from reporting.report_generator import build_report_workbook
                with st.spinner("Building workbook…"):
                    st.session_state["report_file"] = (f"reconciliation_{date.today():%Y-%m-%d}.xlsx",
                                                       build_report_workbook())
            except Exception as e:
                st.error(f"Could not build the report: {e}")
        if st.session_state.get("report_file"):
            name, data = st.session_state["report_file"]
            st.download_button(f"Download {name}", data=data, file_name=name, icon=":material/download:",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# ── Page: Audit trail ─────────────────────────────────────────────────────────

def page_audit() -> None:
    page_header("Audit trail", "Every automated and human action, newest first, with the evidence recorded at "
                               "the time.")
    with st.container(horizontal=True, vertical_alignment="bottom"):
        query = st.text_input("Search", placeholder="ID, action or detail", key="audit_q",
                              icon=":material/search:").strip()
        entity = st.selectbox("Entity", ["All", "transaction", "allocation", "invoice", "review_case",
                                         "remittance", "batch", "system"], key="audit_entity")
        action = st.selectbox("Action", ["All", *get_audit_actions()], key="audit_action")
    logs = get_audit_logs(search=query or None, entity_type=None if entity == "All" else entity,
                          action=None if action == "All" else action, limit=500)
    if not logs:
        st.info("No entries match.", icon=":material/filter_alt_off:")
        return
    df = pd.DataFrame(logs)

    def short(d):
        try:
            parsed = json.loads(d) if isinstance(d, str) else (d or {})
        except ValueError:
            return str(d)[:120]
        return ", ".join(f"{k}={v}" for k, v in parsed.items())[:120] if isinstance(parsed, dict) else str(parsed)[:120]

    view = pd.DataFrame({"When (IST)": to_ist(df["created_at"]), "Entity": df["entity_type"], "ID": df["entity_id"],
                         "Action": df["action"].str.replace("_", " "), "Actor": df["actor"],
                         "Details": df["details"].map(short)})
    st.caption(f"{len(view)} entries{' (limit 500)' if len(view) == 500 else ''}. Select one to see the full record.")
    event = st.dataframe(view, hide_index=True, on_select="rerun", selection_mode="single-row",
                         key=f"audit_{query}_{entity}_{action}", height=table_height(len(view), 14),
                         column_config={"When (IST)": st.column_config.DatetimeColumn(format="D MMM YYYY, HH:mm:ss")})
    picked = event.selection.rows
    if picked and picked[0] < len(logs):
        log = logs[picked[0]]
        with st.container(border=True):
            st.markdown(f"**{md_escape(log['action'].replace('_', ' ').capitalize())}** · "
                        f"{md_escape(log['entity_type'])} `{log['entity_id']}` · by {md_escape(log['actor'])} · "
                        f"{fmt_ts(log['created_at'])}")
            try:
                st.json(json.loads(log["details"]) if log.get("details") else {})
            except ValueError:
                st.code(log["details"], language=None, wrap_lines=True)
            st.caption(f"Log ID {log['log_id']}")


# ── Page: Ask the data ────────────────────────────────────────────────────────

EXAMPLE_QUESTIONS = ["Which credits are still unmatched?", "Which invoices are part-paid?",
                     "How much TDS have customers deducted?", "Summarise the open review cases"]


def _clear_chat() -> None:
    st.session_state["chat"] = []
    st.session_state.pop("chat_example", None)


def page_assistant() -> None:
    page_header("Ask the data", "Each question is routed to a fixed database lookup; the model, when configured, "
                                "only words the answer. Every reply shows the lookup and the rows it used.")
    history = st.session_state.setdefault("chat", [])

    example = None
    if not history:
        example = st.pills("Try a question", EXAMPLE_QUESTIONS, key="chat_example")

    for msg in history:
        with st.chat_message(msg["role"], avatar=":material/person:" if msg["role"] == "user" else ":material/smart_toy:"):
            st.markdown(no_math(msg["content"]) if msg["role"] == "assistant" else md_escape(msg["content"]))
            if "used_llm" in msg:
                st.caption("Worded by the model from the data below." if msg["used_llm"] else
                           "Computed from the data below. No model was used.")
            if msg.get("source"):
                rows = msg.get("rows")
                n = len(rows) if isinstance(rows, list) else 1
                with st.expander(f"Data used · {msg['source']} · {n} record{'s' if n != 1 else ''}",
                                 icon=":material/database:"):
                    if isinstance(rows, list) and rows:
                        st.dataframe(pd.DataFrame(rows), hide_index=True, height=table_height(len(rows), 8))
                    else:
                        st.json(rows)

    if history:
        st.button("Clear conversation", icon=":material/delete_sweep:", on_click=_clear_chat, key="clear_chat")

    prompt = st.chat_input("Ask about credits, invoices, TDS or review cases") or example
    if prompt:
        history.append({"role": "user", "content": prompt})
        try:
            from reporting.report_generator import answer_finance_query
            with st.spinner("Looking it up…"):
                res = answer_finance_query(prompt)
            history.append({"role": "assistant", "content": res["answer"], "used_llm": bool(res.get("used_llm")),
                            "source": res.get("backend_function"), "rows": res.get("raw_data")})
        except Exception as e:
            history.append({"role": "assistant", "content": f"The assistant is unavailable: {e}"})
        st.rerun()


# ── Navigation & sidebar ──────────────────────────────────────────────────────

PAGES = {
    "overview":     st.Page(page_overview,     title="Overview",     icon=":material/space_dashboard:", url_path="overview", default=True),
    "review":       st.Page(page_review,       title="Review queue", icon=":material/fact_check:",      url_path="review"),
    "transactions": st.Page(page_transactions, title="Bank credits", icon=":material/account_balance:", url_path="credits"),
    "import":       st.Page(page_import,       title="Import",       icon=":material/upload_file:",     url_path="import"),
    "reports":      st.Page(page_reports,      title="Reports",      icon=":material/summarize:",       url_path="reports"),
    "audit":        st.Page(page_audit,        title="Audit trail",  icon=":material/history:",         url_path="audit"),
    "assistant":    st.Page(page_assistant,    title="Ask the data", icon=":material/forum:",           url_path="ask"),
}
page = st.navigation(list(PAGES.values()), position="hidden")
show_flash()

with st.sidebar:
    st.html('<div class="ra-brand">ReconcileAI<small>Accounts receivable reconciliation</small></div>')
    sidebar_summary = get_reconciliation_summary()
    open_cases = len(review_queue(get_open_review_cases()))

    st.caption("Reconcile")
    st.page_link(PAGES["overview"])
    st.page_link(PAGES["review"], label=f"Review queue · {open_cases}" if open_cases else "Review queue")
    st.page_link(PAGES["transactions"])
    st.page_link(PAGES["import"])
    st.caption("Records")
    st.page_link(PAGES["reports"])
    st.page_link(PAGES["audit"])
    st.page_link(PAGES["assistant"])

    st.divider()
    st.text_input("Reviewer", key="reviewer", placeholder="Your name",
                  help="Recorded against every approval, rejection and reassignment.")
    if st.button("Run reconciliation", type="primary", icon=":material/play_arrow:", width="stretch",
                 help="Match every unmatched credit. Uncertain ones go to the review queue."):
        run_batch()
    if st.button("Load demo dataset", icon=":material/dataset:", width="stretch",
                 help="Synthetic invoices, bank credits and payment emails. No real client data."):
        load_demo()
    st.caption(f"{sidebar_summary['total_transactions']} credits · {sidebar_summary['invoices_total']} invoices")

page.run()
