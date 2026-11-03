"""
ReconcileAI – Repository Layer
All database read/write operations. No SQL lives outside this file.
"""
from __future__ import annotations
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from database.db import get_connection


# ── Helpers ───────────────────────────────────────────────────────────────────

def _new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12].upper()}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _row_to_dict(row) -> dict:
    return dict(row) if row else {}


def _rows(rows) -> list[dict]:
    return [dict(r) for r in rows]


# ── Customer Repository ───────────────────────────────────────────────────────

def upsert_customer(name: str, gstin: str | None = None, pan: str | None = None,
                    email: str | None = None, variants: list[str] | None = None) -> str:
    conn = get_connection()
    name = (name or "Unknown").strip()
    # Try match by GSTIN first, then by name
    if gstin:
        row = conn.execute("SELECT customer_id FROM customers WHERE gstin = ?", (gstin,)).fetchone()
        if row:
            return row["customer_id"]
    row = conn.execute("SELECT customer_id, gstin, name_variants FROM customers WHERE name = ?",
                       (name,)).fetchone()
    if row:
        updates, params = [], []
        if gstin and not row["gstin"]:
            updates.append("gstin=?"); params.append(gstin)
        if variants:
            known = set(json.loads(row["name_variants"] or "[]"))
            if not set(variants) <= known:
                updates.append("name_variants=?"); params.append(json.dumps(sorted(known | set(variants))))
        if updates:
            conn.execute(f"UPDATE customers SET {', '.join(updates)} WHERE customer_id=?",
                         (*params, row["customer_id"]))
            conn.commit()
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
    return _rows(conn.execute("SELECT * FROM customers ORDER BY name").fetchall())


def get_customer_by_id(customer_id: str) -> dict:
    conn = get_connection()
    return _row_to_dict(conn.execute("SELECT * FROM customers WHERE customer_id=?", (customer_id,)).fetchone())


# ── Invoice Repository ────────────────────────────────────────────────────────

def invoice_exists(invoice_id: str) -> bool:
    conn = get_connection()
    return conn.execute("SELECT 1 FROM invoices WHERE invoice_id=?", (invoice_id,)).fetchone() is not None


def insert_invoice(data: dict) -> str:
    """
    Insert an invoice. If the invoice id already exists the existing row is kept
    unchanged — re-importing a file must never reset outstanding balances or
    status for invoices that have already received payments.
    """
    conn = get_connection()
    iid = data.get("invoice_id") or _new_id("INV")
    total = data.get("total_amount", 0) or 0
    conn.execute(
        """INSERT INTO invoices
           (invoice_id,customer_id,supplier_name,customer_name,customer_gstin,
            invoice_date,due_date,taxable_amount,gst_amount,total_amount,
            outstanding_amount,currency,status,source_document_id,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(invoice_id) DO NOTHING""",
        (iid, data.get("customer_id"), data.get("supplier_name"), data.get("customer_name"),
         data.get("customer_gstin"), data.get("invoice_date"), data.get("due_date"),
         data.get("taxable_amount", 0) or 0, data.get("gst_amount", 0) or 0, total,
         data.get("outstanding_amount", total) if data.get("outstanding_amount") is not None else total,
         data.get("currency", "INR") or "INR", data.get("status", "open") or "open",
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
        rows = conn.execute("SELECT * FROM invoices WHERE status=? ORDER BY rowid", (status,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM invoices ORDER BY rowid").fetchall()
    return _rows(rows)


def get_open_invoices() -> list[dict]:
    """Invoices that can still receive money (open or partially paid)."""
    conn = get_connection()
    return _rows(conn.execute(
        "SELECT * FROM invoices WHERE status IN ('open','partially_paid') ORDER BY rowid").fetchall())


def get_partially_paid_invoices(customer_id: str | None = None) -> list[dict]:
    conn = get_connection()
    if customer_id:
        rows = conn.execute(
            "SELECT * FROM invoices WHERE status='partially_paid' AND customer_id=?", (customer_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM invoices WHERE status='partially_paid'").fetchall()
    return _rows(rows)


def update_invoice_outstanding(invoice_id: str, new_outstanding: float, new_status: str) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE invoices SET outstanding_amount=?, status=? WHERE invoice_id=?",
        (round(float(new_outstanding), 2), new_status, invoice_id)
    )
    conn.commit()


# ── Bank Transaction Repository ───────────────────────────────────────────────

def transaction_exists(transaction_id: str) -> bool:
    conn = get_connection()
    return conn.execute("SELECT 1 FROM bank_transactions WHERE transaction_id=?",
                        (transaction_id,)).fetchone() is not None


def insert_transaction(data: dict) -> str:
    """Insert a bank line. Re-importing the identical line (same id) is a no-op."""
    conn = get_connection()
    tid = data.get("transaction_id") or _new_id("TXN")
    credit = float(data.get("credit_amount", 0) or 0)
    status = data.get("status", "unmatched")
    if credit <= 0 and status == "unmatched":
        status = "excluded"          # debits / zero lines are not receivables
    conn.execute(
        """INSERT OR IGNORE INTO bank_transactions
           (transaction_id,utr,narration,credit_amount,debit_amount,
            transaction_date,bank_name,account_number,currency,status,batch_id,
            source_document_id,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (tid, data.get("utr"), data.get("narration"), credit,
         data.get("debit_amount", 0) or 0, data.get("transaction_date"),
         data.get("bank_name"), data.get("account_number"),
         data.get("currency", "INR"), status,
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
        rows = conn.execute("SELECT * FROM bank_transactions WHERE status=? ORDER BY rowid",
                            (status,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM bank_transactions ORDER BY rowid").fetchall()
    return _rows(rows)


def get_transactions_imported_before(transaction_id: str) -> list[dict]:
    """Transactions stored before this one (import order). Used for duplicate checks."""
    conn = get_connection()
    row = conn.execute("SELECT rowid FROM bank_transactions WHERE transaction_id=?",
                       (transaction_id,)).fetchone()
    if not row:
        return get_all_transactions()
    return _rows(conn.execute(
        "SELECT * FROM bank_transactions WHERE rowid < ? AND status != 'excluded' ORDER BY rowid",
        (row[0],)).fetchall())


def get_unmatched_transactions() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM bank_transactions WHERE status='unmatched' AND credit_amount > 0 ORDER BY rowid"
    ).fetchall()
    return _rows(rows)


def update_transaction_status(transaction_id: str, status: str) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE bank_transactions SET status=? WHERE transaction_id=?", (status, transaction_id)
    )
    conn.commit()


# ── Document Repository ───────────────────────────────────────────────────────

def get_document_by_hash(file_hash: str) -> dict:
    conn = get_connection()
    return _row_to_dict(conn.execute("SELECT * FROM documents WHERE file_hash=?", (file_hash,)).fetchone())


def insert_document(data: dict) -> str:
    conn = get_connection()
    existing = get_document_by_hash(data["file_hash"])
    if existing:
        return existing["document_id"]
    did = data.get("document_id") or _new_id("DOC")
    conn.execute(
        """INSERT INTO documents(document_id,filename,document_type,file_hash,file_path,upload_time,batch_id)
           VALUES(?,?,?,?,?,?,?)""",
        (did, data["filename"], data.get("document_type"), data["file_hash"],
         data.get("file_path"), _now(), data.get("batch_id"))
    )
    conn.commit()
    return did


def get_all_documents() -> list[dict]:
    conn = get_connection()
    return _rows(conn.execute("SELECT * FROM documents ORDER BY upload_time DESC, rowid DESC").fetchall())


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


def get_extractions_for_document(document_id: str) -> list[dict]:
    conn = get_connection()
    return _rows(conn.execute("SELECT * FROM extractions WHERE document_id=? ORDER BY created_at",
                              (document_id,)).fetchall())


# ── Remittance Repository ─────────────────────────────────────────────────────

def insert_remittance(data: dict) -> str:
    conn = get_connection()
    rid = _new_id("REM")
    conn.execute(
        """INSERT INTO remittance_records
           (remittance_id,document_id,customer_name,customer_gstin,invoice_references,payment_amount,
            tds_amount,tds_section,utr,payment_date,currency,evidence_text,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (rid, data.get("document_id"), data.get("customer_name"), data.get("customer_gstin"),
         json.dumps(data.get("invoice_references") or []),
         data.get("payment_amount"), data.get("tds_amount") or 0, data.get("tds_section"),
         data.get("utr"), data.get("payment_date"), data.get("currency") or "INR",
         json.dumps(data.get("evidence") or []), _now())
    )
    conn.commit()
    return rid


def get_remittance_by_utr(utr: str) -> dict | None:
    if not utr:
        return None
    conn = get_connection()
    row = conn.execute("SELECT * FROM remittance_records WHERE utr=? ORDER BY rowid DESC LIMIT 1",
                       (utr,)).fetchone()
    return _row_to_dict(row) if row else None


def find_remittances_by_amount(amount: float, around_date: str | None = None,
                               window_days: int = 10) -> list[dict]:
    """
    Remittances *without* a UTR whose stated payment amount equals `amount`.
    Used to link remittance emails that never quoted the bank reference.
    """
    conn = get_connection()
    rows = _rows(conn.execute(
        """SELECT * FROM remittance_records
           WHERE (utr IS NULL OR utr = '') AND ABS(COALESCE(payment_amount,0) - ?) < 0.01""",
        (float(amount),)).fetchall())
    if not around_date:
        return rows
    try:
        from dateutil import parser as dp
        d = dp.parse(around_date).date()
    except Exception:
        return rows
    out = []
    for r in rows:
        if not r.get("payment_date"):
            out.append(r)
            continue
        try:
            if abs((dp.parse(r["payment_date"]).date() - d).days) <= window_days:
                out.append(r)
        except Exception:
            out.append(r)
    return out


def get_all_remittances() -> list[dict]:
    conn = get_connection()
    return _rows(conn.execute("SELECT * FROM remittance_records ORDER BY rowid").fetchall())


# ── Allocation Repository ─────────────────────────────────────────────────────

def insert_allocation(data: dict) -> str:
    conn = get_connection()
    aid = _new_id("ALLOC")
    conn.execute(
        """INSERT INTO payment_allocations
           (allocation_id,transaction_id,invoice_id,allocated_amount,allocation_status,
            confidence_score,decision_reason,approved_by,approved_at,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (aid, data["transaction_id"], data["invoice_id"], round(float(data["allocated_amount"]), 2),
         data.get("allocation_status", "proposed"), data.get("confidence_score"),
         data.get("decision_reason"), data.get("approved_by"),
         _now() if data.get("allocation_status") == "approved" else None, _now())
    )
    conn.commit()
    return aid


def get_allocation(allocation_id: str) -> dict:
    conn = get_connection()
    return _row_to_dict(conn.execute("SELECT * FROM payment_allocations WHERE allocation_id=?",
                                     (allocation_id,)).fetchone())


def get_all_allocations(status: str | None = None) -> list[dict]:
    conn = get_connection()
    if status:
        return _rows(conn.execute("SELECT * FROM payment_allocations WHERE allocation_status=? ORDER BY rowid",
                                  (status,)).fetchall())
    return _rows(conn.execute("SELECT * FROM payment_allocations ORDER BY rowid").fetchall())


def get_allocations_for_transaction(transaction_id: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM payment_allocations WHERE transaction_id=? ORDER BY rowid", (transaction_id,)
    ).fetchall()
    return _rows(rows)


def get_allocations_for_invoice(invoice_id: str) -> list[dict]:
    """Approved allocations only — these are the ones that reduce the balance."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM payment_allocations WHERE invoice_id=? AND allocation_status='approved' ORDER BY rowid",
        (invoice_id,)
    ).fetchall()
    return _rows(rows)


def get_rejected_invoice_ids(transaction_id: str) -> set[str]:
    """Invoices a reviewer already rejected (or reversed) for this transaction."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT DISTINCT invoice_id FROM payment_allocations
           WHERE transaction_id=? AND allocation_status IN ('rejected','reversed')""",
        (transaction_id,)).fetchall()
    return {r["invoice_id"] for r in rows}


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
        (cid, data["allocation_id"], data["component_type"], round(float(data["amount"]), 2),
         data.get("tds_section"), data.get("tds_status"), data.get("supporting_doc"),
         data.get("notes"), _now())
    )
    conn.commit()
    return cid


def get_settlement_components(allocation_id: str) -> list[dict]:
    conn = get_connection()
    return _rows(conn.execute("SELECT * FROM settlement_components WHERE allocation_id=? ORDER BY rowid",
                              (allocation_id,)).fetchall())


def get_tds_components(status: str | None = None) -> list[dict]:
    """TDS components on approved allocations, joined with invoice + transaction context."""
    conn = get_connection()
    sql = """SELECT sc.*, pa.invoice_id, pa.transaction_id, pa.allocation_status,
                    i.customer_name, i.total_amount AS invoice_total
             FROM settlement_components sc
             JOIN payment_allocations pa ON sc.allocation_id = pa.allocation_id
             LEFT JOIN invoices i ON i.invoice_id = pa.invoice_id
             WHERE sc.component_type='tds' AND pa.allocation_status='approved'"""
    params: tuple = ()
    if status:
        sql += " AND sc.tds_status=?"
        params = (status,)
    return _rows(conn.execute(sql + " ORDER BY sc.rowid", params).fetchall())


def update_settlement_component(component_id: str, **fields: Any) -> None:
    allowed = {"tds_status", "supporting_doc", "notes", "tds_section"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    conn = get_connection()
    conn.execute(f"UPDATE settlement_components SET {', '.join(f'{k}=?' for k in sets)} WHERE component_id=?",
                 (*sets.values(), component_id))
    conn.commit()


def get_all_settlement_components() -> list[dict]:
    conn = get_connection()
    return _rows(conn.execute("SELECT * FROM settlement_components ORDER BY rowid").fetchall())


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


def get_review_case(case_id: str) -> dict:
    conn = get_connection()
    return _row_to_dict(conn.execute("SELECT * FROM review_cases WHERE case_id=?", (case_id,)).fetchone())


def get_open_review_cases() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM review_cases WHERE status='open' ORDER BY created_at, rowid"
    ).fetchall()
    return _rows(rows)


def get_open_cases_for_transaction(transaction_id: str) -> list[dict]:
    conn = get_connection()
    return _rows(conn.execute(
        "SELECT * FROM review_cases WHERE transaction_id=? AND status='open' ORDER BY rowid",
        (transaction_id,)).fetchall())


def get_all_review_cases() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM review_cases ORDER BY created_at DESC, rowid DESC").fetchall()
    return _rows(rows)


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
        (lid, entity_type, entity_id, action, actor, json.dumps(details or {}, default=str), _now())
    )
    conn.commit()
    return lid


def get_audit_logs(entity_id: str | None = None, limit: int = 200,
                   entity_type: str | None = None, action: str | None = None,
                   search: str | None = None) -> list[dict]:
    conn = get_connection()
    where, params = [], []
    if entity_id:
        where.append("entity_id=?"); params.append(entity_id)
    if entity_type:
        where.append("entity_type=?"); params.append(entity_type)
    if action:
        where.append("action=?"); params.append(action)
    if search:
        where.append("(entity_id LIKE ? OR details LIKE ? OR action LIKE ?)")
        params += [f"%{search}%"] * 3
    sql = "SELECT * FROM audit_logs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    return _rows(conn.execute(sql, (*params, limit)).fetchall())


def get_audit_actions() -> list[str]:
    conn = get_connection()
    return [r[0] for r in conn.execute("SELECT DISTINCT action FROM audit_logs ORDER BY action").fetchall()]


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


def get_all_batches() -> list[dict]:
    conn = get_connection()
    return _rows(conn.execute("SELECT * FROM reconciliation_batches ORDER BY started_at DESC, rowid DESC").fetchall())


def get_reconciliation_summary() -> dict:
    conn = get_connection()
    q = lambda sql: conn.execute(sql).fetchone()[0]
    return {
        "total_transactions": q("SELECT COUNT(*) FROM bank_transactions WHERE status != 'excluded'"),
        "matched":            q("SELECT COUNT(*) FROM bank_transactions WHERE status='matched'"),
        "needs_review":       q("SELECT COUNT(*) FROM bank_transactions WHERE status='review'"),
        "unmatched":          q("SELECT COUNT(*) FROM bank_transactions WHERE status='unmatched'"),
        "duplicates":         q("SELECT COUNT(*) FROM bank_transactions WHERE status='duplicate'"),
        "excluded":           q("SELECT COUNT(*) FROM bank_transactions WHERE status='excluded'"),
        "invoices_total":     q("SELECT COUNT(*) FROM invoices"),
        "invoices_open":      q("SELECT COUNT(*) FROM invoices WHERE status='open'"),
        "invoices_partial":   q("SELECT COUNT(*) FROM invoices WHERE status='partially_paid'"),
        "invoices_settled":   q("SELECT COUNT(*) FROM invoices WHERE status='settled'"),
        "invoices_overpaid":  q("SELECT COUNT(*) FROM invoices WHERE status='overpaid'"),
        "open_review_cases":  q("SELECT COUNT(*) FROM review_cases WHERE status='open'"),
        "amount_received":    round(q("SELECT COALESCE(SUM(credit_amount),0) FROM bank_transactions WHERE status != 'excluded'"), 2),
        "amount_matched":     round(q("SELECT COALESCE(SUM(credit_amount),0) FROM bank_transactions WHERE status='matched'"), 2),
        "amount_in_review":   round(q("SELECT COALESCE(SUM(credit_amount),0) FROM bank_transactions WHERE status='review'"), 2),
        "amount_unmatched":   round(q("SELECT COALESCE(SUM(credit_amount),0) FROM bank_transactions WHERE status='unmatched'"), 2),
        "invoice_value":      round(q("SELECT COALESCE(SUM(total_amount),0) FROM invoices"), 2),
        "outstanding_value":  round(q("SELECT COALESCE(SUM(outstanding_amount),0) FROM invoices WHERE status IN ('open','partially_paid')"), 2),
        "tds_pending":        round(q("""SELECT COALESCE(SUM(sc.amount),0) FROM settlement_components sc
                                         JOIN payment_allocations pa ON pa.allocation_id = sc.allocation_id
                                         WHERE sc.component_type='tds' AND pa.allocation_status='approved'
                                           AND COALESCE(sc.tds_status,'') != 'verified'"""), 2),
    }


def update_allocation_amount(allocation_id: str, new_amount: float) -> None:
    conn = get_connection()
    conn.execute("UPDATE payment_allocations SET allocated_amount=? WHERE allocation_id=?",
                 (round(float(new_amount), 2), allocation_id))
    conn.commit()


def get_settlement_component(component_id: str) -> dict:
    conn = get_connection()
    return _row_to_dict(conn.execute("SELECT * FROM settlement_components WHERE component_id=?",
                                     (component_id,)).fetchone())
