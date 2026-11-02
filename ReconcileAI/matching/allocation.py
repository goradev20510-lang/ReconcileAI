"""allocation.py — allocation helpers for multi-invoice payments."""
from __future__ import annotations
from decimal import Decimal

from database.repositories import insert_allocation, insert_settlement_component


def create_multi_invoice_allocation(transaction_id: str, invoice_combo: list[dict],
                                     credit_amount: float, tds_amount: float = 0.0,
                                     reviewer: str = "system") -> list[str]:
    """
    Allocate a single payment across multiple invoices proportionally.
    Returns list of created allocation IDs.
    """
    total_outstanding = sum(Decimal(str(inv["outstanding_amount"])) for inv in invoice_combo)
    credit = Decimal(str(credit_amount))
    allocation_ids = []

    for inv in invoice_combo:
        share = Decimal(str(inv["outstanding_amount"])) / total_outstanding
        alloc_amount = (credit * share).quantize(Decimal("0.01"))
        aid = insert_allocation({
            "transaction_id":    transaction_id,
            "invoice_id":        inv["invoice_id"],
            "allocated_amount":  float(alloc_amount),
            "allocation_status": "proposed",
            "confidence_score":  0.75,
            "decision_reason":   f"Proportional allocation across {len(invoice_combo)} invoices",
            "approved_by":       reviewer,
        })
        insert_settlement_component({"allocation_id": aid, "component_type": "bank_credit",
                                      "amount": float(alloc_amount)})
        allocation_ids.append(aid)

    return allocation_ids
