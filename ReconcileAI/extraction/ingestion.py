"""
ReconcileAI – Document Ingestion Orchestrator
Handles upload, validation, classification and initial storage of all document types.

Every ingest_* function returns {"success": bool, ...}. A file whose SHA-256
was already ingested is rejected (scenario: the same statement uploaded twice).
"""
from __future__ import annotations
import hashlib
import importlib.util
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from config.settings import MAX_FILE_SIZE_BYTES, ALLOWED_EXTENSIONS, UPLOADS_DIR
from database.repositories import (
    insert_document, insert_transaction, insert_invoice, upsert_customer,
    insert_remittance, insert_extraction, create_audit_log, get_document_by_hash,
    invoice_exists, transaction_exists,
)
from extraction.bank_parser import read_bank_csv, read_bank_excel
from extraction.invoice_parser import extract_pdf_bytes_text, extract_excel_invoices
from extraction.document_classifier import classify_by_file_type, classify_document_by_text
from extraction.rule_extractor import (
    extract_invoice_rules, extract_remittance_rules, merge_extractions, parse_amount,
)


# ── File Validation ───────────────────────────────────────────────────────────

def validate_file(filename: str, file_bytes: bytes, document_type: str | None = None) -> dict:
    """Validate file extension and size. Returns {"valid": bool, "reason": str}."""
    if not file_bytes:
        return {"valid": False, "reason": "File is empty"}
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        return {"valid": False, "reason": f"File exceeds {MAX_FILE_SIZE_BYTES // 1024 // 1024} MB limit"}
    ext = Path(filename).suffix.lower()
    allowed = ALLOWED_EXTENSIONS.get(document_type) if document_type else set().union(*ALLOWED_EXTENSIONS.values())
    if ext not in allowed:
        return {"valid": False, "reason": f"File type '{ext}' is not supported for {document_type or 'upload'} "
                                          f"(allowed: {', '.join(sorted(allowed))})"}
    return {"valid": True, "reason": ""}


def calculate_document_hash(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def _precheck(filename: str, file_bytes: bytes, document_type: str, allow_duplicate: bool) -> dict | None:
    v = validate_file(filename, file_bytes, document_type)
    if not v["valid"]:
        return {"success": False, "reason": v["reason"]}
    existing = get_document_by_hash(calculate_document_hash(file_bytes))
    if existing and not allow_duplicate:
        return {"success": False, "duplicate_file": True, "document_id": existing["document_id"],
                "reason": f"This file was already uploaded as '{existing['filename']}' "
                          f"on {existing['upload_time']} — nothing imported."}
    return None


def _with_tempfile(file_bytes: bytes, suffix: str, fn):
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        path = tmp.name
    try:
        return fn(path)
    finally:
        Path(path).unlink(missing_ok=True)


# ── Bank Statement Ingestion ──────────────────────────────────────────────────

def ingest_bank_statement(filename: str, file_bytes: bytes, bank_name: str = "Unknown",
                          batch_id: str | None = None, account_number: str | None = None,
                          allow_duplicate: bool = False) -> dict:
    """Parse and store a bank statement. Returns summary dict."""
    err = _precheck(filename, file_bytes, "bank_statement", allow_duplicate)
    if err:
        return err

    ext = Path(filename).suffix.lower()
    try:
        reader = read_bank_csv if ext == ".csv" else read_bank_excel
        transactions = _with_tempfile(file_bytes, ext,
                                      lambda p: reader(p, bank_name=bank_name, account_number=account_number))
    except Exception as e:
        return {"success": False, "reason": f"Could not parse bank statement: {e}"}
    if not transactions:
        return {"success": False, "reason": "No transactions found — check the file has Date / Narration / "
                                            "Credit columns."}

    doc_id = insert_document({"filename": filename, "document_type": "bank_statement",
                              "file_hash": calculate_document_hash(file_bytes), "batch_id": batch_id})
    imported = already = debits = 0
    for txn in transactions:
        if transaction_exists(txn["transaction_id"]):
            already += 1          # same line seen in an overlapping statement
            continue
        txn["source_document_id"] = doc_id
        txn["batch_id"] = batch_id
        insert_transaction(txn)
        imported += 1
        if float(txn.get("credit_amount") or 0) <= 0:
            debits += 1
        create_audit_log("transaction", txn["transaction_id"], "imported", details={"source": filename})

    create_audit_log("document", doc_id, "bank_statement_ingested",
                     details={"filename": filename, "imported": imported, "already_present": already,
                              "debits_excluded": debits})
    return {"success": True, "document_id": doc_id, "transactions_imported": imported,
            "credits_imported": imported - debits, "debits_excluded": debits, "already_present": already}


# ── Invoice Ingestion ─────────────────────────────────────────────────────────

def ingest_invoice(filename: str, file_bytes: bytes, batch_id: str | None = None,
                   allow_duplicate: bool = False) -> dict:
    """Parse a PDF, Excel or CSV invoice file and store extracted invoices."""
    err = _precheck(filename, file_bytes, "invoice", allow_duplicate)
    if err:
        return err

    ext = Path(filename).suffix.lower()
    doc_id = insert_document({"filename": filename, "document_type": "invoice",
                              "file_hash": calculate_document_hash(file_bytes), "batch_id": batch_id})
    warnings: list[str] = []
    try:
        if ext == ".pdf":
            text = extract_pdf_bytes_text(file_bytes)
            if not text.strip():
                return {"success": False, "document_id": doc_id,
                        "reason": "No text could be read from the PDF (scanned image without OCR?)."}
            extracted = extract_invoice_from_text(text, doc_id)
            invoices_data = [extracted] if extracted else []
            if extracted and extracted.get("missing_fields"):
                warnings.append(f"Missing fields: {', '.join(extracted['missing_fields'])}")
        else:
            raw_rows = _with_tempfile(file_bytes, ext, extract_excel_invoices)
            invoices_data = [_map_excel_invoice_row(r) for r in raw_rows]
            insert_extraction({"document_id": doc_id, "extractor": "tabular_parser",
                               "raw_output": json.dumps(raw_rows[:200], default=str),
                               "validated_output": json.dumps(invoices_data[:200], default=str),
                               "extraction_status": "complete"})
    except Exception as e:
        return {"success": False, "document_id": doc_id, "reason": f"Could not parse invoice file: {e}"}

    stored_ids, skipped = [], []
    for inv in invoices_data:
        if not inv.get("invoice_id") or not inv.get("total_amount"):
            skipped.append(inv.get("invoice_id") or "(no invoice number)")
            continue
        inv["invoice_id"] = str(inv["invoice_id"]).strip()
        if invoice_exists(inv["invoice_id"]):
            skipped.append(f"{inv['invoice_id']} (already exists)")
            continue
        inv["customer_id"] = upsert_customer(name=inv.get("customer_name") or "Unknown",
                                             gstin=inv.get("customer_gstin"))
        inv["source_document_id"] = doc_id
        iid = insert_invoice(inv)
        stored_ids.append(iid)
        create_audit_log("invoice", iid, "imported", details={"source": filename})

    if skipped:
        warnings.append(f"Skipped {len(skipped)}: {', '.join(skipped[:10])}{'…' if len(skipped) > 10 else ''}")
    return {"success": bool(stored_ids), "document_id": doc_id,
            "invoices_imported": len(stored_ids), "invoice_ids": stored_ids,
            "skipped": skipped, "warnings": warnings,
            **({} if stored_ids else {"reason": "; ".join(warnings) or "No invoices found in file."})}


def extract_invoice_from_text(text: str, doc_id: str | None = None) -> dict | None:
    """LLM extraction when available, always backed by the rule-based extractor."""
    from extraction.llm_extractor import extract_invoice_data, llm_available
    rules = extract_invoice_rules(text)
    result, extractor, raw = rules, "rules", json.dumps(rules)
    if llm_available():
        try:
            llm = extract_invoice_data(text)
            result, extractor, raw = merge_extractions(llm, rules), "llm+rules", json.dumps(llm)
            result["missing_fields"] = [k for k in ("invoice_id", "customer_name", "total_amount")
                                        if not result.get(k)]
            result["extraction_status"] = "partial" if result["missing_fields"] else "complete"
        except Exception as e:
            raw = json.dumps({"llm_error": str(e), "rules": rules})
    if doc_id:
        insert_extraction({"document_id": doc_id, "extractor": extractor, "raw_output": raw,
                           "validated_output": json.dumps(result, default=str),
                           "extraction_status": result.get("extraction_status", "complete"),
                           "missing_fields": result.get("missing_fields", [])})
    return result if result.get("invoice_id") else None


def _map_excel_invoice_row(row: dict) -> dict:
    """Map raw Excel/CSV row keys (lower-cased, spaces) to our invoice schema."""
    def _get(*keys):
        for k in keys:
            if row.get(k) not in (None, ""):
                return row[k]
        return None

    def _float(v):
        if v is None:
            return None
        return parse_amount(str(v).replace("₹", "").replace("INR", "").replace("Rs.", ""))

    def _date(v):
        if not v:
            return None
        try:
            from dateutil import parser as dp
            return dp.parse(str(v), dayfirst=not re.match(r"\d{4}-", str(v))).date().isoformat()
        except Exception:
            return None

    total = _float(_get("total amount", "total", "invoice total", "grand total", "invoice amount", "amount"))
    taxable = _float(_get("taxable amount", "taxable value", "taxable", "base amount", "subtotal"))
    gst = _float(_get("gst amount", "gst", "tax amount", "tax"))
    if total is None and taxable is not None:
        total = round(taxable + (gst or 0), 2)
    outstanding = _float(_get("outstanding amount", "outstanding", "balance due", "amount due", "balance"))
    status = "open"
    if outstanding is not None and total and outstanding < total - 0.01:
        status = "settled" if outstanding <= 0.01 else "partially_paid"

    return {
        "invoice_id":         _get("invoice id", "invoice no", "invoice no.", "invoice number", "inv id", "inv no", "bill no"),
        "supplier_name":      _get("supplier name", "supplier", "from", "seller"),
        "customer_name":      _get("customer name", "customer", "bill to", "billed to", "buyer", "party name"),
        "customer_gstin":     _get("customer gstin", "gstin", "gst number", "buyer gstin"),
        "invoice_date":       _date(_get("invoice date", "date", "bill date")),
        "due_date":           _date(_get("due date", "payment due", "due")),
        "taxable_amount":     taxable or 0.0,
        "gst_amount":         gst or 0.0,
        "total_amount":       total or 0.0,
        "outstanding_amount": outstanding if outstanding is not None else total,
        "currency":           _get("currency") or "INR",
        "status":             status,
    }


# ── Remittance Email Ingestion ────────────────────────────────────────────────

def remittance_text(filename: str, file_bytes: bytes) -> str:
    ext = Path(filename).suffix.lower()
    if ext == ".eml":
        from extraction.email_parser import parse_eml_bytes
        return parse_eml_bytes(file_bytes)
    if ext == ".pdf":
        return extract_pdf_bytes_text(file_bytes)
    return file_bytes.decode("utf-8", errors="ignore")


def extract_remittance(text: str) -> tuple[dict, str, str]:
    """Return (result, extractor_name, raw_output)."""
    from extraction.llm_extractor import extract_remittance_data, llm_available
    rules = extract_remittance_rules(text)
    if llm_available():
        try:
            llm = extract_remittance_data(text)
            return merge_extractions(llm, rules), "llm+rules", json.dumps(llm, default=str)
        except Exception as e:
            return rules, "rules", json.dumps({"llm_error": str(e), "rules": rules}, default=str)
    return rules, "rules", json.dumps(rules, default=str)


def ingest_remittance(filename: str, file_bytes: bytes, batch_id: str | None = None,
                      allow_duplicate: bool = False) -> dict:
    """Parse a payment email / remittance advice and store the extracted remittance."""
    err = _precheck(filename, file_bytes, "remittance", allow_duplicate)
    if err:
        return err

    text = remittance_text(filename, file_bytes)
    if not text.strip():
        return {"success": False, "reason": "No readable text in file."}
    doc_id = insert_document({"filename": filename, "document_type": "remittance",
                              "file_hash": calculate_document_hash(file_bytes), "batch_id": batch_id})
    try:
        result, extractor, raw = extract_remittance(text)
    except Exception as e:
        return {"success": False, "document_id": doc_id, "reason": f"Extraction failed: {e}"}

    if not (result.get("payment_amount") or result.get("invoice_references") or result.get("utr")):
        insert_extraction({"document_id": doc_id, "extractor": extractor, "raw_output": raw,
                           "extraction_status": "failed"})
        return {"success": False, "document_id": doc_id, "extracted": result,
                "reason": "No payment amount, invoice reference or UTR found — is this a remittance advice?"}

    insert_extraction({"document_id": doc_id, "extractor": extractor, "raw_output": raw,
                       "validated_output": json.dumps(result, default=str), "extraction_status": "complete"})
    rid = insert_remittance({**result, "document_id": doc_id})
    create_audit_log("remittance", rid, "imported",
                     details={"source": filename, "extractor": extractor, "utr": result.get("utr"),
                              "invoice_references": result.get("invoice_references")})
    return {"success": True, "document_id": doc_id, "remittance_id": rid, "extracted": result,
            "extractor": extractor}


# ── TDS certificate storage ───────────────────────────────────────────────────

def store_supporting_document(filename: str, file_bytes: bytes, document_type: str = "tds_certificate") -> dict:
    """Persist a supporting document (e.g. Form 16A) and register it. Returns {"document_id", "path"}."""
    v = validate_file(filename, file_bytes, document_type)
    if not v["valid"]:
        return {"success": False, "reason": v["reason"]}
    file_hash = calculate_document_hash(file_bytes)
    existing = get_document_by_hash(file_hash)
    if existing:
        return {"success": True, "document_id": existing["document_id"], "path": existing.get("file_path")}
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name)
    path = UPLOADS_DIR / f"{file_hash[:12]}_{safe}"
    path.write_bytes(file_bytes)
    doc_id = insert_document({"filename": filename, "document_type": document_type,
                              "file_hash": file_hash, "file_path": str(path)})
    return {"success": True, "document_id": doc_id, "path": str(path)}


# ── Auto-detect upload ────────────────────────────────────────────────────────

def detect_document_type(filename: str, file_bytes: bytes) -> dict:
    """Classify a file by structure (tabular) or text keywords. Returns {"document_type", "confidence"}."""
    ext = Path(filename).suffix.lower()
    if ext in (".csv", ".xlsx", ".xls"):
        res = _with_tempfile(file_bytes, ext, classify_by_file_type)
        return res or {"document_type": "unknown", "confidence": 0.0}
    if ext == ".eml":
        return {"document_type": "remittance", "confidence": 0.95}
    text = extract_pdf_bytes_text(file_bytes) if ext == ".pdf" else file_bytes.decode("utf-8", errors="ignore")
    return classify_document_by_text(text)


def auto_ingest(filename: str, file_bytes: bytes, bank_name: str = "Unknown") -> dict:
    """Detect the document type and route to the right importer."""
    kind = detect_document_type(filename, file_bytes)
    dtype = kind["document_type"]
    if dtype == "bank_statement":
        res = ingest_bank_statement(filename, file_bytes, bank_name=bank_name)
    elif dtype == "invoice":
        res = ingest_invoice(filename, file_bytes)
    elif dtype == "remittance":
        res = ingest_remittance(filename, file_bytes)
    else:
        res = {"success": False, "reason": "Could not tell what kind of document this is — "
                                           "use the specific tab instead."}
    return {**res, "detected_type": dtype, "detection_confidence": kind.get("confidence", 0.0)}


# ── Synthetic Dataset Loader ──────────────────────────────────────────────────

DEMO_MARKER_HASH = "synthetic-demo-dataset-v2"


def demo_dataset_loaded() -> bool:
    return bool(get_document_by_hash(DEMO_MARKER_HASH))


def load_synthetic_dataset() -> dict:
    """Load the synthetic demo dataset directly into the database (idempotent)."""
    if demo_dataset_loaded():
        return {"already_loaded": True, "customers": 0, "invoices": 0, "transactions": 0,
                "emails": 0, "remittances_stored": 0, "ground_truth": []}

    # Import via file path to avoid any clash with a stdlib/third-party 'data' package
    spec = importlib.util.spec_from_file_location(
        "generate_dataset", Path(__file__).parent.parent / "data" / "synthetic" / "generate_dataset.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    customers, invoices, bank_txns, ground_truth, emails = mod.build_dataset(seed=42)

    marker = insert_document({"filename": "Demo dataset (synthetic)", "document_type": "demo",
                              "file_hash": DEMO_MARKER_HASH})

    for cust in customers:
        upsert_customer(name=cust["name"], gstin=cust.get("gstin"), variants=cust.get("variants", []))

    for inv in invoices:
        inv = dict(inv)
        inv["customer_id"] = upsert_customer(inv["customer_name"], gstin=inv.get("customer_gstin"))
        inv["source_document_id"] = marker
        insert_invoice(inv)

    for txn in bank_txns:
        insert_transaction({**txn, "source_document_id": marker})

    # Remittance emails: parse with the rule-based extractor (no LLM call needed for demo data)
    rem_count = 0
    for em in emails:
        content = em["content"]
        doc_id = insert_document({"filename": em["filename"], "document_type": "remittance",
                                  "file_hash": calculate_document_hash(content.encode("utf-8"))})
        result = extract_remittance_rules(content)
        result["utr"] = result.get("utr") or em.get("utr")
        insert_extraction({"document_id": doc_id, "extractor": "rules",
                           "raw_output": json.dumps(result, default=str),
                           "validated_output": json.dumps(result, default=str),
                           "extraction_status": "complete"})
        insert_remittance({**result, "document_id": doc_id})
        rem_count += 1

    create_audit_log("system", "dataset", "synthetic_dataset_loaded",
                     details={"customers": len(customers), "invoices": len(invoices),
                              "transactions": len(bank_txns), "emails": len(emails)})

    return {
        "already_loaded": False,
        "customers": len(customers), "invoices": len(invoices),
        "transactions": len(bank_txns), "emails": len(emails),
        "remittances_stored": rem_count,
        "ground_truth": ground_truth,
    }
