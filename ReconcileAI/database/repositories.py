"""
ReconcileAI – Repository Layer
All database read/write operations. No SQL lives outside this file.
"""
from __future__ import annotations
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from database.db import get_connection


# ── Helpers ───────────────────────────────────────────────────────────────────

def _new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12].upper()}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _row_to_dict(row) -> dict:
    return dict(row) if row else {}


# ── Customer Repository ───────────────────────────────────────────────────────

def upsert_customer(name: str, gstin: str | None = None, pan: str | None = None,
                    email: str | None = None, variants: list[str] | None = None) -> str:
    conn = get_connection()
    # Try match by GSTIN first, then by name
    if gstin:
        row = conn.execute("SELECT customer_id FROM customers WHERE gstin = ?", (gstin,)).fetchone()
        if row:
            return row["customer_id"]
    row = conn.execute("SELECT customer_id FROM customers WHERE name = ?", (name,)).fetchone()
    if row:
        return row["customer_id"]
    cid = _new_id("CUST")
    conn.execute(
        "INSERT INTO customers(customer_id,name,name_variants,gstin,pan,email,created_at) VALUES(?,?,?,?,?,?,?)",
        (cid, name, json.dumps(variants or []), gstin, pan, email, _now())
    )
    conn.commit()
    return cid


def get_all_customers() -> list[dict]:
    conn = get_connection()
    return [_row_to_dict(r) for r in conn.execute("SELECT * FROM customers").fetchall()]


def get_customer_by_id(customer_id: str) -> dict:
    conn = get_connection()
    return _row_to_dict(conn.execute("SELECT * FROM customers WHERE customer_id=?", (customer_id,)).fetchone())


# ── Invoice Repository ────────────────────────────────────────────────────────

def insert_invoice(data: dict) -> str:
    conn = get_connection()
    iid = data.get("invoice_id") or _new_id("INV")
    conn.execute(
        """INSERT OR REPLACE INTO invoices
           (invoice_id,customer_id,supplier_name,customer_name,customer_gstin,
            invoice_date,due_date,taxable_amount,gst_amount,total_amount,
            outstanding_amount,currency,status,source_document_id,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (iid, data.get("customer_id"), data.get("supplier_name"), data.get("customer_name"),
         data.get("customer_gstin"), data.get("invoice_date"), data.get("due_date"),
         data.get("taxable_amount", 0), data.get("gst_amount", 0), data.get("total_amount", 0),
         data.get("outstanding_amount", data.get("total_amount", 0)),
         data.get("currency", "INR"), data.get("status", "open"),
         data.get("source_document_id"), _now())
    )
    conn.commit()
    return iid


def get_invoice(invoice_id: str) -> dict:
    conn = get_connection()
    return _row_to_dict(conn.execute("SELECT * FROM invoices WHERE invoice_id=?", (invoice_id,)).fetchone())


def get_all_invoices(status: str | None = None) -> list[dict]:
    conn = get_connection()
    if status:
        rows = conn.execute("SELECT * FROM invoices WHERE status=?", (status,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM invoices").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_partially_paid_invoices(customer_id: str | None = None) -> list[dict]:
    conn = get_connection()
    if customer_id:
        rows = conn.execute(
            "SELECT * FROM invoices WHERE status='partially_paid' AND customer_id=?", (customer_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM invoices WHERE status='partially_paid'").fetchall()
    return [_row_to_dict(r) for r in rows]


def update_invoice_outstanding(invoice_id: str, new_outstanding: float, new_status: str) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE invoices SET outstanding_amount=?, status=? WHERE invoice_id=?",
        (new_outstanding, new_status, invoice_id)
    )
    conn.commit()


# ── Bank Transaction Repository ───────────────────────────────────────────────

def insert_transaction(data: dict) -> str:
    conn = get_connection()
    tid = data.get("transaction_id") or _new_id("TXN")
    conn.execute(
        """INSERT OR IGNORE INTO bank_transactions
           (transaction_id,utr,narration,credit_amount,debit_amount,
            transaction_date,bank_name,account_number,currency,status,batch_id,
            source_document_id,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (tid, data.get("utr"), data.get("narration"), data.get("credit_amount", 0),
         data.get("debit_amount", 0), data.get("transaction_date"),
         data.get("bank_name"), data.get("account_number"),
         data.get("currency", "INR"), data.get("status", "unmatched"),
         data.get("batch_id"), data.get("source_document_id"), _now())
    )
    conn.commit()
    return tid


def get_transaction(transaction_id: str) -> dict:
    conn = get_connection()
    return _row_to_dict(conn.execute(
        "SELECT * FROM bank_transactions WHERE transaction_id=?", (transaction_id,)
    ).fetchone())


def get_all_transactions(status: str | None = None) -> list[dict]:
    conn = get_connection()
    if status:
        rows = conn.execute("SELECT * FROM bank_transactions WHERE status=?", (status,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM bank_transactions").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_unmatched_transactions() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM bank_transactions WHERE status='unmatched'"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_transaction_status(transaction_id: str, status: str) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE bank_transactions SET status=? WHERE transaction_id=?", (status, transaction_id)
    )
    conn.commit()


# ── Document Repository ───────────────────────────────────────────────────────

def insert_document(data: dict) -> str:
    conn = get_connection()
    did = data.get("document_id") or _new_id("DOC")
    try:
        conn.execute(
            """INSERT INTO documents(document_id,filename,document_type,file_hash,file_path,upload_time,batch_id)
               VALUES(?,?,?,?,?,?,?)""",
            (did, data["filename"], data.get("document_type"), data["file_hash"],
             data.get("file_path"), _now(), data.get("batch_id"))
        )
        conn.commit()
    except Exception:
        # Duplicate hash → return existing document id
        row = conn.execute("SELECT document_id FROM documents WHERE file_hash=?", (data["file_hash"],)).fetchone()
        if row:
            return row["document_id"]
    return did


def get_all_documents() -> list[dict]:
    conn = get_connection()
    return [_row_to_dict(r) for r in conn.execute("SELECT * FROM documents ORDER BY upload_time DESC").fetchall()]


# ── Extraction Repository ─────────────────────────────────────────────────────

def insert_extraction(data: dict) -> str:
    conn = get_connection()
    eid = _new_id("EXT")
    conn.execute(
        """INSERT INTO extractions(extraction_id,document_id,extractor,raw_output,validated_output,
           extraction_status,missing_fields,created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (eid, data.get("document_id"), data.get("extractor"), data.get("raw_output"),
         data.get("validated_output"), data.get("extraction_status", "complete"),
         json.dumps(data.get("missing_fields", [])), _now())
    )
    conn.commit()
    return eid


# ── Remittance Repository ─────────────────────────────────────────────────────

def insert_remittance(data: dict) -> str:
    conn = get_connection()
    rid = _new_id("REM")
    conn.execute(
        """INSERT INTO remittance_records
           (remittance_id,document_id,customer_name,invoice_references,payment_amount,
            tds_amount,utr,payment_date,currency,evidence_text,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (rid, data.get("document_id"), data.get("customer_name"),
         json.dumps(data.get("invoice_references", [])),
         data.get("payment_amount"), data.get("tds_amount", 0), data.get("utr"),
         data.get("payment_date"), data.get("currency", "INR"),
         json.dumps(data.get("evidence", [])), _now())
    )
    conn.commit()
    return rid


def get_remittance_by_utr(utr: str) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM remittance_records WHERE utr=?", (utr,)).fetchone()
    return _row_to_dict(row) if row else None


def get_all_remittances() -> list[dict]:
    conn = get_connection()
    return [_row_to_dict(r) for r in conn.execute("SELECT * FROM remittance_records").fetchall()]


# ── Allocation Repository ─────────────────────────────────────────────────────

def insert_allocation(data: dict) -> str:
    conn = get_connection()
    aid = _new_id("ALLOC")
    conn.execute(
        """INSERT INTO payment_allocations
           (allocation_id,transaction_id,invoice_id,allocated_amount,allocation_status,
            confidence_score,decision_reason,approved_by,created_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (aid, data["transaction_id"], data["invoice_id"], data["allocated_amount"],
         data.get("allocation_status", "proposed"), data.get("confidence_score"),
         data.get("decision_reason"), data.get("approved_by"), _now())
    )
    conn.commit()
    return aid


def get_allocations_for_transaction(transaction_id: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM payment_allocations WHERE transaction_id=?", (transaction_id,)
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_allocations_for_invoice(invoice_id: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM payment_allocations WHERE invoice_id=? AND allocation_status='approved'",
        (invoice_id,)
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_allocation_status(allocation_id: str, status: str, reviewer: str = "system",
                             notes: str | None = None) -> None:
    conn = get_connection()
    conn.execute(
        """UPDATE payment_allocations
           SET allocation_status=?, approved_by=?, approved_at=?
           WHERE allocation_id=?""",
        (status, reviewer, _now(), allocation_id)
    )
    conn.commit()


# ── Settlement Components ─────────────────────────────────────────────────────

def insert_settlement_component(data: dict) -> str:
    conn = get_connection()
    cid = _new_id("SETL")
    conn.execute(
        """INSERT INTO settlement_components
           (component_id,allocation_id,component_type,amount,tds_section,
            tds_status,supporting_doc,notes,created_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (cid, data["allocation_id"], data["component_type"], data["amount"],
         data.get("tds_section"), data.get("tds_status"), data.get("supporting_doc"),
         data.get("notes"), _now())
    )
    conn.commit()
    return cid


# ── Review Cases ──────────────────────────────────────────────────────────────

def insert_review_case(data: dict) -> str:
    conn = get_connection()
    cid = _new_id("CASE")
    conn.execute(
        """INSERT INTO review_cases
           (case_id,transaction_id,invoice_id,exception_type,exception_summary,
            suggested_action,status,created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (cid, data.get("transaction_id"), data.get("invoice_id"),
         data["exception_type"], data.get("exception_summary"),
         data.get("suggested_action"), "open", _now())
    )
    conn.commit()
    return cid


def get_open_review_cases() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM review_cases WHERE status='open' ORDER BY created_at DESC"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_all_review_cases() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM review_cases ORDER BY created_at DESC").fetchall()
    return [_row_to_dict(r) for r in rows]


def update_review_case(case_id: str, status: str, reviewer: str, notes: str) -> None:
    conn = get_connection()
    conn.execute(
        """UPDATE review_cases
           SET status=?, reviewer=?, reviewed_at=?, resolution_notes=?
           WHERE case_id=?""",
        (status, reviewer, _now(), notes, case_id)
    )
    conn.commit()


# ── Audit Logs ────────────────────────────────────────────────────────────────

def create_audit_log(entity_type: str, entity_id: str, action: str,
                     actor: str = "system", details: dict | None = None) -> str:
    conn = get_connection()
    lid = _new_id("LOG")
    conn.execute(
        """INSERT INTO audit_logs(log_id,entity_type,entity_id,action,actor,details,created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (lid, entity_type, entity_id, action, actor, json.dumps(details or {}), _now())
    )
    conn.commit()
    return lid


def get_audit_logs(entity_id: str | None = None, limit: int = 200) -> list[dict]:
    conn = get_connection()
    if entity_id:
        rows = conn.execute(
            "SELECT * FROM audit_logs WHERE entity_id=? ORDER BY created_at DESC LIMIT ?",
            (entity_id, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ── Reconciliation Batch ──────────────────────────────────────────────────────

def create_batch() -> str:
    conn = get_connection()
    bid = _new_id("BATCH")
    conn.execute(
        "INSERT INTO reconciliation_batches(batch_id,started_at,status) VALUES(?,?,?)",
        (bid, _now(), "running")
    )
    conn.commit()
    return bid


def update_batch(batch_id: str, stats: dict) -> None:
    conn = get_connection()
    conn.execute(
        """UPDATE reconciliation_batches
           SET completed_at=?,total_transactions=?,matched=?,needs_review=?,unmatched=?,status=?
           WHERE batch_id=?""",
        (_now(), stats.get("total", 0), stats.get("matched", 0),
         stats.get("needs_review", 0), stats.get("unmatched", 0),
         "completed", batch_id)
    )
    conn.commit()


def get_reconciliation_summary() -> dict:
    conn = get_connection()
    total    = conn.execute("SELECT COUNT(*) FROM bank_transactions").fetchone()[0]
    matched  = conn.execute("SELECT COUNT(*) FROM bank_transactions WHERE status='matched'").fetchone()[0]
    review   = conn.execute("SELECT COUNT(*) FROM bank_transactions WHERE status='review'").fetchone()[0]
    unmatched= conn.execute("SELECT COUNT(*) FROM bank_transactions WHERE status='unmatched'").fetchone()[0]
    duplicate= conn.execute("SELECT COUNT(*) FROM bank_transactions WHERE status='duplicate'").fetchone()[0]
    inv_open = conn.execute("SELECT COUNT(*) FROM invoices WHERE status='open'").fetchone()[0]
    inv_part = conn.execute("SELECT COUNT(*) FROM invoices WHERE status='partially_paid'").fetchone()[0]
    inv_settled = conn.execute("SELECT COUNT(*) FROM invoices WHERE status='settled'").fetchone()[0]
    open_cases = conn.execute("SELECT COUNT(*) FROM review_cases WHERE status='open'").fetchone()[0]
    return {
        "total_transactions": total, "matched": matched, "needs_review": review,
        "unmatched": unmatched, "duplicates": duplicate,
        "invoices_open": inv_open, "invoices_partial": inv_part, "invoices_settled": inv_settled,
        "open_review_cases": open_cases,
    }
