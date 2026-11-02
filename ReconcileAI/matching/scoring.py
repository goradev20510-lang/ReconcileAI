"""
ReconcileAI – Candidate Scoring Engine
Assigns a confidence score [0.0, 1.0] to each (transaction, invoice) candidate pair.
All scoring logic is deterministic Python.
"""
from __future__ import annotations
from decimal import Decimal

from config.thresholds import SCORE_WEIGHTS, AMOUNT_TOLERANCE_PERCENT


def score_candidates(transaction: dict, candidates: list[dict]) -> list[dict]:
    """
    Score and rank candidate invoices for a transaction.

    Parameters
    ----------
    transaction : bank_transactions row
    candidates  : list of {"invoice": {...}, "signals": {...}}

    Returns
    -------
    Same list sorted by score descending, each with a "score" key added.
    """
    scored = []
    for c in candidates:
        score = _score_candidate(transaction, c["invoice"], c["signals"])
        scored.append({**c, "score": round(score, 4)})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def _score_candidate(transaction: dict, invoice: dict, signals: dict) -> float:
    w = SCORE_WEIGHTS
    score = 0.0

    # 1. Exact invoice reference in remittance/narration
    if signals.get("invoice_ref_exact"):
        score += w["invoice_ref_exact"]

    # 2. Customer identity match
    if signals.get("customer_match"):
        fuzzy_score = signals.get("fuzzy_score", 0)
        if fuzzy_score == 100 or signals.get("match_method") == "gstin_exact":
            score += w["customer_exact"]
        else:
            score += w["customer_fuzzy"] * (fuzzy_score / 100.0)

    # 3. Amount match
    credit     = Decimal(str(transaction.get("credit_amount", 0)))
    tds_amount = Decimal(str(signals.get("tds_amount", 0)))
    effective  = credit + tds_amount
    inv_total  = Decimal(str(invoice.get("total_amount", 0)))
    outstanding = Decimal(str(invoice.get("outstanding_amount", 0)))
    tol        = inv_total * AMOUNT_TOLERANCE_PERCENT

    if abs(effective - inv_total) <= tol or abs(effective - outstanding) <= tol:
        if abs(effective - inv_total) == 0 or abs(effective - outstanding) == 0:
            score += w["amount_exact"]
        else:
            score += w["amount_within_tol"]

    # 4. Date proximity (transaction date within 30 days of due date)
    score += _date_proximity_score(
        transaction.get("transaction_date"),
        invoice.get("due_date"),
        w["date_proximity"]
    )

    return min(score, 1.0)


def _date_proximity_score(txn_date: str | None, due_date: str | None, weight: float) -> float:
    """Returns a fractional score based on how close the transaction is to the due date."""
    if not txn_date or not due_date:
        return 0.0
    try:
        from dateutil import parser as dp
        t = dp.parse(txn_date).date()
        d = dp.parse(due_date).date()
        diff = abs((t - d).days)
        if diff <= 7:
            return weight
        elif diff <= 30:
            return weight * 0.5
        elif diff <= 90:
            return weight * 0.2
    except Exception:
        pass
    return 0.0
