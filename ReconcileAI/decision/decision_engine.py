"""
ReconcileAI – Decision Engine
Decides the reconciliation outcome for each transaction using scored candidates
and validation results. The LLM only explains; this module decides.
"""
from __future__ import annotations
from decimal import Decimal

from config.thresholds import AUTO_MATCH_THRESHOLD, REVIEW_THRESHOLD
from database.repositories import (
    get_transaction, get_remittance_by_utr,
    insert_allocation, insert_settlement_component, update_transaction_status,
    update_invoice_outstanding, get_allocations_for_invoice, create_audit_log,
    insert_review_case,
)
from matching.candidate_generator import generate_candidate_invoices, detect_multi_invoice_payment
from matching.scoring import score_candidates
from validation.financial_rules import validate_allocation, calculate_outstanding
from validation.duplicate_detection import detect_duplicate_transaction
from extraction.llm_extractor import interpret_bank_narration, generate_match_explanation


def reconcile_transaction(transaction_id: str) -> dict:
    """
    Full reconciliation pipeline for a single bank transaction.
    Returns a decision dict with outcome and explanation.
    """
    transaction = get_transaction(transaction_id)
    if not transaction:
        return {"outcome": "error", "reason": "Transaction not found"}

    # ── Step 1: Duplicate check ────────────────────────────────────────────
    dup = detect_duplicate_transaction(transaction)
    if dup["is_duplicate"]:
        update_transaction_status(transaction_id, "duplicate")
        create_audit_log("transaction", transaction_id, "flagged_duplicate",
                         details={"reason": dup["reason"]})
        return {"outcome": "duplicate", "reason": dup["reason"], "duplicate_of": dup["duplicate_of"]}

    # ── Step 2: Narration hints via LLM ───────────────────────────────────
    narration_hints = None
    narration = transaction.get("narration", "")
    if narration:
        try:
            narration_hints = interpret_bank_narration(
                narration,
                transaction.get("credit_amount", 0),
                transaction.get("transaction_date"),
            )
        except Exception:
            narration_hints = None

    # ── Step 3: Remittance lookup ─────────────────────────────────────────
    remittance = None
    utr = transaction.get("utr")
    if utr:
        remittance = get_remittance_by_utr(utr)

    # ── Step 4: Generate and score candidates ─────────────────────────────
    candidates = generate_candidate_invoices(transaction, narration_hints, remittance)
    if not candidates:
        update_transaction_status(transaction_id, "unmatched")
        cid = insert_review_case({
            "transaction_id": transaction_id,
            "exception_type": "missing_invoice_reference",
            "exception_summary": "No candidate invoices found. No invoice reference, customer match, or amount match.",
            "suggested_action": "Obtain remittance advice from payer.",
        })
        create_audit_log("transaction", transaction_id, "unmatched",
                         details={"review_case": cid})
        return {"outcome": "unmatched", "review_case_id": cid}

    ranked = score_candidates(transaction, candidates)
    best   = ranked[0]
    invoice = best["invoice"]
    score   = best["score"]
    signals = best["signals"]
    tds_amount = float(signals.get("tds_amount", 0))

    # ── Step 5: Multi-invoice payment detection ───────────────────────────
    all_inv = [c["invoice"] for c in candidates]
    multi_combos = detect_multi_invoice_payment(
        transaction.get("credit_amount", 0), tds_amount, all_inv
    )

    if len(ranked) > 1 and ranked[0]["score"] - ranked[1]["score"] < 0.10:
        # Ambiguous — two candidates very close in score
        cid = _create_review_case(transaction_id, invoice["invoice_id"],
                                   "multiple_candidate_invoices",
                                   f"Top candidates {ranked[0]['invoice']['invoice_id']} "
                                   f"(score {ranked[0]['score']:.2f}) and "
                                   f"{ranked[1]['invoice']['invoice_id']} "
                                   f"(score {ranked[1]['score']:.2f}) are too close to auto-match.")
        update_transaction_status(transaction_id, "review")
        return {"outcome": "review", "reason": "Multiple similarly-scored candidates", "review_case_id": cid}

    # ── Step 6: Financial validation ─────────────────────────────────────
    validation = validate_allocation(
        transaction.get("credit_amount", 0),
        tds_amount,
        invoice,
        tds_section=None,
        has_tds_certificate=False,
    )

    # ── Step 7: Decision ──────────────────────────────────────────────────
    # Case A: High-confidence exact match — auto-approve
    if score >= AUTO_MATCH_THRESHOLD and validation["overall_valid"] and not validation["requires_review"]:
        return _auto_approve(transaction, invoice, signals, validation, score)

    # Case B: Exact invoice ref confirmed + high score — partial payment is
    # acceptable to auto-approve (partial pay is a business fact, not an error)
    partial_only = (
        validation.get("is_partial_payment", False)
        and not validation.get("is_overpayment", False)
        and not validation.get("tds_invalid", False)
        and not validation.get("duplicate_tds", False)
    )
    if score >= AUTO_MATCH_THRESHOLD and signals.get("invoice_ref_exact") and partial_only:
        return _auto_approve(transaction, invoice, signals, validation, score)

    # Case C: Medium confidence or flagged — send for human review
    elif score >= REVIEW_THRESHOLD or validation["requires_review"]:
        return _send_to_review(transaction_id, invoice, signals, validation, score, ranked)

    else:
        update_transaction_status(transaction_id, "unmatched")
        cid = insert_review_case({
            "transaction_id": transaction_id,
            "exception_type": "missing_invoice_reference",
            "exception_summary": f"Best candidate score {score:.2f} is below review threshold.",
            "suggested_action": "Obtain remittance advice or verify customer identity.",
        })
        return {"outcome": "unmatched", "review_case_id": cid}



def _auto_approve(transaction: dict, invoice: dict, signals: dict,
                   validation: dict, score: float) -> dict:
    """Create an approved allocation automatically."""
    tid = transaction["transaction_id"]
    iid = invoice["invoice_id"]
    credit    = float(transaction.get("credit_amount", 0))
    tds_amount= float(signals.get("tds_amount", 0))

    # Build evidence dict for LLM explanation
    evidence = {
        "transaction_id":    tid,
        "invoice_id":        iid,
        "confidence_score":  score,
        "signals":           signals,
        "validation":        validation,
    }
    try:
        explanation = generate_match_explanation(evidence)
    except Exception:
        explanation = f"Transaction matched to {iid} with confidence {score:.0%}."

    aid = insert_allocation({
        "transaction_id":   tid,
        "invoice_id":       iid,
        "allocated_amount": credit + tds_amount,
        "allocation_status": "approved",
        "confidence_score": score,
        "decision_reason":  explanation,
        "approved_by":      "system",
    })

    # Settlement components
    insert_settlement_component({"allocation_id": aid, "component_type": "bank_credit", "amount": credit})
    if tds_amount > 0:
        insert_settlement_component({
            "allocation_id": aid, "component_type": "tds", "amount": tds_amount,
            "tds_status": validation["tds_validation"]["tds_status"],
        })

    # Update invoice outstanding
    existing_allocs = get_allocations_for_invoice(iid)
    new_outstanding = calculate_outstanding(invoice["total_amount"], existing_allocs)
    new_status = "settled" if new_outstanding <= 0.01 else "partially_paid"
    update_invoice_outstanding(iid, new_outstanding, new_status)
    update_transaction_status(tid, "matched")

    create_audit_log("transaction", tid, "auto_matched",
                     details={"invoice_id": iid, "score": score, "allocation_id": aid})

    return {
        "outcome":       "matched",
        "invoice_id":    iid,
        "score":         score,
        "allocation_id": aid,
        "explanation":   explanation,
        "new_outstanding": new_outstanding,
    }


def _send_to_review(transaction_id: str, invoice: dict, signals: dict,
                     validation: dict, score: float, ranked: list[dict]) -> dict:
    """Create a review case for human decision."""
    exc_type = "amount_discrepancy"
    if validation["amount_validation"]["is_partial"]:
        exc_type = "possible_tds_deduction" if signals.get("tds_amount", 0) > 0 else "amount_discrepancy"
    elif validation["amount_validation"]["is_overpayment"]:
        exc_type = "amount_discrepancy"

    summary = (
        f"Score {score:.2f}. "
        f"Effective payment ₹{validation['amount_validation']['effective_payment']:,.2f}. "
        f"Invoice outstanding ₹{invoice['outstanding_amount']:,.2f}. "
        f"Validation: {validation['tds_validation'].get('tds_status','n/a')}."
    )
    cid = _create_review_case(transaction_id, invoice["invoice_id"], exc_type, summary)
    update_transaction_status(transaction_id, "review")
    return {"outcome": "review", "reason": exc_type, "review_case_id": cid, "score": score}


def _create_review_case(transaction_id: str, invoice_id: str, exc_type: str, summary: str) -> str:
    cid = insert_review_case({
        "transaction_id":   transaction_id,
        "invoice_id":       invoice_id,
        "exception_type":   exc_type,
        "exception_summary": summary,
    })
    create_audit_log("transaction", transaction_id, "sent_to_review",
                     details={"review_case_id": cid, "exception_type": exc_type})
    return cid


def run_batch_reconciliation(batch_id: str | None = None) -> dict:
    """Run reconciliation on all unmatched transactions."""
    from database.repositories import get_unmatched_transactions, update_batch, create_batch
    if not batch_id:
        batch_id = create_batch()

    transactions = get_unmatched_transactions()
    stats = {"total": len(transactions), "matched": 0, "needs_review": 0, "unmatched": 0}

    for txn in transactions:
        result = reconcile_transaction(txn["transaction_id"])
        outcome = result.get("outcome", "unmatched")
        if outcome == "matched":
            stats["matched"] += 1
        elif outcome == "review":
            stats["needs_review"] += 1
        else:
            stats["unmatched"] += 1

    update_batch(batch_id, stats)
    return {"batch_id": batch_id, **stats}
