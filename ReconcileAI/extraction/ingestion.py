"""
ReconcileAI – Document Ingestion Orchestrator
Handles upload, validation, classification and initial storage of all document types.
"""
from __future__ import annotations
import hashlib
import json
import sys
import os
import tempfile
from pathlib import Path
from typing import Any

from config.settings import MAX_FILE_SIZE_BYTES, ALLOWED_EXTENSIONS
from database.repositories import (
    insert_document, insert_transaction, insert_invoice, upsert_customer,
    insert_remittance, insert_extraction, create_audit_log,
)
from extraction.bank_parser import read_bank_csv, read_bank_excel
from extraction.invoice_parser import extract_pdf_text, extract_excel_invoices
from extraction.document_classifier import classify_by_file_type, classify_document_by_text


# ── File Validation ───────────────────────────────────────────────────────────

def validate_file(filename: str, file_bytes: bytes) -> dict:
    """Validate file extension and size. Returns {"valid": bool, "reason": str}."""
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        return {"valid": False, "reason": f"File exceeds {MAX_FILE_SIZE_BYTES // 1024 // 1024} MB limit"}
    ext = Path(filename).suffix.lower()
    allowed = set().union(*ALLOWED_EXTENSIONS.values())
    if ext not in allowed:
        return {"valid": False, "reason": f"File type '{ext}' is not supported"}
    return {"valid": True, "reason": ""}


def calculate_document_hash(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


# ── Bank Statement Ingestion ──────────────────────────────────────────────────

def ingest_bank_statement(filename: str, file_bytes: bytes,
                           bank_name: str = "Unknown", batch_id: str | None = None) -> dict:
    """Parse and store a bank statement. Returns summary dict."""
    validation = validate_file(filename, file_bytes)
    if not validation["valid"]:
        return {"success": False, "reason": validation["reason"]}

    file_hash = calculate_document_hash(file_bytes)
    ext = Path(filename).suffix.lower()

    # Save to temp file for parsing
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        if ext == ".csv":
            transactions = read_bank_csv(tmp_path, bank_name=bank_name)
        else:
            transactions = read_bank_excel(tmp_path, bank_name=bank_name)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    # Store document record
    doc_id = insert_document({
        "filename":      filename,
        "document_type": "bank_statement",
        "file_hash":     file_hash,
        "batch_id":      batch_id,
    })

    # Store transactions
    imported = 0
    for txn in transactions:
        txn["source_document_id"] = doc_id
        txn["batch_id"] = batch_id
        insert_transaction(txn)
        imported += 1
        create_audit_log("transaction", txn["transaction_id"], "imported",
                         details={"source": filename})

    return {"success": True, "document_id": doc_id, "transactions_imported": imported}


# ── Invoice Ingestion ─────────────────────────────────────────────────────────

def ingest_invoice(filename: str, file_bytes: bytes, batch_id: str | None = None) -> dict:
    """Parse a PDF or Excel invoice and store extracted data."""
    validation = validate_file(filename, file_bytes)
    if not validation["valid"]:
        return {"success": False, "reason": validation["reason"]}

    file_hash = calculate_document_hash(file_bytes)
    ext = Path(filename).suffix.lower()

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    doc_id = insert_document({
        "filename":      filename,
        "document_type": "invoice",
        "file_hash":     file_hash,
        "batch_id":      batch_id,
    })

    try:
        if ext == ".pdf":
            text = extract_pdf_text(tmp_path)
            extracted = _llm_extract_invoice(text, doc_id)
            invoices_data = [extracted] if extracted else []
        else:
            raw_rows = extract_excel_invoices(tmp_path)
            invoices_data = [_map_excel_invoice_row(r) for r in raw_rows]
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    stored_ids = []
    for inv_data in invoices_data:
        if not inv_data.get("invoice_id"):
            continue
        # Upsert customer
        cust_id = upsert_customer(
            name=inv_data.get("customer_name", "Unknown"),
            gstin=inv_data.get("customer_gstin"),
        )
        inv_data["customer_id"]       = cust_id
        inv_data["source_document_id"] = doc_id
        iid = insert_invoice(inv_data)
        stored_ids.append(iid)
        create_audit_log("invoice", iid, "imported", details={"source": filename})

    return {"success": True, "document_id": doc_id, "invoices_imported": len(stored_ids),
            "invoice_ids": stored_ids}


def _llm_extract_invoice(text: str, doc_id: str) -> dict | None:
    """Call LLM extractor and store the extraction result."""
    try:
        from extraction.llm_extractor import extract_invoice_data
        result = extract_invoice_data(text)
        insert_extraction({
            "document_id":      doc_id,
            "extractor":        "llm",
            "raw_output":       json.dumps(result),
            "validated_output": json.dumps(result),
            "extraction_status": result.get("extraction_status", "complete"),
            "missing_fields":   result.get("missing_fields", []),
        })
        return result
    except Exception as e:
        insert_extraction({
            "document_id": doc_id, "extractor": "llm",
            "extraction_status": "failed", "raw_output": str(e),
        })
        return None


def _map_excel_invoice_row(row: dict) -> dict:
    """Map raw Excel row keys to our standard invoice schema."""
    def _get(*keys):
        for k in keys:
            if k in row and row[k] is not None:
                return row[k]
        return None

    def _float(v):
        try:
            return float(str(v).replace(",", "").replace("₹", ""))
        except Exception:
            return 0.0

    return {
        "invoice_id":       _get("invoice_id", "invoice no", "invoice number", "inv_id"),
        "supplier_name":    _get("supplier_name", "supplier", "from"),
        "customer_name":    _get("customer_name", "customer", "bill to", "billed to"),
        "customer_gstin":   _get("customer_gstin", "gstin", "gst number"),
        "invoice_date":     _get("invoice_date", "date", "invoice date"),
        "due_date":         _get("due_date", "payment due"),
        "taxable_amount":   _float(_get("taxable_amount", "taxable", "base amount") or 0),
        "gst_amount":       _float(_get("gst_amount", "gst", "tax amount") or 0),
        "total_amount":     _float(_get("total_amount", "total", "amount") or 0),
        "currency":         _get("currency") or "INR",
        "status":           "open",
    }


# ── Remittance Email Ingestion ────────────────────────────────────────────────

def ingest_remittance(filename: str, file_bytes: bytes, batch_id: str | None = None) -> dict:
    """Parse a payment email and store extracted remittance data."""
    validation = validate_file(filename, file_bytes)
    if not validation["valid"]:
        return {"success": False, "reason": validation["reason"]}

    file_hash = calculate_document_hash(file_bytes)
    text = file_bytes.decode("utf-8", errors="ignore")

    doc_id = insert_document({
        "filename":      filename,
        "document_type": "remittance",
        "file_hash":     file_hash,
        "batch_id":      batch_id,
    })

    try:
        from extraction.llm_extractor import extract_remittance_data
        result = extract_remittance_data(text)
        insert_extraction({
            "document_id":      doc_id,
            "extractor":        "llm",
            "raw_output":       json.dumps(result),
            "validated_output": json.dumps(result),
            "extraction_status": "complete",
        })
        rid = insert_remittance({**result, "document_id": doc_id})
        create_audit_log("remittance", rid, "imported", details={"source": filename})
        return {"success": True, "document_id": doc_id, "remittance_id": rid, "extracted": result}
    except Exception as e:
        return {"success": False, "document_id": doc_id, "reason": str(e)}


# ── Synthetic Dataset Loader ──────────────────────────────────────────────────

def load_synthetic_dataset() -> dict:
    """Load the pre-generated synthetic dataset directly into the database."""
    import sys, importlib
    # Ensure the project root is on sys.path so 'data' resolves correctly
    project_root = str(Path(__file__).parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    import random
    # Import via importlib to avoid stdlib 'data' name clash
    spec = importlib.util.spec_from_file_location(
        "generate_dataset",
        Path(__file__).parent.parent / "data" / "synthetic" / "generate_dataset.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    random.seed(42)
    customers = mod.CUSTOMERS
    invoices  = mod.generate_invoices()
    bank_txns, ground_truth = mod.generate_bank_transactions(invoices)
    emails    = mod.generate_payment_emails(bank_txns, invoices, ground_truth)

    # Load customers
    for cust in customers:
        upsert_customer(name=cust["name"], gstin=cust.get("gstin"),
                        variants=cust.get("variants", []))

    # Load invoices
    for inv in invoices:
        cust_id = upsert_customer(inv["customer_name"], gstin=inv.get("customer_gstin"))
        inv["customer_id"] = cust_id
        insert_invoice(inv)

    # Load bank transactions
    for txn in bank_txns:
        insert_transaction(txn)

    # Load remittances from emails (parse inline — no LLM needed for synthetic data)
    rem_count = 0
    for email in emails:
        content = email["content"]
        utr = email.get("utr")
        if utr:
            # Basic pattern extraction for synthetic emails (no LLM call needed)
            import re
            inv_refs = re.findall(r"INV-\d+", content)
            amount_m = re.search(r"₹([\d,]+(?:\.\d+)?)", content)
            tds_m    = re.search(r"TDS of ₹([\d,]+(?:\.\d+)?)", content)
            amount   = float(amount_m.group(1).replace(",","")) if amount_m else 0.0
            tds      = float(tds_m.group(1).replace(",","")) if tds_m else 0.0

            insert_remittance({
                "customer_name":       None,
                "invoice_references":  inv_refs,
                "payment_amount":      amount,
                "tds_amount":          tds,
                "utr":                 utr,
                "payment_date":        None,
                "currency":            "INR",
                "evidence":            [content[:200]],
            })
            rem_count += 1

    create_audit_log("system", "dataset", "synthetic_dataset_loaded",
                     details={"customers": len(customers), "invoices": len(invoices),
                               "transactions": len(bank_txns), "emails": len(emails)})

    return {
        "customers": len(customers), "invoices": len(invoices),
        "transactions": len(bank_txns), "emails": len(emails),
        "remittances_stored": rem_count,
        "ground_truth": ground_truth,
    }
