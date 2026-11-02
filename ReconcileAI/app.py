"""
ReconcileAI – Main Streamlit Application
Six screens: Dashboard | Upload | Workspace | Review | Reports | Audit
"""
from __future__ import annotations
import json
import sys
import os

# Add project root to Python path
sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st
import pandas as pd

from database.db import initialise_database
from database.repositories import (
    get_reconciliation_summary, get_all_transactions, get_all_invoices,
    get_open_review_cases, get_all_review_cases, get_audit_logs,
    get_allocations_for_transaction, get_all_documents,
)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="ReconcileAI",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

* { font-family: 'Inter', sans-serif !important; }

/* Dark background */
.stApp { background: #0a0f1e; }
section[data-testid="stSidebar"] { background: #0d1428 !important; border-right: 1px solid #1e2d4a; }
section[data-testid="stSidebar"] .stMarkdown p { color: #8899bb !important; }

/* Metric cards */
.metric-card {
    background: linear-gradient(135deg, #0f1d3a 0%, #162040 100%);
    border: 1px solid #1e3060;
    border-radius: 16px;
    padding: 24px;
    text-align: center;
    transition: transform 0.2s, box-shadow 0.2s;
    position: relative;
    overflow: hidden;
}
.metric-card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0; height: 3px;
    border-radius: 16px 16px 0 0;
}
.metric-card.green::before  { background: linear-gradient(90deg, #00d4a0, #00ff87); }
.metric-card.blue::before   { background: linear-gradient(90deg, #4a9eff, #00cfff); }
.metric-card.orange::before { background: linear-gradient(90deg, #ff9f43, #ffcc02); }
.metric-card.red::before    { background: linear-gradient(90deg, #ff4757, #ff6b81); }
.metric-card:hover { transform: translateY(-4px); box-shadow: 0 16px 40px rgba(0,0,0,0.4); }
.metric-value { font-size: 2.8rem; font-weight: 800; line-height: 1; margin-bottom: 6px; }
.metric-card.green  .metric-value { color: #00d4a0; }
.metric-card.blue   .metric-value { color: #4a9eff; }
.metric-card.orange .metric-value { color: #ff9f43; }
.metric-card.red    .metric-value { color: #ff4757; }
.metric-label { font-size: 0.85rem; color: #8899bb; font-weight: 500; letter-spacing: 0.05em; text-transform: uppercase; }

/* Section header */
.section-header {
    font-size: 1.4rem; font-weight: 700; color: #e8f0ff;
    border-bottom: 1px solid #1e3060; padding-bottom: 12px; margin-bottom: 20px;
}

/* Status badges */
.badge {
    display: inline-block; padding: 3px 10px; border-radius: 20px;
    font-size: 0.75rem; font-weight: 600; letter-spacing: 0.04em;
}
.badge-matched  { background: #003d2b; color: #00d4a0; border: 1px solid #00d4a0; }
.badge-review   { background: #3d2800; color: #ff9f43; border: 1px solid #ff9f43; }
.badge-unmatched{ background: #2d1018; color: #ff4757; border: 1px solid #ff4757; }
.badge-duplicate{ background: #1a1040; color: #a855f7; border: 1px solid #a855f7; }
.badge-open     { background: #001a3d; color: #4a9eff; border: 1px solid #4a9eff; }
.badge-settled  { background: #003d2b; color: #00d4a0; border: 1px solid #00d4a0; }
.badge-partial  { background: #2d1a00; color: #ffcc02; border: 1px solid #ffcc02; }

/* Upload box */
.upload-zone {
    border: 2px dashed #1e3060; border-radius: 16px;
    padding: 40px; text-align: center; background: #0d1428;
    transition: border-color 0.2s;
}
.upload-zone:hover { border-color: #4a9eff; }

/* Buttons */
.stButton > button {
    background: linear-gradient(135deg, #1a4bcc, #0c2f8a) !important;
    color: white !important; border: none !important;
    border-radius: 10px !important; font-weight: 600 !important;
    transition: all 0.2s !important;
}
.stButton > button:hover {
    transform: translateY(-2px) !important;
    box-shadow: 0 8px 24px rgba(26,75,204,0.4) !important;
}
.stButton > button[kind="secondary"] {
    background: #1e3060 !important;
}

/* DataFrames */
.stDataFrame { border-radius: 12px; overflow: hidden; }
div[data-testid="stDataFrameResizable"] table { background: #0d1428 !important; color: #d0e0ff; }
div[data-testid="stDataFrameResizable"] th { background: #1a2d50 !important; color: #4a9eff !important; }
div[data-testid="stDataFrameResizable"] tr:hover { background: #122040 !important; }

/* Success / error messages */
div[data-testid="stAlert"] { border-radius: 12px !important; }

/* Tabs */
button[role="tab"] { color: #8899bb !important; font-weight: 500 !important; }
button[role="tab"][aria-selected="true"] { color: #4a9eff !important; border-bottom: 2px solid #4a9eff !important; }

/* Chat */
.chat-bubble { background: #0d1428; border: 1px solid #1e3060; border-radius: 16px 16px 16px 4px; padding: 14px 18px; margin: 8px 0; }
.chat-bubble.user { background: #0f2050; border-color: #1a4bcc; border-radius: 16px 16px 4px 16px; }
.chat-text { color: #d0e0ff; line-height: 1.6; }

/* Sidebar logo */
.sidebar-logo { font-size: 1.5rem; font-weight: 800; color: #4a9eff; letter-spacing: -0.02em; }
.sidebar-logo span { color: #00d4a0; }

/* Progress bar */
.stProgress > div > div > div { background: linear-gradient(90deg, #4a9eff, #00d4a0) !important; border-radius: 4px; }
</style>
""", unsafe_allow_html=True)

# ── Initialise DB ─────────────────────────────────────────────────────────────
initialise_database()

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown('<div class="sidebar-logo">Reconcile<span>AI</span></div>', unsafe_allow_html=True)
    st.markdown("*AI-powered accounts receivable*")
    st.divider()

    page = st.radio(
        "Navigation",
        ["📊 Dashboard", "📁 Upload", "🔍 Workspace", "👤 Human Review", "📈 Reports", "🕵️ Audit Logs"],
        label_visibility="collapsed",
    )
    st.divider()
    st.markdown("**Quick Actions**")

    if st.button("⚡ Run Reconciliation", width='stretch'):
        with st.spinner("Running reconciliation batch…"):
            try:
                from decision.decision_engine import run_batch_reconciliation
                result = run_batch_reconciliation()
                st.success(f"Batch complete: {result['matched']} matched, "
                           f"{result['needs_review']} review, {result['unmatched']} unmatched")
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")

    if st.button("🗃️ Load Demo Dataset", width='stretch'):
        with st.spinner("Loading synthetic dataset…"):
            try:
                from extraction.ingestion import load_synthetic_dataset
                result = load_synthetic_dataset()
                st.success(f"Loaded: {result['invoices']} invoices, {result['transactions']} transactions")
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")

    st.divider()
    summary = get_reconciliation_summary()
    st.caption(f"DB: {summary['total_transactions']} transactions")


# ── Page: Dashboard ───────────────────────────────────────────────────────────
if page == "📊 Dashboard":
    st.markdown("## ⚡ ReconcileAI Dashboard")
    st.markdown("*Accounts receivable reconciliation — real-time overview*")

    summary = get_reconciliation_summary()

    # KPI row
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f"""<div class="metric-card blue">
            <div class="metric-value">{summary['total_transactions']}</div>
            <div class="metric-label">Total Transactions</div>
        </div>""", unsafe_allow_html=True)
    with col2:
        st.markdown(f"""<div class="metric-card green">
            <div class="metric-value">{summary['matched']}</div>
            <div class="metric-label">✓ Matched</div>
        </div>""", unsafe_allow_html=True)
    with col3:
        st.markdown(f"""<div class="metric-card orange">
            <div class="metric-value">{summary['needs_review']}</div>
            <div class="metric-label">⚠ Needs Review</div>
        </div>""", unsafe_allow_html=True)
    with col4:
        st.markdown(f"""<div class="metric-card red">
            <div class="metric-value">{summary['unmatched']}</div>
            <div class="metric-label">✗ Unmatched</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Invoice status
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(f"""<div class="metric-card blue">
            <div class="metric-value">{summary['invoices_open']}</div>
            <div class="metric-label">Open Invoices</div>
        </div>""", unsafe_allow_html=True)
    with col2:
        st.markdown(f"""<div class="metric-card orange">
            <div class="metric-value">{summary['invoices_partial']}</div>
            <div class="metric-label">Partially Paid</div>
        </div>""", unsafe_allow_html=True)
    with col3:
        st.markdown(f"""<div class="metric-card green">
            <div class="metric-value">{summary['invoices_settled']}</div>
            <div class="metric-label">Settled</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Auto-reconciliation rate
    total = summary["total_transactions"] or 1
    rate  = summary["matched"] / total
    st.markdown("#### 🎯 Auto-Reconciliation Rate")
    st.progress(rate)
    st.caption(f"{rate:.1%} of transactions automatically matched")

    # Open review cases
    if summary["open_review_cases"] > 0:
        st.warning(f"⚠️ **{summary['open_review_cases']} cases** require human review")

    # Recent transactions
    st.markdown("---")
    st.markdown('<div class="section-header">Recent Transactions</div>', unsafe_allow_html=True)
    txns = get_all_transactions()
    if txns:
        df = pd.DataFrame(txns[-10:])
        _STATUS_EMOJI = {"matched": "✅", "review": "⚠️", "unmatched": "❌", "duplicate": "🔁"}
        df["status"] = df["status"].apply(lambda s: f"{_STATUS_EMOJI.get(s,'🔵')} {s.title()}")
        df["credit_amount"] = df["credit_amount"].apply(lambda x: f"₹{x:,.2f}")
        st.dataframe(df[["transaction_id","utr","narration","credit_amount","transaction_date","status"]],
                     width='stretch', hide_index=True)

    # AI Query Assistant
    st.markdown("---")
    st.markdown('<div class="section-header">💬 Finance Assistant</div>', unsafe_allow_html=True)
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    for msg in st.session_state.chat_history[-6:]:
        css_class = "user" if msg["role"] == "user" else ""
        st.markdown(f'<div class="chat-bubble {css_class}"><div class="chat-text">{"🧑 " if msg["role"]=="user" else "🤖 "}{msg["content"]}</div></div>',
                    unsafe_allow_html=True)

    query = st.chat_input("Ask about your reconciliation data…")
    if query:
        st.session_state.chat_history.append({"role": "user", "content": query})
        try:
            from reporting.report_generator import answer_finance_query
            result = answer_finance_query(query)
            answer = result["answer"]
        except Exception as e:
            answer = f"⚠️ Assistant unavailable: {e}"
        st.session_state.chat_history.append({"role": "assistant", "content": answer})
        st.rerun()


# ── Page: Upload ──────────────────────────────────────────────────────────────
elif page == "📁 Upload":
    st.markdown("## 📁 Document Upload")
    st.markdown("Upload bank statements, invoices and payment emails for processing.")

    from extraction.ingestion import ingest_bank_statement, ingest_invoice, ingest_remittance

    tab1, tab2, tab3 = st.tabs(["🏦 Bank Statements", "📄 Invoices", "✉️ Payment Emails"])

    with tab1:
        st.markdown("#### Upload Bank Statement")
        st.caption("Supported formats: CSV, Excel (.xlsx, .xls)")
        bank_name = st.text_input("Bank Name", value="AXIS Bank", key="bank_name")
        uploaded = st.file_uploader("Choose file", type=["csv", "xlsx", "xls"],
                                     key="bank_upload", label_visibility="collapsed")
        if uploaded:
            if st.button("📥 Import Bank Statement", key="import_bank"):
                with st.spinner("Parsing bank statement…"):
                    result = ingest_bank_statement(uploaded.name, uploaded.read(), bank_name=bank_name)
                    if result["success"]:
                        st.success(f"✅ Imported {result['transactions_imported']} transactions from {uploaded.name}")
                    else:
                        st.error(f"❌ {result['reason']}")

    with tab2:
        st.markdown("#### Upload Invoices")
        st.caption("Supported formats: PDF, Excel (.xlsx, .xls)")
        uploaded = st.file_uploader("Choose invoice file", type=["pdf", "xlsx", "xls"],
                                     key="invoice_upload", label_visibility="collapsed")
        if uploaded:
            if st.button("📥 Import Invoices", key="import_invoice"):
                with st.spinner("Extracting invoice data…"):
                    result = ingest_invoice(uploaded.name, uploaded.read())
                    if result["success"]:
                        st.success(f"✅ Imported {result['invoices_imported']} invoice(s): {result.get('invoice_ids', [])}")
                    else:
                        st.error(f"❌ {result['reason']}")

        st.markdown("---")
        st.markdown("#### Paste Email / Remittance Text")

    with tab3:
        st.markdown("#### Upload Payment Emails")
        st.caption("Supported formats: .eml, .txt. Or paste below.")
        uploaded = st.file_uploader("Choose email file", type=["eml", "txt"],
                                     key="email_upload", label_visibility="collapsed")
        pasted = st.text_area("Or paste email content here:", height=200, key="email_paste")

        if st.button("📥 Import Remittance", key="import_email"):
            content = None
            fname   = "pasted_email.txt"
            if uploaded:
                content = uploaded.read()
                fname   = uploaded.name
            elif pasted.strip():
                content = pasted.encode("utf-8")
            if content:
                with st.spinner("Extracting remittance data…"):
                    result = ingest_remittance(fname, content)
                    if result["success"]:
                        st.success(f"✅ Remittance extracted from {fname}")
                        with st.expander("Extracted Data"):
                            st.json(result.get("extracted", {}))
                    else:
                        st.error(f"❌ {result['reason']}")

    # Uploaded Documents Table
    st.markdown("---")
    st.markdown('<div class="section-header">Uploaded Documents</div>', unsafe_allow_html=True)
    docs = get_all_documents()
    if docs:
        df = pd.DataFrame(docs)
        st.dataframe(df[["document_id","filename","document_type","upload_time"]],
                     width='stretch', hide_index=True)
    else:
        st.info("No documents uploaded yet. Use the demo dataset from the sidebar.")


# ── Page: Reconciliation Workspace ───────────────────────────────────────────
elif page == "🔍 Workspace":
    st.markdown("## 🔍 Reconciliation Workspace")
    st.markdown("Inspect transaction-to-invoice match candidates and allocation details.")

    txns = get_all_transactions()
    if not txns:
        st.info("No transactions loaded. Upload a bank statement or load the demo dataset.")
        st.stop()

    status_filter = st.selectbox("Filter by status",
                                  ["All", "unmatched", "review", "matched", "duplicate"])
    if status_filter != "All":
        txns = [t for t in txns if t["status"] == status_filter]

    for txn in txns[:50]:
        status = txn.get("status", "unmatched")
        badge_class = {"matched": "matched", "review": "review",
                       "unmatched": "unmatched", "duplicate": "duplicate"}.get(status, "unmatched")
        with st.expander(
            f"📌 {txn['transaction_id']}  •  ₹{txn['credit_amount']:,.2f}  •  "
            f"{txn.get('transaction_date','?')}  •  {status.upper()}",
            expanded=(status == "review")
        ):
            col1, col2 = st.columns([2, 1])
            with col1:
                st.markdown(f"**Narration:** {txn.get('narration','—')}")
                st.markdown(f"**UTR:** `{txn.get('utr','—')}`")
            with col2:
                st.markdown(f"""<span class="badge badge-{badge_class}">{status.upper()}</span>""",
                            unsafe_allow_html=True)
                st.markdown(f"**Credit:** ₹{txn['credit_amount']:,.2f}")

            allocs = get_allocations_for_transaction(txn["transaction_id"])
            if allocs:
                st.markdown("**Allocations:**")
                for a in allocs:
                    alloc_status = a.get("allocation_status","?")
                    score = a.get("confidence_score") or 0
                    st.markdown(
                        f"→ `{a['invoice_id']}` | ₹{a['allocated_amount']:,.2f} | "
                        f"Score: **{score:.0%}** | Status: *{alloc_status}*"
                    )
                    if a.get("decision_reason"):
                        st.caption(f"🤖 {a['decision_reason']}")
            else:
                st.caption("No allocations yet.")

            # Run individual reconciliation
            if status == "unmatched":
                if st.button(f"🔄 Reconcile {txn['transaction_id']}", key=f"rec_{txn['transaction_id']}"):
                    with st.spinner("Running…"):
                        try:
                            from decision.decision_engine import reconcile_transaction
                            res = reconcile_transaction(txn["transaction_id"])
                            outcome = res.get("outcome","?")
                            if outcome == "matched":
                                st.success(f"✅ Matched to {res.get('invoice_id')} (score {res.get('score',0):.0%})")
                            elif outcome == "review":
                                st.warning(f"⚠️ Sent to review: {res.get('reason','')}")
                            else:
                                st.info(f"ℹ️ {outcome}: {res.get('reason','')}")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error: {e}")


# ── Page: Human Review ────────────────────────────────────────────────────────
elif page == "👤 Human Review":
    st.markdown("## 👤 Human Review Queue")
    st.markdown("Approve, reject or correct uncertain reconciliation decisions.")

    from decision.review_manager import approve_match, reject_match, correct_match

    open_cases = get_open_review_cases()

    if not open_cases:
        st.success("✅ No open cases — all transactions have been reviewed!")
        st.markdown("---")
        st.markdown("**Resolved Cases**")
        all_cases = get_all_review_cases()
        if all_cases:
            df = pd.DataFrame([c for c in all_cases if c["status"] != "open"])
            if not df.empty:
                st.dataframe(df[["case_id","transaction_id","invoice_id","exception_type","status","reviewed_at"]],
                             width='stretch', hide_index=True)
        st.stop()

    st.markdown(f"**{len(open_cases)} open case(s)**")

    for case in open_cases:
        exc_type = case["exception_type"].replace("_", " ").title()
        with st.expander(f"🔴 {case['case_id']}  •  {exc_type}  •  TXN: {case['transaction_id']}", expanded=True):
            col1, col2 = st.columns([3, 2])
            with col1:
                st.markdown(f"**Exception:** {exc_type}")
                st.markdown(f"**Summary:** {case.get('exception_summary','—')}")
                if case.get("invoice_id"):
                    st.markdown(f"**Suggested Invoice:** `{case['invoice_id']}`")
                if case.get("suggested_action"):
                    st.info(f"💡 {case['suggested_action']}")

            with col2:
                reviewer = st.text_input("Your name", value="Finance Team", key=f"rev_{case['case_id']}")
                notes    = st.text_area("Notes", height=80, key=f"notes_{case['case_id']}")

                # Get the proposed allocation for this case
                allocs = get_allocations_for_transaction(case["transaction_id"])
                proposed = [a for a in allocs if a["allocation_status"] == "proposed"]

                col_a, col_b, col_c = st.columns(3)
                with col_a:
                    if proposed and st.button("✅ Approve", key=f"approve_{case['case_id']}"):
                        try:
                            result = approve_match(case["case_id"], proposed[0]["allocation_id"],
                                                   reviewer, notes)
                            st.success("Approved!")
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))

                with col_b:
                    if proposed and st.button("❌ Reject", key=f"reject_{case['case_id']}"):
                        try:
                            result = reject_match(case["case_id"], proposed[0]["allocation_id"],
                                                  reviewer, notes)
                            st.warning("Rejected")
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))

                with col_c:
                    with st.popover("✏️ Correct"):
                        all_invoices = get_all_invoices()
                        inv_options  = [i["invoice_id"] for i in all_invoices]
                        chosen_inv   = st.selectbox("Select correct invoice",
                                                     inv_options, key=f"corr_inv_{case['case_id']}")
                        if st.button("Confirm Correction", key=f"confirm_corr_{case['case_id']}"):
                            try:
                                result = correct_match(case["case_id"], case["transaction_id"],
                                                       chosen_inv, reviewer, notes)
                                st.success(f"Corrected → {chosen_inv}")
                                st.rerun()
                            except Exception as e:
                                st.error(str(e))


# ── Page: Reports ─────────────────────────────────────────────────────────────
elif page == "📈 Reports":
    st.markdown("## 📈 Reconciliation Reports")
    st.markdown("View settled invoices, outstanding balances and export results.")

    tab1, tab2, tab3 = st.tabs(["📊 Summary", "📄 Invoice Status", "💾 Export"])

    with tab1:
        summary = get_reconciliation_summary()
        st.markdown("#### Reconciliation Batch Summary")

        col1, col2 = st.columns(2)
        with col1:
            data = {"Status": ["Matched", "Needs Review", "Unmatched", "Duplicates"],
                    "Count": [summary["matched"], summary["needs_review"],
                               summary["unmatched"], summary["duplicates"]]}
            st.dataframe(pd.DataFrame(data), width='stretch', hide_index=True)

        with col2:
            inv_data = {"Invoice Status": ["Open", "Partially Paid", "Settled"],
                        "Count": [summary["invoices_open"], summary["invoices_partial"],
                                   summary["invoices_settled"]]}
            st.dataframe(pd.DataFrame(inv_data), width='stretch', hide_index=True)

        # AI Management Summary
        st.markdown("#### 🤖 AI Management Summary")
        if st.button("Generate AI Summary"):
            with st.spinner("Generating…"):
                try:
                    from extraction.llm_extractor import generate_reconciliation_report_summary
                    text = generate_reconciliation_report_summary(summary)
                    st.markdown(f"> {text}")
                except Exception as e:
                    st.warning(f"LLM unavailable: {e}")
                    st.markdown(f"> **{summary['total_transactions']}** transactions processed. "
                                f"**{summary['matched']}** automatically matched ({summary['matched']/(summary['total_transactions'] or 1):.0%}). "
                                f"**{summary['needs_review']}** require review. "
                                f"**{summary['unmatched']}** remain unmatched.")

    with tab2:
        invoices = get_all_invoices()
        if invoices:
            df = pd.DataFrame(invoices)
            status_filter = st.multiselect("Filter status",
                                            ["open","partially_paid","settled","overpaid"],
                                            default=["open","partially_paid","settled"])
            if status_filter:
                df = df[df["status"].isin(status_filter)]
            df["total_amount"]      = df["total_amount"].apply(lambda x: f"₹{x:,.2f}")
            df["outstanding_amount"] = df["outstanding_amount"].apply(lambda x: f"₹{x:,.2f}")
            st.dataframe(df[["invoice_id","customer_name","invoice_date","due_date",
                              "total_amount","outstanding_amount","status"]],
                         width='stretch', hide_index=True)
        else:
            st.info("No invoices loaded.")

    with tab3:
        st.markdown("#### Export Reconciliation Report")
        st.markdown("Downloads an Excel workbook with all transactions, invoices, review cases and audit logs.")
        if st.button("📥 Generate and Download Report", type="primary"):
            with st.spinner("Building Excel report…"):
                try:
                    from reporting.report_generator import export_reconciliation_report
                    path = export_reconciliation_report()
                    with open(path, "rb") as f:
                        st.download_button(
                            label="⬇️ Download Excel Report",
                            data=f.read(),
                            file_name=path.split("/")[-1].split("\\")[-1],
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        )
                    st.success(f"Report saved to {path}")
                except Exception as e:
                    st.error(f"Error generating report: {e}")


# ── Page: Audit Logs ──────────────────────────────────────────────────────────
elif page == "🕵️ Audit Logs":
    st.markdown("## 🕵️ Audit Logs")
    st.markdown("Complete decision history — every action traced to its evidence.")

    search_id = st.text_input("Search by entity ID (transaction, invoice, allocation…)",
                               placeholder="e.g. TXN-001")
    logs = get_audit_logs(entity_id=search_id if search_id else None, limit=200)

    if logs:
        df = pd.DataFrame(logs)
        # Parse details JSON
        def _fmt_details(d):
            if not d:
                return "—"
            try:
                parsed = json.loads(d) if isinstance(d, str) else d
                return str(parsed)[:120]
            except Exception:
                return str(d)[:120]
        df["details"] = df["details"].apply(_fmt_details)
        st.dataframe(df[["log_id","entity_type","entity_id","action","actor","details","created_at"]],
                     width='stretch', hide_index=True)
    else:
        st.info("No audit logs yet.")
