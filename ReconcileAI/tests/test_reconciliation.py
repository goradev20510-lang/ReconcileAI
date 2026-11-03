"""
ReconcileAI – Test Suite
Tests extraction, matching, validation and reconciliation against the synthetic dataset.
Run: pytest tests/ -v
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from decimal import Decimal

# ── Extraction tests ──────────────────────────────────────────────────────────

def test_bank_csv_parser():
    """Bank parser must handle well-formed CSV correctly."""
    import tempfile, csv
    from extraction.bank_parser import read_bank_csv
    rows = [
        ["Date", "Narration", "UTR", "Credit Amount", "Debit Amount"],
        ["01-11-2026", "NEFT/XYZTECH/INV-001", "AXIS123456789012", "118000", "0"],
        ["02-11-2026", "IMPS/APEXSOL/PAYMENT", "HDFC987654321098", "0", "5000"],
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)
        tmp = f.name
    txns = read_bank_csv(tmp)
    assert len(txns) >= 1
    assert txns[0]["credit_amount"] == 118000.0
    assert txns[0]["utr"] == "AXIS123456789012"


def test_document_classifier_invoice():
    from extraction.document_classifier import classify_document_by_text
    text = "TAX INVOICE\nGSTIN: 27AABCX1234A1Z5\nInvoice No: INV-001\nTaxable Amount: 90000\nGST: 10000\nTotal: 100000"
    result = classify_document_by_text(text)
    assert result["document_type"] == "invoice"
    assert result["confidence"] > 0.3


def test_document_classifier_bank_statement():
    from extraction.document_classifier import classify_document_by_text
    text = "Account Statement\nDate Narration Credit Debit Balance\n01-11-2026 NEFT/XYZTECH 118000 0 500000"
    result = classify_document_by_text(text)
    assert result["document_type"] == "bank_statement"


def test_document_classifier_remittance():
    from extraction.document_classifier import classify_document_by_text
    text = "We have transferred 98000 against INV-101. TDS of 2000 has been deducted. UTR No: AXIS123."
    result = classify_document_by_text(text)
    assert result["document_type"] == "remittance"


# ── Normalisation tests ───────────────────────────────────────────────────────

def test_fuzzy_customer_match():
    from matching.fuzzy_match import fuzzy_match_customer
    customers = [
        {"customer_id": "CUST01", "name": "XYZ Technologies Pvt Ltd"},
        {"customer_id": "CUST02", "name": "Apex Solutions Ltd"},
    ]
    # All these should match CUST01
    for variant in ["XYZ Tech", "XYZ Technologies", "XYZ PVT LTD", "xyz technologies pvt. ltd."]:
        results = fuzzy_match_customer(variant, customers, threshold=60)
        assert len(results) > 0, f"Expected match for '{variant}'"
        assert results[0]["customer_id"] == "CUST01", f"Wrong match for '{variant}'"


def test_exact_invoice_match_normalisation():
    from matching.exact_match import find_exact_invoice_match
    invoices = [{"invoice_id": "INV-101", "status": "open", "total_amount": 100000, "outstanding_amount": 100000}]
    # All these should match INV-101
    for ref in ["INV-101", "INV101", "inv-101", "INV 101", "INV-0101"]:
        results = find_exact_invoice_match(ref, invoices)
        assert len(results) == 1, f"Expected match for '{ref}', got {results}"


# ── Financial validation tests ────────────────────────────────────────────────

def test_validate_exact_payment():
    from validation.financial_rules import validate_payment_amount
    # Full payment: credit = 100000, TDS = 0, invoice = 100000
    result = validate_payment_amount(100000, 0, 100000, 100000)
    assert result["valid"] is True
    assert result["is_partial"] is False
    assert result["is_overpayment"] is False


def test_validate_tds_payment():
    from validation.financial_rules import validate_payment_amount
    # Payment with 10% TDS: credit = 90000, TDS = 10000, invoice = 100000
    result = validate_payment_amount(90000, 10000, 100000, 100000)
    assert result["valid"] is True
    assert result["is_partial"] is False
    assert result["effective_payment"] == 100000.0


def test_validate_partial_payment():
    from validation.financial_rules import validate_payment_amount
    result = validate_payment_amount(60000, 0, 100000, 100000)
    assert result["is_partial"] is True
    assert result["valid"] is True  # Partial is still a valid allocation


def test_validate_overpayment():
    from validation.financial_rules import validate_payment_amount
    result = validate_payment_amount(110000, 0, 100000, 100000)
    assert result["is_overpayment"] is True


def test_validate_tds_claim_correct_rate():
    from validation.financial_rules import validate_tds_claim
    # 10% TDS on 100000 = 10000
    result = validate_tds_claim(10000, 100000, tds_section="194J")
    assert result["valid"] is True
    assert result["tds_status"] == "evidence_pending"  # No cert attached


def test_validate_tds_claim_wrong_rate():
    from validation.financial_rules import validate_tds_claim
    # Claimed 20000 (20%) but 194J is 10%
    result = validate_tds_claim(20000, 100000, tds_section="194J")
    assert result["valid"] is False


def test_outstanding_calculation():
    from validation.financial_rules import calculate_outstanding
    allocs = [{"allocated_amount": 40000}, {"allocated_amount": 30000}]
    outstanding = calculate_outstanding(100000, allocs)
    assert outstanding == 30000.0


# ── Duplicate detection tests ─────────────────────────────────────────────────

def test_duplicate_utr_detection():
    from validation.duplicate_detection import detect_duplicate_transaction
    existing = [
        {"transaction_id": "TXN-001", "utr": "AXIS123456789012",
         "credit_amount": 100000, "narration": "NEFT/XYZ", "transaction_date": "2026-11-01"}
    ]
    new_txn = {
        "transaction_id": "TXN-002", "utr": "AXIS123456789012",
        "credit_amount": 100000, "narration": "NEFT/XYZ", "transaction_date": "2026-11-01"
    }
    result = detect_duplicate_transaction(new_txn, existing)
    assert result["is_duplicate"] is True
    assert result["duplicate_of"] == "TXN-001"


def test_non_duplicate_different_utr():
    from validation.duplicate_detection import detect_duplicate_transaction
    existing = [
        {"transaction_id": "TXN-001", "utr": "AXIS111111111111",
         "credit_amount": 100000, "narration": "NEFT/XYZ", "transaction_date": "2026-11-01"}
    ]
    new_txn = {
        "transaction_id": "TXN-002", "utr": "HDFC222222222222",
        "credit_amount": 100000, "narration": "NEFT/ABC", "transaction_date": "2026-11-02"
    }
    result = detect_duplicate_transaction(new_txn, existing)
    assert result["is_duplicate"] is False


# ── Scoring tests ─────────────────────────────────────────────────────────────

def test_score_exact_match_should_be_high():
    from matching.scoring import score_candidates
    txn = {"transaction_id": "TXN-001", "credit_amount": 118000,
            "transaction_date": "2026-11-15"}
    candidates = [{
        "invoice": {
            "invoice_id": "INV-001", "total_amount": 118000,
            "outstanding_amount": 118000, "due_date": "2026-11-30",
            "customer_id": "CUST01", "status": "open",
        },
        "signals": {
            "invoice_ref_exact": True, "customer_match": True,
            "amount_match": True, "tds_amount": 0,
            "credit_amount": 118000, "effective_payment": 118000,
            "fuzzy_score": 100,
        }
    }]
    scored = score_candidates(txn, candidates)
    from config.thresholds import AUTO_MATCH_THRESHOLD
    assert scored[0]["score"] >= AUTO_MATCH_THRESHOLD


def test_score_no_signals_should_be_low():
    from matching.scoring import score_candidates
    txn = {"transaction_id": "TXN-002", "credit_amount": 50000}
    candidates = [{
        "invoice": {
            "invoice_id": "INV-999", "total_amount": 99999,
            "outstanding_amount": 99999, "due_date": None,
            "customer_id": "CUST99", "status": "open",
        },
        "signals": {
            "invoice_ref_exact": False, "customer_match": False,
            "amount_match": False, "tds_amount": 0,
            "credit_amount": 50000, "effective_payment": 50000,
        }
    }]
    scored = score_candidates(txn, candidates)
    from config.thresholds import REVIEW_THRESHOLD
    assert scored[0]["score"] < min(0.15, REVIEW_THRESHOLD)


# ── Database integration tests ────────────────────────────────────────────────

def test_database_insert_and_retrieve(db):
    """Basic round-trip: insert an invoice, read it back."""
    from database.repositories import insert_invoice, get_invoice
    inv_data = {
        "invoice_id": "TEST-INV-001", "customer_name": "Test Corp",
        "total_amount": 59000, "outstanding_amount": 59000,
        "taxable_amount": 50000, "gst_amount": 9000, "currency": "INR", "status": "open",
    }
    iid = insert_invoice(inv_data)
    assert iid == "TEST-INV-001"
    retrieved = get_invoice("TEST-INV-001")
    assert retrieved["total_amount"] == 59000.0


# ── Scenario integration test ─────────────────────────────────────────────────

def test_synthetic_dataset_loads():
    """The synthetic dataset generator must produce valid data."""
    import random
    random.seed(42)
    from data.synthetic.generate_dataset import generate_invoices, generate_bank_transactions, CUSTOMERS
    invoices = generate_invoices()
    assert len(invoices) == 40
    bank_txns, ground_truth = generate_bank_transactions(invoices)
    assert len(bank_txns) == 30
    assert len(ground_truth) >= 15
    # Every invoice must have a total_amount > 0
    for inv in invoices:
        assert inv["total_amount"] > 0
    # Check all 15 scenarios are present
    scenarios = {g["scenario"] for g in ground_truth}
    required = {"exact_reference", "name_variation", "partial_payment",
                "tds_deduction", "bulk_payment", "duplicate_utr_copy",
                "unknown_customer", "overpayment"}
    assert required.issubset(scenarios), f"Missing scenarios: {required - scenarios}"
