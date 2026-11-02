"""
ReconcileAI – Exact Match Engine
Fast lookup for transactions that carry explicit invoice references.
"""
from __future__ import annotations
import re
from database.repositories import get_all_invoices


def find_exact_invoice_match(invoice_ref_candidate: str | None,
                              all_invoices: list[dict] | None = None) -> list[dict]:
    """
    Search for invoices whose invoice_id matches the extracted reference.
    Returns a list of matching invoice dicts (usually 0 or 1 items).
    """
    if not invoice_ref_candidate:
        return []

    normalised = _normalise_invoice_ref(invoice_ref_candidate)
    invoices   = all_invoices if all_invoices is not None else get_all_invoices()
    matches    = []

    for inv in invoices:
        if _normalise_invoice_ref(inv.get("invoice_id", "")) == normalised:
            matches.append(inv)

    return matches


def _normalise_invoice_ref(ref: str) -> str:
    """Strip spaces, hyphens and leading zeros to create a canonical key."""
    ref = re.sub(r"[\s\-_/]", "", ref.upper().strip())
    # Remove leading zeros from numeric suffix: INV-0101 → INV101
    ref = re.sub(r"0+(\d)", r"\1", ref)
    return ref
