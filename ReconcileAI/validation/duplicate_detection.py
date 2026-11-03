"""
ReconcileAI – Duplicate Detection
Detects repeated bank credits (same UTR, or the same line re-keyed without a UTR).

Only transactions imported *before* the one being checked can be its original,
so the first occurrence is never flagged — only the copies are.
"""
from __future__ import annotations
import re

from config.thresholds import DUPLICATE_UTR_WINDOW_DAYS
from database.repositories import get_transactions_imported_before

_INV_RE = re.compile(r"\b(inv[\s\-_/]?\d{3,6})\b", re.IGNORECASE)


def detect_duplicate_transaction(transaction: dict,
                                  existing_transactions: list[dict] | None = None) -> dict:
    """
    Check whether a transaction duplicates one recorded earlier.

    Duplicate criteria:
    1. Same UTR as an earlier credit (definitive — a UTR identifies one bank transfer).
    2. No usable UTR on one side, same amount, near-identical narration within
       DUPLICATE_UTR_WINDOW_DAYS (probable re-keyed / re-imported line).
       Two credits that both carry UTRs and the UTRs differ are distinct transfers
       (e.g. a split payment PART1 / PART2), never duplicates.

    Returns
    -------
    {"is_duplicate": bool, "reason": str, "duplicate_of": str | None}
    """
    if existing_transactions is None:
        existing_transactions = get_transactions_imported_before(transaction.get("transaction_id", ""))
    me = transaction.get("transaction_id")
    existing = [ex for ex in existing_transactions if ex.get("transaction_id") != me]
    utr = (transaction.get("utr") or "").strip().upper()

    # Rule 1: UTR match
    if utr:
        for ex in existing:
            if (ex.get("utr") or "").strip().upper() == utr:
                return {
                    "is_duplicate": True,
                    "reason":       f"Same UTR '{utr}' already recorded as {ex['transaction_id']}",
                    "duplicate_of": ex["transaction_id"],
                }

    # Rule 2: same amount + near-identical narration, only when a UTR is missing
    txn_date = _parse_date(transaction.get("transaction_date"))
    credit = round(float(transaction.get("credit_amount", 0) or 0), 2)
    narration = (transaction.get("narration") or "").strip().lower()

    for ex in existing:
        ex_utr = (ex.get("utr") or "").strip().upper()
        if utr and ex_utr and utr != ex_utr:
            continue
        if round(float(ex.get("credit_amount", 0) or 0), 2) != credit:
            continue
        ex_date = _parse_date(ex.get("transaction_date"))
        if txn_date and ex_date and abs((txn_date - ex_date).days) > DUPLICATE_UTR_WINDOW_DAYS:
            continue
        ex_narration = (ex.get("narration") or "").strip().lower()

        # Narrations citing different invoices are different payments.
        my_refs, ex_refs = _extract_invoice_refs(narration), _extract_invoice_refs(ex_narration)
        if my_refs and ex_refs and my_refs.isdisjoint(ex_refs):
            continue

        if _narration_similarity(narration, ex_narration) > 0.92:
            return {
                "is_duplicate": True,
                "reason": (f"Same amount ₹{credit:,.2f} and near-identical narration within "
                           f"{DUPLICATE_UTR_WINDOW_DAYS} days — possible duplicate of {ex['transaction_id']}"),
                "duplicate_of": ex["transaction_id"],
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
    return {re.sub(r"[\s\-_/]", "", m.group(0).upper()) for m in _INV_RE.finditer(narration or "")}


def _narration_similarity(a: str, b: str) -> float:
    """Simple character-level similarity for narration comparison."""
    if not a or not b:
        return 0.0
    from rapidfuzz import fuzz
    return fuzz.ratio(a, b) / 100.0
