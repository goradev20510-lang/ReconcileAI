"""
ReconcileAI – Synthetic Dataset Generator
Creates realistic test data covering all 15 reconciliation scenarios.
Run: python -m data.synthetic.generate_dataset
"""
from __future__ import annotations
import csv
import json
import random
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent

# ── Master Customer List ──────────────────────────────────────────────────────
CUSTOMERS = [
    {"id": "CUST01", "name": "XYZ Technologies Pvt Ltd",       "gstin": "27AABCX1234A1Z5", "variants": ["XYZ Tech", "XYZ Technologies", "XYZ PVT LTD", "XYZ TECHNOLOGIES PVT. LTD."]},
    {"id": "CUST02", "name": "Apex Solutions Ltd",              "gstin": "29AABCA5678B2Z3", "variants": ["Apex Solutions", "APEX SOLUTIONS LTD", "Apex Sol Ltd"]},
    {"id": "CUST03", "name": "Bharat Enterprises",             "gstin": "07AABCB9012C3Z1", "variants": ["Bharat Entr", "BHARAT ENTERPRISES", "Bharat Ent."]},
    {"id": "CUST04", "name": "Nexus Infotech Pvt Ltd",         "gstin": "33AABCN3456D4Z7", "variants": ["Nexus Infotech", "NEXUS INFO", "Nexus IT"]},
    {"id": "CUST05", "name": "Sterling Global Corporation",    "gstin": "19AABCS7890E5Z9", "variants": ["Sterling Global", "STERLING CORP", "Sterling Glob Corp"]},
    {"id": "CUST06", "name": "Orbit Logistics Pvt Ltd",        "gstin": "24AABCO2345F6Z2", "variants": ["Orbit Logistics", "ORBIT LOG", "Orbit Logi Pvt"]},
    {"id": "CUST07", "name": "Prime Systems Ltd",              "gstin": "06AABCP6789G7Z4", "variants": ["Prime Systems", "PRIME SYS LTD", "Prime Sys"]},
    {"id": "CUST08", "name": "Zeta Consultants",               "gstin": "36AABCZ1234H8Z6", "variants": ["Zeta Consult", "ZETA CONSULTANTS", "Zeta & Co"]},
    {"id": "CUST09", "name": "Meridian Healthcare Ltd",        "gstin": "21AABCM5678I9Z8", "variants": ["Meridian Health", "MERIDIAN HEALTHCARE"]},
    {"id": "CUST10", "name": "Vantage Retail Pvt Ltd",         "gstin": "22AABCV9012J1Z0", "variants": ["Vantage Retail", "VANTAGE RETAIL"]},
    {"id": "CUST11", "name": "Innovatech Software Solutions",  "gstin": "32AABCI3456K2Z3", "variants": ["Innovatech", "INNOVATECH SOFTWARE"]},
    {"id": "CUST12", "name": "Global Trade Partners",          "gstin": "09AABCG7890L3Z5", "variants": ["Global Trade", "GTP"]},
    {"id": "CUST13", "name": "Cascade Infrastructure Ltd",    "gstin": "10AABCC2345M4Z7", "variants": ["Cascade Infra", "CASCADE INFRA"]},
    {"id": "CUST14", "name": "Pinnacle Analytics Pvt Ltd",    "gstin": "16AABCP6789N5Z9", "variants": ["Pinnacle Analytics", "PINNACLE ANALYTICS"]},
    {"id": "CUST15", "name": "Unknown Payer Co",               "gstin": None,               "variants": []},
]

SUPPLIER = "ReconcileAI Demo Supplier Pvt Ltd"
BASE_DATE = date(2026, 10, 1)


def _rand_date(start_offset=0, end_offset=60) -> date:
    return BASE_DATE + timedelta(days=random.randint(start_offset, end_offset))


def _inv_id(n: int) -> str:
    return f"INV-{n:03d}"


def _utr() -> str:
    prefix = random.choice(["AXIS", "HDFC", "SBI", "ICICI", "KOTAK"])
    return f"{prefix}{random.randint(10**11, 10**12 - 1)}"


# ── Invoice Generator ─────────────────────────────────────────────────────────

def generate_invoices() -> list[dict]:
    invoices = []
    inv_n = 1

    for cust in CUSTOMERS[:14]:  # CUST15 = unknown payer, no invoices
        for _ in range(random.randint(2, 3)):
            taxable = Decimal(str(random.choice([50000, 75000, 90000, 100000, 120000, 150000, 200000])))
            gst_rate = Decimal("0.18")
            gst = (taxable * gst_rate).quantize(Decimal("1"))
            total = taxable + gst
            inv_date = _rand_date(0, 30)
            due_date = inv_date + timedelta(days=30)
            invoices.append({
                "invoice_id":        _inv_id(inv_n),
                "customer_id":       cust["id"],
                "supplier_name":     SUPPLIER,
                "customer_name":     cust["name"],
                "customer_gstin":    cust["gstin"],
                "invoice_date":      inv_date.isoformat(),
                "due_date":          due_date.isoformat(),
                "taxable_amount":    float(taxable),
                "gst_amount":        float(gst),
                "total_amount":      float(total),
                "outstanding_amount": float(total),
                "currency":          "INR",
                "status":            "open",
            })
            inv_n += 1

    # Ensure we have exactly 40 invoices
    while len(invoices) < 40:
        cust = random.choice(CUSTOMERS[:10])
        taxable = Decimal(str(random.choice([60000, 80000, 110000])))
        gst = (taxable * Decimal("0.18")).quantize(Decimal("1"))
        total = taxable + gst
        inv_date = _rand_date(0, 20)
        due_date = inv_date + timedelta(days=30)
        invoices.append({
            "invoice_id":        _inv_id(inv_n),
            "customer_id":       cust["id"],
            "supplier_name":     SUPPLIER,
            "customer_name":     cust["name"],
            "customer_gstin":    cust["gstin"],
            "invoice_date":      inv_date.isoformat(),
            "due_date":          due_date.isoformat(),
            "taxable_amount":    float(taxable),
            "gst_amount":        float(gst),
            "total_amount":      float(total),
            "outstanding_amount": float(total),
            "currency":          "INR",
            "status":            "open",
        })
        inv_n += 1

    return invoices[:40]


# ── Bank Statement Generator ──────────────────────────────────────────────────

def generate_bank_transactions(invoices: list[dict]) -> tuple[list[dict], list[dict]]:
    """Returns (bank_transactions, ground_truth_allocations)."""
    txns = []
    ground_truth = []
    txn_n = 1

    def _txn(narration, credit, date_offset=35, utr_val=None, cust_variant=None):
        nonlocal txn_n
        t = {
            "transaction_id":  f"TXN-{txn_n:03d}",
            "utr":             utr_val or _utr(),
            "narration":       narration,
            "credit_amount":   float(credit),
            "debit_amount":    0.0,
            "transaction_date": (_rand_date(date_offset - 5, date_offset + 5)).isoformat(),
            "bank_name":       "AXIS Bank",
            "account_number":  "9876543210",
            "currency":        "INR",
            "status":          "unmatched",
        }
        txn_n += 1
        return t

    # Scenario 1: Exact invoice reference (INV-001)
    inv = invoices[0]
    t_utr = _utr()
    t = _txn(f"NEFT/{inv['customer_name'].split()[0].upper()}/{inv['invoice_id']}", inv["total_amount"], utr_val=t_utr)
    txns.append(t)
    ground_truth.append({"transaction_id": t["transaction_id"], "invoice_id": inv["invoice_id"],
                          "scenario": "exact_reference", "expected_outcome": "matched"})

    # Scenario 2: Customer name variation (INV-002)
    inv = invoices[1]
    cust = next(c for c in CUSTOMERS if c["id"] == inv["customer_id"])
    variant = random.choice(cust["variants"]) if cust["variants"] else cust["name"]
    t = _txn(f"NEFT/{variant.upper().replace(' ', '')}/PAYMENT", inv["total_amount"])
    txns.append(t)
    ground_truth.append({"transaction_id": t["transaction_id"], "invoice_id": inv["invoice_id"],
                          "scenario": "name_variation", "expected_outcome": "matched"})

    # Scenario 3: Payment without invoice number (INV-003)
    inv = invoices[2]
    t = _txn(f"RTGS/{inv['customer_name'].split()[0].upper()}/SETTLEMENT", inv["total_amount"])
    txns.append(t)
    ground_truth.append({"transaction_id": t["transaction_id"], "invoice_id": inv["invoice_id"],
                          "scenario": "no_invoice_ref", "expected_outcome": "review"})

    # Scenario 4: Partial payment (INV-004)
    inv = invoices[3]
    partial = round(float(inv["total_amount"]) * 0.6, 2)
    t = _txn(f"IMPS/{inv['customer_name'].split()[0].upper()}/{inv['invoice_id']}", partial)
    txns.append(t)
    ground_truth.append({"transaction_id": t["transaction_id"], "invoice_id": inv["invoice_id"],
                          "scenario": "partial_payment", "expected_outcome": "review"})

    # Scenario 5: TDS deduction (INV-005, 10% TDS)
    inv = invoices[4]
    tds = round(float(inv["total_amount"]) * 0.10, 2)
    credit = round(float(inv["total_amount"]) - tds, 2)
    t_utr = _utr()
    t = _txn(f"NEFT/{inv['customer_name'].split()[0].upper()}/{inv['invoice_id']}", credit, utr_val=t_utr)
    txns.append(t)
    ground_truth.append({"transaction_id": t["transaction_id"], "invoice_id": inv["invoice_id"],
                          "scenario": "tds_deduction", "tds_amount": tds, "expected_outcome": "review",
                          "utr": t_utr})

    # Scenario 6: One payment, multiple invoices (INV-006 + INV-007)
    inv1, inv2 = invoices[5], invoices[6]
    bulk_total = float(inv1["total_amount"]) + float(inv2["total_amount"])
    t = _txn(f"RTGS/{inv1['customer_name'].split()[0].upper()}/BULK", bulk_total)
    txns.append(t)
    ground_truth.append({"transaction_id": t["transaction_id"],
                          "invoice_ids": [inv1["invoice_id"], inv2["invoice_id"]],
                          "scenario": "bulk_payment", "expected_outcome": "review"})

    # Scenario 7: Multiple payments, one invoice (INV-008, two payments)
    inv = invoices[7]
    half = round(float(inv["total_amount"]) / 2, 2)
    t1 = _txn(f"NEFT/{inv['customer_name'].split()[0].upper()}/{inv['invoice_id']}/PART1", half)
    t2 = _txn(f"NEFT/{inv['customer_name'].split()[0].upper()}/{inv['invoice_id']}/PART2", half)
    txns.extend([t1, t2])
    ground_truth.extend([
        {"transaction_id": t1["transaction_id"], "invoice_id": inv["invoice_id"],
         "scenario": "multi_payment_1", "expected_outcome": "review"},
        {"transaction_id": t2["transaction_id"], "invoice_id": inv["invoice_id"],
         "scenario": "multi_payment_2", "expected_outcome": "review"},
    ])

    # Scenario 8: Duplicate UTR
    inv = invoices[8]
    dup_utr = _utr()
    t1 = _txn(f"NEFT/{inv['customer_name'].split()[0].upper()}/{inv['invoice_id']}", inv["total_amount"], utr_val=dup_utr)
    t2 = _txn(f"NEFT/{inv['customer_name'].split()[0].upper()}/{inv['invoice_id']}", inv["total_amount"], utr_val=dup_utr)
    txns.extend([t1, t2])
    ground_truth.extend([
        {"transaction_id": t1["transaction_id"], "invoice_id": inv["invoice_id"],
         "scenario": "duplicate_utr_original", "expected_outcome": "matched"},
        {"transaction_id": t2["transaction_id"], "invoice_id": inv["invoice_id"],
         "scenario": "duplicate_utr_copy", "expected_outcome": "duplicate"},
    ])

    # Scenario 9: Unknown customer
    t = _txn("UPI/UNKNOWNPAYER/PAYMENT", 50000)
    txns.append(t)
    ground_truth.append({"transaction_id": t["transaction_id"], "invoice_id": None,
                          "scenario": "unknown_customer", "expected_outcome": "unmatched"})

    # Scenario 10: Two invoices with identical amounts (INV-009, INV-010)
    inv1, inv2 = invoices[9], invoices[10]
    # Force same amount
    same_amount = float(inv1["total_amount"])
    t = _txn(f"NEFT/AMBIGUOUS/PAYMENT", same_amount)
    txns.append(t)
    ground_truth.append({"transaction_id": t["transaction_id"],
                          "invoice_ids": [inv1["invoice_id"], inv2["invoice_id"]],
                          "scenario": "ambiguous_same_amount", "expected_outcome": "review"})

    # Scenario 11: Incorrect amount (INV-011, off by ₹5,000)
    inv = invoices[10]
    wrong_amount = float(inv["total_amount"]) - 5000
    t = _txn(f"NEFT/{inv['customer_name'].split()[0].upper()}/{inv['invoice_id']}", wrong_amount)
    txns.append(t)
    ground_truth.append({"transaction_id": t["transaction_id"], "invoice_id": inv["invoice_id"],
                          "scenario": "amount_discrepancy", "expected_outcome": "review"})

    # Scenario 12: Missing remittance (INV-012)
    inv = invoices[11]
    t = _txn(f"NEFT/{inv['customer_name'].split()[0].upper()}/OCT", inv["total_amount"])
    txns.append(t)
    ground_truth.append({"transaction_id": t["transaction_id"], "invoice_id": inv["invoice_id"],
                          "scenario": "missing_remittance", "expected_outcome": "review"})

    # Scenario 13: Incorrect invoice reference in narration (INV-013 but narration says wrong inv)
    inv = invoices[12]
    t = _txn(f"NEFT/{inv['customer_name'].split()[0].upper()}/INV-999", inv["total_amount"])
    txns.append(t)
    ground_truth.append({"transaction_id": t["transaction_id"], "invoice_id": inv["invoice_id"],
                          "scenario": "wrong_invoice_ref", "expected_outcome": "review"})

    # Scenario 14: Overpayment (INV-014 + extra ₹10,000)
    inv = invoices[13]
    over = float(inv["total_amount"]) + 10000
    t = _txn(f"RTGS/{inv['customer_name'].split()[0].upper()}/{inv['invoice_id']}", over)
    txns.append(t)
    ground_truth.append({"transaction_id": t["transaction_id"], "invoice_id": inv["invoice_id"],
                          "scenario": "overpayment", "expected_outcome": "review"})

    # Scenario 15: Repeated file upload (same hash) — handled at ingestion, not here
    # Pad remaining transactions up to 30 with clean exact matches from unused invoices
    used_inv_ids = {
        gt.get("invoice_id") for gt in ground_truth
        if gt.get("invoice_id")
    } | {
        iid for gt in ground_truth
        for iid in (gt.get("invoice_ids") or [])
    }
    unused_invoices = [inv for inv in invoices if inv["invoice_id"] not in used_inv_ids]
    for inv in unused_invoices:
        if len(txns) >= 30:
            break
        t = _txn(f"NEFT/{inv['customer_name'].split()[0].upper()}/{inv['invoice_id']}",
                 inv["total_amount"])
        txns.append(t)
        ground_truth.append({
            "transaction_id": t["transaction_id"],
            "invoice_id":     inv["invoice_id"],
            "scenario":       "clean_exact_match",
            "expected_outcome": "matched",
        })

    # If still not 30, add generic transactions (no match expected)
    while len(txns) < 30:
        t = _txn(f"NEFT/FILLER/MISC{len(txns)}", random.choice([50000, 75000, 100000]))
        txns.append(t)
        ground_truth.append({
            "transaction_id": t["transaction_id"],
            "invoice_id":     None,
            "scenario":       "clean_exact_match",
            "expected_outcome": "matched",
        })

    return txns[:30], ground_truth


# ── Payment Email Generator ───────────────────────────────────────────────────

def generate_payment_emails(transactions: list[dict], invoices: list[dict],
                             ground_truth: list[dict]) -> list[dict]:
    """Generate 15 remittance emails linked to key scenarios."""
    emails = []
    inv_map = {inv["invoice_id"]: inv for inv in invoices}

    # Emails for TDS scenario
    tds_gt = next((g for g in ground_truth if g["scenario"] == "tds_deduction"), None)
    if tds_gt:
        inv = inv_map.get(tds_gt["invoice_id"], {})
        tds = tds_gt.get("tds_amount", 0)
        credit = float(inv.get("total_amount", 0)) - tds
        cust = next((c for c in CUSTOMERS if c["id"] == inv.get("customer_id")), {})
        emails.append({
            "filename": "email_tds_01.txt",
            "linked_transaction_id": tds_gt["transaction_id"],
            "utr": tds_gt.get("utr"),
            "content": f"""From: accounts@{cust.get('name','').lower().replace(' ','')}example.com
To: ar@supplierexample.com
Subject: Payment against {inv.get('invoice_id','')}

Dear Team,

We have transferred ₹{credit:,.2f} against {inv.get('invoice_id','')} dated {inv.get('invoice_date','')}.
TDS of ₹{tds:,.2f} has been deducted under Section 194J.
Transaction reference: {tds_gt.get('utr','')}.

Please acknowledge receipt.

Regards,
Accounts Payable Team
{cust.get('name','')}
""",
        })

    # Emails for bulk payment scenario
    bulk_gt = next((g for g in ground_truth if g["scenario"] == "bulk_payment"), None)
    if bulk_gt:
        inv_ids = bulk_gt.get("invoice_ids", [])
        invs = [inv_map[i] for i in inv_ids if i in inv_map]
        if invs:
            cust = next((c for c in CUSTOMERS if c["id"] == invs[0].get("customer_id")), {})
            total = sum(float(i["total_amount"]) for i in invs)
            bulk_txn = next((t for t in transactions if t["transaction_id"] == bulk_gt["transaction_id"]), {})
            emails.append({
                "filename": "email_bulk_01.txt",
                "linked_transaction_id": bulk_gt["transaction_id"],
                "utr": bulk_txn.get("utr"),
                "content": f"""From: finance@{cust.get('name','').split()[0].lower()}example.com
To: ar@supplierexample.com
Subject: Bulk Payment — {', '.join(inv_ids)}

Dear Sir/Madam,

Please find below payment details for multiple invoices:
- {inv_ids[0]}: ₹{float(invs[0]['total_amount']):,.2f}
- {inv_ids[1]}: ₹{float(invs[1]['total_amount']):,.2f}

Total transferred: ₹{total:,.2f}
UTR: {bulk_txn.get('utr','')}

Regards,
{cust.get('name','')}
""",
            })

    # Clean confirmation emails for exact matches
    for gt in ground_truth:
        if gt["scenario"] == "clean_exact_match" and len(emails) < 15:
            inv = inv_map.get(gt.get("invoice_id", ""), {})
            if not inv:
                continue
            cust = next((c for c in CUSTOMERS if c["id"] == inv.get("customer_id")), {})
            txn = next((t for t in transactions if t["transaction_id"] == gt["transaction_id"]), {})
            emails.append({
                "filename": f"email_confirm_{len(emails)+1:02d}.txt",
                "linked_transaction_id": gt["transaction_id"],
                "utr": txn.get("utr"),
                "content": f"""From: payments@{cust.get('name','').split()[0].lower()}example.com
To: ar@supplierexample.com
Subject: Payment Confirmation — {inv.get('invoice_id','')}

Hi,

We have made a payment of ₹{float(inv.get('total_amount',0)):,.2f} towards invoice {inv.get('invoice_id','')} (due {inv.get('due_date','')}).
UTR: {txn.get('utr','')}

Best regards,
Finance Team, {cust.get('name','')}
""",
            })

    return emails[:15]


# ── Write Files ───────────────────────────────────────────────────────────────

def write_dataset():
    random.seed(42)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    customers = CUSTOMERS
    invoices  = generate_invoices()
    bank_txns, ground_truth = generate_bank_transactions(invoices)
    emails    = generate_payment_emails(bank_txns, invoices, ground_truth)

    # Write customers JSON
    (OUTPUT_DIR / "customers.json").write_text(json.dumps(customers, indent=2), encoding="utf-8")

    # Write invoices JSON
    (OUTPUT_DIR / "invoices.json").write_text(json.dumps(invoices, indent=2), encoding="utf-8")

    # Write bank statement CSV
    with open(OUTPUT_DIR / "bank_statement.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["transaction_id","utr","narration","credit_amount",
                                                "debit_amount","transaction_date","bank_name",
                                                "account_number","currency","status"])
        writer.writeheader()
        writer.writerows(bank_txns)

    # Write ground truth JSON
    (OUTPUT_DIR / "ground_truth.json").write_text(json.dumps(ground_truth, indent=2), encoding="utf-8")

    # Write email files
    email_dir = OUTPUT_DIR / "emails"
    email_dir.mkdir(exist_ok=True)
    for email in emails:
        (email_dir / email["filename"]).write_text(email["content"], encoding="utf-8")

    # Write email index
    (OUTPUT_DIR / "email_index.json").write_text(
        json.dumps([{k: v for k, v in e.items() if k != "content"} for e in emails], indent=2),
        encoding="utf-8"
    )

    print(f"✅ Dataset generated in {OUTPUT_DIR}")
    print(f"   Customers:    {len(customers)}")
    print(f"   Invoices:     {len(invoices)}")
    print(f"   Transactions: {len(bank_txns)}")
    print(f"   Emails:       {len(emails)}")
    print(f"   Ground truth: {len(ground_truth)} allocation records")


if __name__ == "__main__":
    write_dataset()
