"""
ReconcileAI – Settlement Ledger
Recalculates and finalises invoice settlement state after allocations change.
"""
from __future__ import annotations
from decimal import Decimal

from database.repositories import (
    get_allocations_for_invoice, get_invoice, update_invoice_outstanding, create_audit_log
)


def calculate_settlement_balance(invoice_id: str) -> dict:
    """
    Recalculate the full settlement state of an invoice.

    Returns
    -------
    {
        "invoice_id": str,
        "total_amount": float,
        "total_paid": float,
        "outstanding": float,
        "status": "open" | "partially_paid" | "settled" | "overpaid",
        "components": list of allocation summaries
    }
    """
    invoice = get_invoice(invoice_id)
    if not invoice:
        return {"error": f"Invoice {invoice_id} not found"}

    allocs = get_allocations_for_invoice(invoice_id)   # only 'approved'
    total_paid = sum(Decimal(str(a["allocated_amount"])) for a in allocs)
    total      = Decimal(str(invoice["total_amount"]))
    outstanding = total - total_paid

    if outstanding < 0:
        status = "overpaid"
    elif outstanding <= Decimal("0.01"):
        status = "settled"
    elif total_paid > 0:
        status = "partially_paid"
    else:
        status = "open"

    return {
        "invoice_id":   invoice_id,
        "total_amount": float(total),
        "total_paid":   float(total_paid),
        "outstanding":  float(max(outstanding, Decimal("0"))),
        "overpaid_by":  float(abs(min(outstanding, Decimal("0")))),
        "status":       status,
        "allocations":  len(allocs),
    }


def finalise_settlement(invoice_id: str) -> dict:
    """Recalculate and persist the settlement state for an invoice."""
    result = calculate_settlement_balance(invoice_id)
    if "error" in result:
        return result

    update_invoice_outstanding(invoice_id, result["outstanding"], result["status"])
    create_audit_log("invoice", invoice_id, "settlement_recalculated",
                     details=result)
    return result
