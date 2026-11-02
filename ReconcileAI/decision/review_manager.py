"""
ReconcileAI – Human Review Manager
Approve, reject, correct and reverse reconciliation decisions.
Every action is recorded in the audit log.
"""
from __future__ import annotations
from decimal import Decimal

from database.repositories import (
    get_open_review_cases, get_all_review_cases, update_review_case,
    update_allocation_status, get_allocations_for_transaction,
    update_invoice_outstanding, get_allocations_for_invoice,
    insert_allocation, insert_settlement_component,
    update_transaction_status, create_audit_log, get_invoice, get_transaction,
)
from validation.financial_rules import calculate_outstanding


def approve_match(case_id: str, allocation_id: str, reviewer: str = "human",
                  notes: str = "") -> dict:
    """
    Approve a proposed match. Finalises the allocation and updates invoice outstanding.
    """
    update_allocation_status(allocation_id, "approved", reviewer, notes)
    update_review_case(case_id, "approved", reviewer, notes)

    # Recalculate invoice outstanding
    alloc = next(
        (a for a in get_allocations_for_transaction(
            _get_transaction_for_alloc(allocation_id))
         if a["allocation_id"] == allocation_id), None
    )
    if alloc:
        invoice = get_invoice(alloc["invoice_id"])
        existing = get_allocations_for_invoice(alloc["invoice_id"])
        new_outstanding = calculate_outstanding(invoice["total_amount"], existing)
        new_status = "settled" if new_outstanding <= 0.01 else "partially_paid"
        update_invoice_outstanding(alloc["invoice_id"], new_outstanding, new_status)
        update_transaction_status(alloc["transaction_id"], "matched")
        create_audit_log("allocation", allocation_id, "human_approved",
                         actor=reviewer, details={"notes": notes})

    return {"status": "approved", "allocation_id": allocation_id}


def reject_match(case_id: str, allocation_id: str, reviewer: str = "human",
                 notes: str = "") -> dict:
    """
    Reject a proposed match. The transaction remains unmatched.
    """
    update_allocation_status(allocation_id, "rejected", reviewer, notes)
    update_review_case(case_id, "rejected", reviewer, notes)

    txn_id = _get_transaction_for_alloc(allocation_id)
    if txn_id:
        update_transaction_status(txn_id, "unmatched")
        create_audit_log("allocation", allocation_id, "human_rejected",
                         actor=reviewer, details={"notes": notes})

    return {"status": "rejected", "allocation_id": allocation_id}


def correct_match(case_id: str, transaction_id: str, new_invoice_id: str,
                  reviewer: str = "human", notes: str = "") -> dict:
    """
    Correct a match by selecting a different invoice.
    Rejects existing proposed allocations and creates a new approved one.
    """
    # Reject existing proposed allocations
    existing = get_allocations_for_transaction(transaction_id)
    for alloc in existing:
        if alloc["allocation_status"] in ("proposed",):
            update_allocation_status(alloc["allocation_id"], "rejected", reviewer, notes)

    txn = get_transaction(transaction_id)
    invoice = get_invoice(new_invoice_id)
    if not txn or not invoice:
        return {"status": "error", "reason": "Transaction or invoice not found"}

    credit = float(txn.get("credit_amount", 0))
    aid = insert_allocation({
        "transaction_id":    transaction_id,
        "invoice_id":        new_invoice_id,
        "allocated_amount":  credit,
        "allocation_status": "approved",
        "confidence_score":  1.0,
        "decision_reason":   f"Manually corrected by {reviewer}: {notes}",
        "approved_by":       reviewer,
    })
    insert_settlement_component({"allocation_id": aid, "component_type": "bank_credit", "amount": credit})

    existing_approved = get_allocations_for_invoice(new_invoice_id)
    new_outstanding = calculate_outstanding(invoice["total_amount"], existing_approved)
    new_status = "settled" if new_outstanding <= 0.01 else "partially_paid"
    update_invoice_outstanding(new_invoice_id, new_outstanding, new_status)
    update_transaction_status(transaction_id, "matched")
    update_review_case(case_id, "corrected", reviewer, notes)

    create_audit_log("transaction", transaction_id, "human_corrected",
                     actor=reviewer, details={"new_invoice_id": new_invoice_id, "notes": notes})

    return {"status": "corrected", "new_allocation_id": aid, "new_invoice_id": new_invoice_id}


def reverse_allocation(allocation_id: str, reviewer: str = "human", notes: str = "") -> dict:
    """
    Reverse an approved allocation (e.g., if it was incorrect).
    Reinstates the invoice outstanding and marks the transaction as unmatched.
    """
    from database.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM payment_allocations WHERE allocation_id=?", (allocation_id,)
    ).fetchone()
    if not row:
        return {"status": "error", "reason": "Allocation not found"}

    alloc = dict(row)
    invoice = get_invoice(alloc["invoice_id"])

    # Mark allocation as reversed (not deleted — audit trail preserved)
    update_allocation_status(allocation_id, "reversed", reviewer, notes)

    # Recalculate outstanding (excluding this reversed allocation)
    remaining = [a for a in get_allocations_for_invoice(alloc["invoice_id"])
                 if a["allocation_id"] != allocation_id]
    new_outstanding = calculate_outstanding(invoice["total_amount"], remaining)
    new_status = (
        "settled"       if new_outstanding <= 0.01 else
        "partially_paid" if new_outstanding < invoice["total_amount"] else
        "open"
    )
    update_invoice_outstanding(alloc["invoice_id"], new_outstanding, new_status)
    update_transaction_status(alloc["transaction_id"], "unmatched")

    create_audit_log("allocation", allocation_id, "reversed",
                     actor=reviewer, details={"notes": notes})

    return {"status": "reversed", "allocation_id": allocation_id,
            "invoice_id": alloc["invoice_id"], "new_outstanding": new_outstanding}


def _get_transaction_for_alloc(allocation_id: str) -> str | None:
    from database.db import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT transaction_id FROM payment_allocations WHERE allocation_id=?",
        (allocation_id,)
    ).fetchone()
    return row["transaction_id"] if row else None
