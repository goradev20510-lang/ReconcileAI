"""
ReconcileAI – Human Review Manager
Approve, reject, correct, reverse and dismiss reconciliation decisions, and
manage TDS evidence. Every action is recorded in the audit log, and invoice
balances are always recomputed from approved allocations (never edited by hand).
"""
from __future__ import annotations
from decimal import Decimal

from database.repositories import (
    get_review_case, update_review_case, update_allocation_status,
    get_allocations_for_transaction, get_allocation, insert_allocation,
    insert_settlement_component, update_transaction_status, create_audit_log,
    get_invoice, get_transaction, get_settlement_component, update_settlement_component,
    update_allocation_amount, get_open_cases_for_transaction,
)
from validation.settlement import finalise_settlement


class ReviewError(ValueError):
    """A review action that cannot be applied (bad state or input)."""


def _case(case_id: str) -> dict:
    case = get_review_case(case_id)
    if not case:
        raise ReviewError(f"Review case {case_id} not found")
    return case


def _proposed(transaction_id: str) -> list[dict]:
    return [a for a in get_allocations_for_transaction(transaction_id) if a["allocation_status"] == "proposed"]


def _close_other_cases(transaction_id: str, keep: str, reviewer: str) -> None:
    for c in get_open_cases_for_transaction(transaction_id):
        if c["case_id"] != keep:
            update_review_case(c["case_id"], "superseded", reviewer, "Resolved via another case")


# ── Approve / Reject ──────────────────────────────────────────────────────────

def approve_match(case_id: str, allocation_id: str | None = None, reviewer: str = "human",
                  notes: str = "") -> dict:
    """
    Approve the proposed allocation(s) of a review case. A bulk payment has one
    proposed allocation per invoice; all of them are approved together.
    """
    case = _case(case_id)
    if case["status"] != "open":
        raise ReviewError(f"Case {case_id} is already {case['status']}")
    tid = case["transaction_id"]
    proposed = _proposed(tid)
    if allocation_id:
        proposed = [a for a in proposed if a["allocation_id"] == allocation_id] or proposed
    if not proposed:
        raise ReviewError("Nothing to approve — this case has no proposed allocation. Use Correct instead.")

    invoices = []
    for alloc in proposed:
        update_allocation_status(alloc["allocation_id"], "approved", reviewer, notes)
        invoices.append(alloc["invoice_id"])
    settlements = {iid: finalise_settlement(iid, actor=reviewer) for iid in dict.fromkeys(invoices)}
    update_transaction_status(tid, "matched")
    update_review_case(case_id, "approved", reviewer, notes)
    _close_other_cases(tid, case_id, reviewer)
    create_audit_log("transaction", tid, "human_approved", actor=reviewer,
                     details={"case_id": case_id, "allocations": [a["allocation_id"] for a in proposed],
                              "invoices": invoices, "notes": notes})
    return {"status": "approved", "allocation_ids": [a["allocation_id"] for a in proposed],
            "invoices": {k: v.get("status") for k, v in settlements.items()}}


def reject_match(case_id: str, allocation_id: str | None = None, reviewer: str = "human",
                 notes: str = "") -> dict:
    """
    Reject the proposed allocation(s). The transaction goes back to 'unmatched' and
    the rejected invoices are excluded the next time it is reconciled.
    """
    case = _case(case_id)
    if case["status"] != "open":
        raise ReviewError(f"Case {case_id} is already {case['status']}")
    tid = case["transaction_id"]
    proposed = _proposed(tid)
    for alloc in proposed:
        update_allocation_status(alloc["allocation_id"], "rejected", reviewer, notes)
    update_review_case(case_id, "rejected", reviewer, notes)
    _close_other_cases(tid, case_id, reviewer)
    update_transaction_status(tid, "unmatched")
    create_audit_log("transaction", tid, "human_rejected", actor=reviewer,
                     details={"case_id": case_id, "rejected_invoices": [a["invoice_id"] for a in proposed],
                              "notes": notes})
    return {"status": "rejected", "allocation_ids": [a["allocation_id"] for a in proposed]}


# ── Correct ───────────────────────────────────────────────────────────────────

def correct_match(case_id: str, transaction_id: str, new_invoice_id: str | list[str],
                  reviewer: str = "human", notes: str = "", tds_amount: float = 0.0,
                  tds_section: str | None = None) -> dict:
    """
    Allocate the transaction to the invoice(s) the reviewer chose. Proposed
    allocations are rejected. With several invoices the money is applied to each
    invoice's outstanding balance in the order given; any excess stays on the last.
    """
    ids = [new_invoice_id] if isinstance(new_invoice_id, str) else list(new_invoice_id)
    ids = [i for i in dict.fromkeys(ids) if i]
    if not ids:
        raise ReviewError("Choose at least one invoice")
    txn = get_transaction(transaction_id)
    if not txn:
        raise ReviewError("Transaction not found")
    invoices = [get_invoice(i) for i in ids]
    missing = [i for i, inv in zip(ids, invoices) if not inv]
    if missing:
        raise ReviewError(f"Invoice not found: {', '.join(missing)}")
    if tds_amount < 0:
        raise ReviewError("TDS cannot be negative")

    for alloc in _proposed(transaction_id):
        update_allocation_status(alloc["allocation_id"], "rejected", reviewer, notes)

    credit = Decimal(str(txn["credit_amount"]))
    tds = Decimal(str(tds_amount or 0))
    remaining = credit + tds
    created = []
    for k, inv in enumerate(invoices):
        last = k == len(invoices) - 1
        portion = remaining if last else min(remaining, Decimal(str(inv["outstanding_amount"])))
        if portion <= 0:
            break
        remaining -= portion
        tds_part = (tds * portion / (credit + tds)).quantize(Decimal("0.01")) if tds > 0 else Decimal("0")
        aid = insert_allocation({
            "transaction_id": transaction_id, "invoice_id": inv["invoice_id"],
            "allocated_amount": float(portion), "allocation_status": "approved",
            "confidence_score": 1.0,
            "decision_reason": f"Manually allocated by {reviewer}" + (f": {notes}" if notes else ""),
            "approved_by": reviewer,
        })
        insert_settlement_component({"allocation_id": aid, "component_type": "bank_credit",
                                     "amount": float(portion - tds_part)})
        if tds_part > 0:
            insert_settlement_component({"allocation_id": aid, "component_type": "tds", "amount": float(tds_part),
                                         "tds_status": "claimed", "tds_section": tds_section})
        created.append(aid)
        finalise_settlement(inv["invoice_id"], actor=reviewer)

    update_transaction_status(transaction_id, "matched")
    if case_id:
        update_review_case(case_id, "corrected", reviewer, notes)
        _close_other_cases(transaction_id, case_id, reviewer)
    create_audit_log("transaction", transaction_id, "human_corrected", actor=reviewer,
                     details={"new_invoice_ids": ids, "allocations": created, "tds_amount": float(tds),
                              "notes": notes})
    return {"status": "corrected", "new_allocation_ids": created, "new_allocation_id": created[0] if created else None,
            "new_invoice_id": ids[0], "new_invoice_ids": ids}


# ── Reverse / Dismiss ─────────────────────────────────────────────────────────

def reverse_allocation(allocation_id: str, reviewer: str = "human", notes: str = "") -> dict:
    """Reverse one approved allocation and reinstate the invoice balance."""
    alloc = get_allocation(allocation_id)
    if not alloc:
        return {"status": "error", "reason": "Allocation not found"}
    if alloc["allocation_status"] != "approved":
        return {"status": "error", "reason": f"Allocation is {alloc['allocation_status']}, not approved"}

    update_allocation_status(allocation_id, "reversed", reviewer, notes)
    settlement = finalise_settlement(alloc["invoice_id"], actor=reviewer)
    still_approved = [a for a in get_allocations_for_transaction(alloc["transaction_id"])
                      if a["allocation_status"] == "approved"]
    if not still_approved:
        update_transaction_status(alloc["transaction_id"], "unmatched")
    create_audit_log("allocation", allocation_id, "reversed", actor=reviewer,
                     details={"transaction_id": alloc["transaction_id"], "invoice_id": alloc["invoice_id"],
                              "notes": notes})
    return {"status": "reversed", "allocation_id": allocation_id, "invoice_id": alloc["invoice_id"],
            "new_outstanding": settlement.get("outstanding")}


def reverse_transaction(transaction_id: str, reviewer: str = "human", notes: str = "") -> dict:
    """Reverse every approved allocation of a transaction (undo a match, incl. bulk splits)."""
    approved = [a for a in get_allocations_for_transaction(transaction_id) if a["allocation_status"] == "approved"]
    if not approved:
        raise ReviewError("This transaction has no approved allocation to reverse")
    results = [reverse_allocation(a["allocation_id"], reviewer, notes) for a in approved]
    update_transaction_status(transaction_id, "unmatched")
    return {"status": "reversed", "allocations": [r["allocation_id"] for r in results],
            "invoices": [r["invoice_id"] for r in results]}


def dismiss_transaction(case_id: str, reviewer: str = "human", notes: str = "") -> dict:
    """Mark a credit as not a customer receipt (interest, refund, internal transfer…)."""
    case = _case(case_id)
    tid = case["transaction_id"]
    for alloc in _proposed(tid):
        update_allocation_status(alloc["allocation_id"], "rejected", reviewer, notes)
    update_transaction_status(tid, "excluded")
    update_review_case(case_id, "dismissed", reviewer, notes)
    _close_other_cases(tid, case_id, reviewer)
    create_audit_log("transaction", tid, "dismissed_non_receivable", actor=reviewer, details={"notes": notes})
    return {"status": "dismissed", "transaction_id": tid}


# ── Duplicates ────────────────────────────────────────────────────────────────

def confirm_duplicate(case_id: str, reviewer: str = "human", notes: str = "") -> dict:
    case = _case(case_id)
    update_review_case(case_id, "approved", reviewer, notes or "Confirmed duplicate")
    _close_other_cases(case["transaction_id"], case_id, reviewer)
    create_audit_log("transaction", case["transaction_id"], "duplicate_confirmed", actor=reviewer,
                     details={"case_id": case_id, "notes": notes})
    return {"status": "confirmed", "transaction_id": case["transaction_id"]}


def release_duplicate(case_id: str, reviewer: str = "human", notes: str = "",
                      reconcile_now: bool = True) -> dict:
    """The flagged credit is genuine — clear the flag and (optionally) reconcile it immediately."""
    case = _case(case_id)
    tid = case["transaction_id"]
    update_review_case(case_id, "rejected", reviewer, notes or "Not a duplicate")
    _close_other_cases(tid, case_id, reviewer)
    create_audit_log("transaction", tid, "duplicate_released", actor=reviewer,
                     details={"case_id": case_id, "notes": notes})
    update_transaction_status(tid, "unmatched")
    result = None
    if reconcile_now:
        from decision.decision_engine import reconcile_transaction
        result = reconcile_transaction(tid)
    return {"status": "released", "transaction_id": tid, "reconcile_result": result}


# ── TDS evidence ──────────────────────────────────────────────────────────────

def verify_tds(component_id: str, reviewer: str = "human", certificate_document_id: str | None = None,
               notes: str = "") -> dict:
    """Mark a TDS component verified (Form 16A / 26AS checked)."""
    comp = get_settlement_component(component_id)
    if not comp or comp["component_type"] != "tds":
        raise ReviewError("TDS component not found")
    update_settlement_component(component_id, tds_status="verified",
                                supporting_doc=certificate_document_id or comp.get("supporting_doc"),
                                notes=notes or comp.get("notes"))
    create_audit_log("settlement_component", component_id, "tds_verified", actor=reviewer,
                     details={"allocation_id": comp["allocation_id"], "amount": comp["amount"],
                              "certificate": certificate_document_id, "notes": notes})
    return {"status": "verified", "component_id": component_id}


def reject_tds(component_id: str, reviewer: str = "human", notes: str = "") -> dict:
    """
    The customer's TDS claim is not supported: the deducted amount is still owed.
    The allocation is reduced by the TDS amount and the invoice re-opened for it.
    """
    comp = get_settlement_component(component_id)
    if not comp or comp["component_type"] != "tds":
        raise ReviewError("TDS component not found")
    if comp.get("tds_status") == "rejected":
        raise ReviewError("TDS already rejected")
    alloc = get_allocation(comp["allocation_id"])
    update_settlement_component(component_id, tds_status="rejected", notes=notes)
    update_allocation_amount(alloc["allocation_id"], float(alloc["allocated_amount"]) - float(comp["amount"]))
    settlement = finalise_settlement(alloc["invoice_id"], actor=reviewer)
    create_audit_log("settlement_component", component_id, "tds_rejected", actor=reviewer,
                     details={"allocation_id": alloc["allocation_id"], "invoice_id": alloc["invoice_id"],
                              "amount": comp["amount"], "notes": notes})
    return {"status": "rejected", "invoice_id": alloc["invoice_id"], "new_outstanding": settlement.get("outstanding")}
