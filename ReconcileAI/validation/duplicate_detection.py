"""
ReconcileAI – Duplicate Detection
Detects potential duplicate bank transaction imports based on UTR and amount/date proximity.
"""
from __future__ import annotations
from datetime import datetime, timedelta

from config.thresholds import DUPLICATE_UTR_WINDOW_DAYS
from database.repositories import get_all_transactions


def detect_duplicate_transaction(transaction: dict,
                                  existing_transactions: list[dict] | None = None) -> dict:
    """
    Check whether a transaction is a duplicate of one already in the database.

    Duplicate criteria:
    1. Same UTR (definitive duplicate)
    2. Same amount + narration within DUPLICATE_UTR_WINDOW_DAYS (probable duplicate)

    Returns
    -------
    {"is_duplicate": bool, "reason": str, "duplicate_of": str | None}
    """
    existing = existing_transactions if existing_transactions is not None else get_all_transactions()
    utr      = transaction.get("utr")

    # Rule 1: UTR match
    if utr:
        for ex in existing:
            if (ex.get("utr") == utr and
                    ex.get("transaction_id") != transaction.get("transaction_id")):
                return {
                    "is_duplicate":  True,
                    "reason":        f"Same UTR '{utr}' already recorded as {ex['transaction_id']}",
                    "duplicate_of":  ex["transaction_id"],
                }

    # Rule 2: Same amount + narration within window — probable re-submission
    txn_date = _parse_date(transaction.get("transaction_date"))
    credit   = round(float(transaction.get("credit_amount", 0)), 2)
    narration = (transaction.get("narration") or "").strip().lower()

    for ex in existing:
        if ex.get("transaction_id") == transaction.get("transaction_id"):
            continue
        if round(float(ex.get("credit_amount", 0)), 2) != credit:
            continue
        ex_date = _parse_date(ex.get("transaction_date"))
        if txn_date and ex_date:
            if abs((txn_date - ex_date).days) > DUPLICATE_UTR_WINDOW_DAYS:
                continue
        ex_narration = (ex.get("narration") or "").strip().lower()

        # If both narrations carry distinct invoice references, they are
        # definitively DIFFERENT payments — never flag as duplicate.
        my_refs = _extract_invoice_refs(narration)
        ex_refs = _extract_invoice_refs(ex_narration)
        if my_refs and ex_refs and my_refs.isdisjoint(ex_refs):
            continue

        if _narration_similarity(narration, ex_narration) > 0.92:
            return {
                "is_duplicate":  True,
                "reason":        f"Same amount ₹{credit:,.2f} and near-identical narration within {DUPLICATE_UTR_WINDOW_DAYS} days — possible duplicate of {ex['transaction_id']}",
                "duplicate_of":  ex["transaction_id"],
            }

    return {"is_duplicate": False, "reason": "", "duplicate_of": None}


def _parse_date(date_str: str | None):
    if not date_str:
        return None
    try:
        from dateutil import parser as dp
        return dp.parse(date_str).date()
    except Exception:
        return None


def _extract_invoice_refs(narration: str) -> set[str]:
    """Return a set of normalised invoice reference strings found in the narration."""
    import re
    pattern = re.compile(r"\b(inv[-_/]?\d{3,6})\b", re.IGNORECASE)
    return {m.group(0).upper().replace("-", "").replace("/", "").replace("_", "")
            for m in pattern.finditer(narration)}


def _narration_similarity(a: str, b: str) -> float:
    """Simple character-level similarity for narration comparison."""
    if not a or not b:
        return 0.0
    from rapidfuzz import fuzz
    return fuzz.ratio(a, b) / 100.0
