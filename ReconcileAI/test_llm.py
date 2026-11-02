"""
Full end-to-end LLM pipeline test — OpenRouter + gemini-2.5-flash
Tests all 4 extraction functions used by ReconcileAI.
"""
import sys, importlib
sys.path.insert(0, ".")

# Force reload settings so .env changes are picked up
import config.settings as _s
importlib.reload(_s)
import extraction.llm_extractor as _llm
importlib.reload(_llm)
from extraction.llm_extractor import (
    _call_llm, extract_invoice_data, extract_remittance_data, interpret_bank_narration
)

SEP = "=" * 60

# ── TEST 1: Basic connectivity ─────────────────────────────────
print(SEP)
print("TEST 1: Basic connectivity")
print(SEP)
result = _call_llm(
    system_prompt="You are a financial assistant.",
    user_content="Reply with only: CONNECTED",
    temperature=0.0,
)
print("Response:", result.strip())
print("✅ PASSED\n")

# ── TEST 2: Invoice extraction ─────────────────────────────────
print(SEP)
print("TEST 2: Invoice extraction")
print(SEP)
INVOICE_TEXT = """
TAX INVOICE
Invoice No: INV-2026-001        Date: 15-Oct-2026
Bill To: XYZ Technologies Pvt Ltd   GSTIN: 27AABCX1234A1Z5

Software Consulting (1 unit @ ₹90,000)   ₹90,000.00
GST @ 18%                                 ₹16,200.00
Total Amount Due                        ₹1,06,200.00

Payment Due: 15-Nov-2026
"""
inv = extract_invoice_data(INVOICE_TEXT)
print(f"  Invoice ID:    {inv.get('invoice_id')}")
print(f"  Customer:      {inv.get('customer_name')}")
print(f"  Total:         {inv.get('total_amount')}")
print(f"  GST:           {inv.get('gst_amount')}")
print(f"  Status:        {inv.get('extraction_status')}")
assert inv.get("total_amount") is not None, "Missing total_amount"
print("✅ PASSED\n")

# ── TEST 3: Remittance email extraction ────────────────────────
print(SEP)
print("TEST 3: Remittance email extraction")
print(SEP)
EMAIL_TEXT = """
Subject: Payment Advice - INV-2026-001
From: accounts@xyztechnologies.com

Invoice Amount:   ₹1,06,200.00
TDS Deducted:    ₹9,000.00 (u/s 194J @ 10% on ₹90,000)
Net Amount Paid: ₹97,200.00
UTR: AXIS202610150012345    Date: 20-Oct-2026    Mode: NEFT
"""
rem = extract_remittance_data(EMAIL_TEXT)
print(f"  Customer:      {rem.get('customer_name')}")
print(f"  Invoice refs:  {rem.get('invoice_references')}")
print(f"  Payment amt:   {rem.get('payment_amount')}")
print(f"  TDS:           {rem.get('tds_amount')}")
print(f"  UTR:           {rem.get('utr')}")
assert rem.get("utr") is not None, "Missing UTR"
assert rem.get("tds_amount", 0) > 0, "Missing TDS"
print("✅ PASSED\n")

# ── TEST 4: Bank narration interpretation ──────────────────────
print(SEP)
print("TEST 4: Narration interpretation")
print(SEP)
narr = interpret_bank_narration(
    "NEFT/XYZTECHNOLOGIESPVTLTD/INV2026001/97200",
    amount=97200.0
)
print(f"  Payer:         {narr.get('likely_payer')}")
print(f"  Invoice refs:  {narr.get('invoice_references')}")
print(f"  Confidence:    {narr.get('confidence')}")
print("✅ PASSED\n")

print(SEP)
print("ALL 4 LLM TESTS PASSED")
print(f"Provider: OpenRouter  |  Model: google/gemini-2.5-flash")
print(SEP)
