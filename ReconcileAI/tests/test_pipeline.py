"""End-to-end tests: demo pipeline, review actions, duplicates, TDS, ingestion, migration, reports."""
import io
import json
import sqlite3

import pytest


def _status(tid):
    from database.repositories import get_transaction
    return get_transaction(tid)["status"]


def _allocs(tid, status=None):
    from database.repositories import get_allocations_for_transaction
    return [a for a in get_allocations_for_transaction(tid) if status is None or a["allocation_status"] == status]


def _open_case(tid):
    from database.repositories import get_open_cases_for_transaction
    cases = get_open_cases_for_transaction(tid)
    assert cases, f"no open case for {tid}"
    return cases[0]


# ── Demo dataset, end to end ──────────────────────────────────────────────────

def test_demo_outcomes_match_ground_truth(reconciled_demo):
    ground_truth, stats = reconciled_demo
    assert stats["total"] == 30
    wrong = [(g["transaction_id"], g["scenario"], g["expected_outcome"], _status(g["transaction_id"]))
             for g in ground_truth if _status(g["transaction_id"]) != g["expected_outcome"]]
    assert not wrong, wrong


def test_every_review_case_has_a_proposal(reconciled_demo):
    from database.repositories import get_all_transactions
    for txn in get_all_transactions("review"):
        assert _allocs(txn["transaction_id"], "proposed"), txn["transaction_id"]


def test_auto_matches_settle_invoices(reconciled_demo):
    from database.repositories import get_invoice
    alloc = _allocs("TXN-001", "approved")[0]
    assert get_invoice(alloc["invoice_id"])["status"] == "settled"
    assert "INV-001" in alloc["decision_reason"]


def test_bulk_payment_split_across_invoices(reconciled_demo):
    ground_truth, _ = reconciled_demo
    gt = next(g for g in ground_truth if g["scenario"] == "bulk_payment")
    proposed = _allocs(gt["transaction_id"], "proposed")
    assert sorted(a["invoice_id"] for a in proposed) == sorted(gt["invoice_ids"])
    from database.repositories import get_transaction
    assert sum(a["allocated_amount"] for a in proposed) == pytest.approx(get_transaction(gt["transaction_id"])["credit_amount"])
    assert _open_case(gt["transaction_id"])["exception_type"] == "bulk_payment"


def test_duplicate_utr_flagged_but_split_payment_is_not(reconciled_demo):
    ground_truth, _ = reconciled_demo
    copy = next(g for g in ground_truth if g["scenario"] == "duplicate_utr_copy")
    orig = next(g for g in ground_truth if g["scenario"] == "duplicate_utr_original")
    assert _status(copy["transaction_id"]) == "duplicate"
    assert _status(orig["transaction_id"]) == "matched"
    assert _open_case(copy["transaction_id"])["exception_type"] == "possible_duplicate"
    for sc in ("multi_payment_1", "multi_payment_2"):
        tid = next(g for g in ground_truth if g["scenario"] == sc)["transaction_id"]
        assert _status(tid) != "duplicate"


def test_tds_case_carries_section_and_amount(reconciled_demo):
    from database.repositories import get_settlement_components
    ground_truth, _ = reconciled_demo
    gt = next(g for g in ground_truth if g["scenario"] == "tds_deduction")
    assert _open_case(gt["transaction_id"])["exception_type"] == "possible_tds_deduction"
    alloc = _allocs(gt["transaction_id"], "proposed")[0]
    tds = [c for c in get_settlement_components(alloc["allocation_id"]) if c["component_type"] == "tds"]
    assert tds and tds[0]["amount"] == pytest.approx(gt["tds_amount"])
    assert tds[0]["tds_section"] == "194J"
    assert tds[0]["tds_status"] == "evidence_pending"      # rate correct, certificate missing


def test_rerunning_batch_is_idempotent(reconciled_demo):
    from decision.decision_engine import run_batch_reconciliation
    from database.repositories import get_open_review_cases
    before = len(get_open_review_cases())
    stats = run_batch_reconciliation(use_llm=False)
    assert stats["matched"] == 0
    assert len(get_open_review_cases()) == before          # old cases superseded, not duplicated


# ── Review actions ────────────────────────────────────────────────────────────

def test_approve_review_case(reconciled_demo):
    from decision.review_manager import approve_match
    from database.repositories import get_invoice
    ground_truth, _ = reconciled_demo
    tid = next(g for g in ground_truth if g["scenario"] == "bulk_payment")["transaction_id"]
    case = _open_case(tid)
    approve_match(case["case_id"], None, "tester", "ok")
    assert _status(tid) == "matched"
    for a in _allocs(tid, "approved"):
        assert get_invoice(a["invoice_id"])["status"] == "settled"
    from database.repositories import get_review_case
    assert get_review_case(case["case_id"])["status"] == "approved"


def test_reject_then_reconcile_excludes_rejected_invoice(reconciled_demo):
    from decision.review_manager import reject_match
    from decision.decision_engine import reconcile_transaction
    ground_truth, _ = reconciled_demo
    tid = next(g for g in ground_truth if g["scenario"] == "missing_remittance")["transaction_id"]
    first = _allocs(tid, "proposed")[0]["invoice_id"]
    reject_match(_open_case(tid)["case_id"], None, "tester", "wrong invoice")
    assert _status(tid) == "unmatched"
    res = reconcile_transaction(tid, use_llm=False)
    assert res.get("invoice_id") != first


def test_correct_to_multiple_invoices_with_tds(reconciled_demo):
    from decision.review_manager import correct_match
    from database.repositories import get_invoice, get_transaction, get_settlement_components
    ground_truth, _ = reconciled_demo
    tid = next(g for g in ground_truth if g["scenario"] == "tds_deduction")["transaction_id"]
    gt = next(g for g in ground_truth if g["transaction_id"] == tid)
    res = correct_match(_open_case(tid)["case_id"], tid, [gt["invoice_id"]], "tester", "per Form 16A",
                        tds_amount=gt["tds_amount"], tds_section="194J")
    assert _status(tid) == "matched"
    assert get_invoice(gt["invoice_id"])["status"] == "settled"
    comps = get_settlement_components(res["new_allocation_id"])
    assert {c["component_type"] for c in comps} == {"bank_credit", "tds"}
    assert sum(c["amount"] for c in comps) == pytest.approx(get_transaction(tid)["credit_amount"] + gt["tds_amount"])


def test_reverse_reopens_invoice_and_excludes_it_next_time(reconciled_demo):
    from decision.review_manager import reverse_transaction
    from decision.decision_engine import evaluate_transaction
    from database.repositories import get_invoice
    res = reverse_transaction("TXN-001", "tester", "posted to wrong customer")
    assert _status("TXN-001") == "unmatched"
    assert get_invoice("INV-001")["status"] == "open"
    ev = evaluate_transaction("TXN-001", use_llm=False)
    assert all(c["invoice"]["invoice_id"] != "INV-001" for c in ev["ranked"])


def test_dismiss_marks_non_receivable(reconciled_demo):
    from decision.review_manager import dismiss_transaction
    from database.repositories import get_reconciliation_summary
    tid = "TXN-011"                                         # unknown payer
    dismiss_transaction(_open_case(tid)["case_id"], "tester", "bank interest")
    assert _status(tid) == "excluded"
    assert get_reconciliation_summary()["excluded"] == 1


def test_release_duplicate_reconciles(reconciled_demo):
    from decision.review_manager import release_duplicate, confirm_duplicate
    ground_truth, _ = reconciled_demo
    tid = next(g for g in ground_truth if g["scenario"] == "duplicate_utr_copy")["transaction_id"]
    res = release_duplicate(_open_case(tid)["case_id"], "tester", "genuinely paid twice")
    assert res["reconcile_result"]["outcome"] != "duplicate"
    assert _status(tid) != "duplicate"


def test_approve_twice_is_refused(reconciled_demo):
    from decision.review_manager import approve_match, ReviewError
    ground_truth, _ = reconciled_demo
    tid = next(g for g in ground_truth if g["scenario"] == "overpayment")["transaction_id"]
    case = _open_case(tid)
    approve_match(case["case_id"], None, "tester")
    with pytest.raises(ReviewError):
        approve_match(case["case_id"], None, "tester")
    from database.repositories import get_invoice
    inv = _allocs(tid, "approved")[0]["invoice_id"]
    assert get_invoice(inv)["status"] == "overpaid"


def test_tds_verify_and_reject(reconciled_demo):
    from decision.review_manager import approve_match, verify_tds, reject_tds
    from database.repositories import get_tds_components, get_invoice
    ground_truth, _ = reconciled_demo
    gt = next(g for g in ground_truth if g["scenario"] == "tds_deduction")
    approve_match(_open_case(gt["transaction_id"])["case_id"], None, "tester")
    comp = get_tds_components()[0]
    verify_tds(comp["component_id"], "tester", None, "26AS checked")
    assert get_tds_components("verified")
    reject_tds(comp["component_id"], "tester", "no certificate after all")
    inv = get_invoice(gt["invoice_id"])
    assert inv["status"] == "partially_paid"
    assert inv["outstanding_amount"] == pytest.approx(gt["tds_amount"])


# ── Ingestion ─────────────────────────────────────────────────────────────────

BANK_CSV = (
    "Account Statement for 9876543210\n"
    "Date,Narration,UTR,Credit Amount,Debit Amount\n"
    "05-11-2026,NEFT/ACME TRADERS/INV-501,HDFCN52026110500001,59000,\n"
    "06-11-2026,ATM WDL,,,2000\n"
).encode()

INVOICE_CSV = (
    "Invoice No,Customer Name,Customer GSTIN,Invoice Date,Due Date,Taxable Amount,GST Amount,Total Amount\n"
    "INV-501,Acme Traders Pvt Ltd,27AABCA1111A1Z5,2026-10-01,2026-10-31,50000,9000,59000\n"
    "INV-502,Acme Traders Pvt Ltd,27AABCA1111A1Z5,2026-10-05,2026-11-04,20000,3600,23600\n"
).encode()


def test_bank_and_invoice_csv_roundtrip(db):
    from extraction.ingestion import ingest_bank_statement, ingest_invoice
    from decision.decision_engine import run_batch_reconciliation
    from database.repositories import get_reconciliation_summary, get_invoice
    inv = ingest_invoice("register.csv", INVOICE_CSV)
    assert inv["success"] and inv["invoices_imported"] == 2
    bank = ingest_bank_statement("stmt.csv", BANK_CSV, bank_name="HDFC")
    assert bank["success"] and bank["credits_imported"] == 1 and bank["debits_excluded"] == 1
    stats = run_batch_reconciliation(use_llm=False)
    assert stats == {**stats, "total": 1, "matched": 1}
    assert get_invoice("INV-501")["status"] == "settled"
    s = get_reconciliation_summary()
    assert s["total_transactions"] == 1 and s["excluded"] == 1


def test_same_file_twice_is_rejected_and_invoices_never_reset(db):
    from extraction.ingestion import ingest_invoice, ingest_bank_statement
    from decision.decision_engine import run_batch_reconciliation
    from database.repositories import get_invoice, insert_invoice
    ingest_invoice("register.csv", INVOICE_CSV)
    ingest_bank_statement("stmt.csv", BANK_CSV)
    run_batch_reconciliation(use_llm=False)
    again = ingest_invoice("register.csv", INVOICE_CSV)
    assert again["success"] is False and again.get("duplicate_file")
    # a *different* file carrying an existing invoice number must not reset its balance
    other = INVOICE_CSV.replace(b"INV-502", b"INV-503")
    res = ingest_invoice("register_v2.csv", other)
    assert res["invoices_imported"] == 1 and any("INV-501" in s for s in res["skipped"])
    insert_invoice({"invoice_id": "INV-501", "total_amount": 59000})        # direct re-insert: no-op
    assert get_invoice("INV-501")["status"] == "settled"


def test_remittance_eml_rules_and_amount_link(db):
    from extraction.ingestion import ingest_invoice, ingest_bank_statement, ingest_remittance
    from decision.decision_engine import run_batch_reconciliation
    ingest_invoice("register.csv", INVOICE_CSV)
    bank = ("Date,Narration,Credit Amount\n"
            "07-11-2026,NEFT/CMS PAYOUT,21200\n").encode()          # no UTR, no ref, no payer
    ingest_bank_statement("stmt2.csv", bank)
    eml = ("From: ap@acme.example\nTo: ar@us.example\nSubject: Payment advice\n"
           "Content-Type: text/plain\n\n"
           "Dear team,\nWe have paid Rs. 21,200.00 on 07-11-2026 against Invoice INV-502.\n"
           "TDS of Rs. 2,400.00 deducted u/s 194J.\nRegards,\nAcme Traders Pvt Ltd\n").encode()
    rem = ingest_remittance("advice.eml", eml)
    assert rem["success"], rem
    ex = rem["extracted"]
    assert ex["invoice_references"] == ["INV-502"] and ex["tds_amount"] == 2400 and ex["tds_section"] == "194J"
    assert ex["payment_amount"] == 21200 and ex["utr"] is None
    run_batch_reconciliation(use_llm=False)
    from database.repositories import get_all_transactions
    txn = get_all_transactions()[0]
    assert txn["status"] == "review"                          # TDS needs a certificate
    assert _allocs(txn["transaction_id"], "proposed")[0]["invoice_id"] == "INV-502"


def test_auto_ingest_routes_by_content(db):
    from extraction.ingestion import auto_ingest
    assert auto_ingest("a.csv", BANK_CSV)["detected_type"] == "bank_statement"
    assert auto_ingest("b.csv", INVOICE_CSV)["detected_type"] == "invoice"
    r = auto_ingest("c.txt", b"We have transferred Rs. 5,000.00 against INV-501. UTR No: HDFCN52026110599999")
    assert r["detected_type"] == "remittance" and r["success"]


def test_demo_loader_is_idempotent(db):
    from extraction.ingestion import load_synthetic_dataset
    from database.repositories import get_all_transactions, get_all_remittances
    first = load_synthetic_dataset()
    second = load_synthetic_dataset()
    assert second["already_loaded"] is True
    assert len(get_all_transactions()) == first["transactions"]
    assert len(get_all_remittances()) == first["remittances_stored"]


# ── Migration ─────────────────────────────────────────────────────────────────

def test_migrates_v1_database_with_unique_utr(tmp_path):
    from database.db import set_database_path, initialise_database, close_connection
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE bank_transactions (transaction_id TEXT PRIMARY KEY, utr TEXT UNIQUE, narration TEXT,
            credit_amount REAL NOT NULL DEFAULT 0, debit_amount REAL NOT NULL DEFAULT 0, transaction_date TEXT,
            bank_name TEXT, account_number TEXT, currency TEXT NOT NULL DEFAULT 'INR',
            status TEXT NOT NULL DEFAULT 'unmatched', batch_id TEXT, source_document_id TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')));
        INSERT INTO bank_transactions(transaction_id, utr, credit_amount) VALUES ('T1', 'UTR1', 100);
        CREATE TABLE remittance_records (remittance_id TEXT PRIMARY KEY, document_id TEXT, customer_name TEXT,
            invoice_references TEXT, payment_amount REAL, tds_amount REAL DEFAULT 0, utr TEXT, payment_date TEXT,
            currency TEXT DEFAULT 'INR', evidence_text TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')));
    """)
    con.commit()
    con.close()
    try:
        set_database_path(path)
        initialise_database()
        from database.repositories import insert_transaction, get_all_transactions, insert_remittance
        insert_transaction({"transaction_id": "T2", "utr": "UTR1", "credit_amount": 100})
        assert [t["transaction_id"] for t in get_all_transactions()] == ["T1", "T2"]
        insert_remittance({"utr": "X", "tds_section": "194C", "customer_gstin": "27AABCA1111A1Z5"})
    finally:
        close_connection()
        set_database_path(None)


# ── Extraction & matching units ───────────────────────────────────────────────

def test_rule_invoice_extraction():
    from extraction.rule_extractor import extract_invoice_rules
    text = """TAX INVOICE
    Supplier: ReconcileAI Demo Supplier Pvt Ltd   GSTIN: 29AABCR0000R1Z1
    Invoice No: INV-7781
    Invoice Date: 03/10/2026
    Due Date: 02/11/2026
    Bill To: Nexus Infotech Pvt Ltd
    GSTIN: 33AABCN3456D4Z7
    Taxable Amount: 1,00,000.00
    IGST @18%: 18,000.00
    Grand Total: ₹1,18,000.00"""
    r = extract_invoice_rules(text)
    assert r["invoice_id"] == "INV-7781"
    assert r["customer_name"] == "Nexus Infotech Pvt Ltd"
    assert r["customer_gstin"] == "33AABCN3456D4Z7"
    assert (r["taxable_amount"], r["gst_amount"], r["total_amount"]) == (100000.0, 18000.0, 118000.0)
    assert r["invoice_date"] == "2026-10-03" and r["due_date"] == "2026-11-02"
    assert r["extraction_status"] == "complete"


@pytest.mark.parametrize("narration,refs,payer", [
    ("NEFT/XYZ/INV-001", ["INV-001"], "XYZ"),
    ("NEFT-HDFC0001234-APEX SOLUTIONS LTD-INV 0012", ["INV-0012"], "APEX SOLUTIONS LTD"),
    ("RTGS/HDFCR52024101500123/GLOBAL TRADE/PAYMENT", [], "GLOBAL TRADE"),
    ("BY TRANSFER-IMPS-ORBIT LOG-INV/0045", ["INV-0045"], "ORBIT LOG"),
    ("NEFT/123456789012/INV-0012", ["INV-0012"], None),
    ("NEFT/ORBIT/OCT PAYMENT", [], "ORBIT"),
])
def test_narration_parser(narration, refs, payer):
    from matching.candidate_generator import _extract_from_narration
    assert _extract_from_narration(narration) == (refs, payer)


def test_implied_tds_detection():
    from validation.financial_rules import infer_tds_deduction
    inv = {"total_amount": 118000, "outstanding_amount": 118000, "taxable_amount": 100000}
    hit = infer_tds_deduction(116000, inv)                   # 2 % of taxable (194C)
    assert hit and hit["sections"] == ["194C"] and hit["basis"] == "taxable value"
    assert infer_tds_deduction(60000, inv) is None            # plain part-payment


def test_tds_on_taxable_value_is_accepted():
    from validation.financial_rules import validate_tds_claim
    r = validate_tds_claim(10000, 118000, "194J", taxable_amount=100000)
    assert r["valid"] and r["tds_status"] == "evidence_pending"


# ── Reports ───────────────────────────────────────────────────────────────────

def test_report_workbook_and_aging(reconciled_demo):
    from openpyxl import load_workbook
    from datetime import date
    from reporting.report_generator import build_report_workbook, aging_report, aging_summary, customer_balances
    wb = load_workbook(io.BytesIO(build_report_workbook()))
    for sheet in ("Summary", "Transactions", "Allocations", "Aging", "Customers", "TDS Register", "Audit Log"):
        assert sheet in wb.sheetnames
    df = aging_report(date(2027, 3, 1))
    assert not df.empty and set(df["bucket"]) <= {"Not due", "1–30", "31–60", "61–90", "90+"}
    assert aging_summary(date(2027, 3, 1))["Total"].sum() == pytest.approx(df["outstanding_amount"].sum())
    assert customer_balances()["received"].sum() > 0


@pytest.mark.parametrize("question,fn", [
    ("which payments are unmatched?", "get_unmatched_transactions"),
    ("show open review cases", "get_open_review_cases"),
    ("status of INV-005", "get_invoice"),
    ("what is overdue?", "get_aging"),
    ("pending TDS", "get_tds_register"),
    ("give me a summary", "get_reconciliation_summary"),
])
def test_finance_assistant_routes_offline(reconciled_demo, question, fn):
    from reporting.report_generator import answer_finance_query
    res = answer_finance_query(question)
    assert res["backend_function"] == fn and res["answer"] and res["used_llm"] is False


# ── Trainer replay == real engine ─────────────────────────────────────────────

def test_trainer_replay_agrees_with_engine(db):
    """The trainer optimises a vectorised copy of the decision gates; it must match the engine."""
    from data.synthetic.generate_training_data import generate
    from training import train_matcher as tm
    from decision.decision_engine import evaluate_transaction
    from database.db import get_connection, reset_database
    from database.repositories import insert_invoice, insert_transaction, insert_remittance
    learned = json.loads(tm.T.LEARNED_PARAMS_PATH.read_text())["params"] if tm.T.LEARNED_PARAMS_PATH.exists() \
        else tm.T.DEFAULT_PARAMS
    import config.thresholds as T
    saved = {k: getattr(T, k) for k in ("AUTO_MATCH_THRESHOLD", "REVIEW_THRESHOLD", "AMBIGUITY_MARGIN",
                                        "CUSTOMER_NAME_FUZZY_MIN")}
    saved_w = dict(T.SCORE_WEIGHTS)
    T.AUTO_MATCH_THRESHOLD, T.REVIEW_THRESHOLD = learned["auto_match_threshold"], learned["review_threshold"]
    T.AMBIGUITY_MARGIN, T.CUSTOMER_NAME_FUZZY_MIN = learned["ambiguity_margin"], learned["customer_name_fuzzy_min"]
    T.SCORE_WEIGHTS.clear(); T.SCORE_WEIGHTS.update(learned["score_weights"])
    import matching.candidate_generator as cg
    real_inv, real_cust = cg.get_all_invoices, cg.get_all_customers
    try:
        batches = generate(4, seed=11)
        D = tm.extract(batches)
        cg.get_all_invoices, cg.get_all_customers = real_inv, real_cust       # extract() patches these
        import matching.fuzzy_match as fm
        fm._thresholds.CUSTOMER_NAME_FUZZY_MIN = learned["customer_name_fuzzy_min"]
        replay = tm.decide(D, learned)["outcome"]
        names = ("matched", "review", "unmatched")
        i = 0
        mismatches = []
        for b in batches:
            reset_database()
            conn = get_connection()
            for c in b["customers"]:
                conn.execute("INSERT INTO customers(customer_id,name,gstin) VALUES(?,?,?)",
                             (c["customer_id"], c["name"], c["gstin"]))
            conn.commit()
            for inv in b["invoices"]:
                insert_invoice(inv)
            for r in b["remittances"]:
                insert_remittance(r)
            for t in b["transactions"]:
                insert_transaction(t)
            for t in b["transactions"]:
                ev = evaluate_transaction(t["transaction_id"], use_llm=False)
                if ev["outcome"] != "duplicate" and ev["outcome"] != names[replay[i]]:
                    mismatches.append((t["narration"], ev["outcome"], names[replay[i]]))
                i += 1
        assert not mismatches, mismatches[:10]
    finally:
        cg.get_all_invoices, cg.get_all_customers = real_inv, real_cust
        for k, v in saved.items():
            setattr(T, k, v)
        T.SCORE_WEIGHTS.clear(); T.SCORE_WEIGHTS.update(saved_w)


def test_pdf_invoice_ingestion_without_llm(db):
    import pymupdf
    from extraction.ingestion import ingest_invoice
    from database.repositories import get_invoice
    doc = pymupdf.open()
    page = doc.new_page()
    lines = ["TAX INVOICE", "Supplier: ReconcileAI Demo Supplier Pvt Ltd", "Invoice No: INV-9001",
             "Invoice Date: 2026-10-10", "Due Date: 2026-11-09", "Bill To: Zeta Consultants",
             "GSTIN: 36AABCZ1234H8Z6", "Taxable Amount: 50,000.00", "CGST @9%: 4,500.00",
             "SGST @9%: 4,500.00", "Grand Total: 59,000.00"]
    for k, line in enumerate(lines):
        page.insert_text((72, 72 + 18 * k), line)
    pdf = doc.tobytes()
    res = ingest_invoice("inv9001.pdf", pdf)
    assert res["success"], res
    inv = get_invoice("INV-9001")
    assert inv["customer_name"] == "Zeta Consultants"
    assert (inv["gst_amount"], inv["total_amount"], inv["due_date"]) == (9000.0, 59000.0, "2026-11-09")


def test_bare_number_reference_from_llm_or_remittance():
    from matching.exact_match import find_exact_invoice_match
    invs = [{"invoice_id": "INV-0045"}, {"invoice_id": "INV-0046"}, {"invoice_id": "CN-0046"}]
    assert find_exact_invoice_match("0045", invs) == []                       # narration regex path: no
    assert find_exact_invoice_match("0045", invs, allow_numeric=True) == [invs[0]]
    assert find_exact_invoice_match("46", invs, allow_numeric=True) == []     # < 3 digits
    assert find_exact_invoice_match("0046", invs, allow_numeric=True) == []   # ambiguous (two invoices)


def test_migration_closes_cases_on_resolved_credits(reconciled_demo):
    from database.db import get_connection, initialise_database
    from database.repositories import get_open_cases_for_transaction
    conn = get_connection()
    for tid in ("TXN-001", "TXN-017"):          # matched credits with a leftover open case (old engine)
        conn.execute("INSERT INTO review_cases(case_id,transaction_id,exception_type,status,created_at) "
                     "VALUES(?,?,'amount_discrepancy','open',datetime('now'))", (f"OLD-{tid}", tid))
    conn.commit()
    initialise_database()
    assert not get_open_cases_for_transaction("TXN-001") and not get_open_cases_for_transaction("TXN-017")
    assert get_open_cases_for_transaction("TXN-002")      # a credit still in review keeps its case
