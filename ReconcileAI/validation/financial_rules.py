"""
ReconcileAI – Financial Rules & Validation Engine
All financial arithmetic uses Python Decimal for precision.
No floating-point is used for monetary comparisons.
"""
from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP

from config.thresholds import AMOUNT_TOLERANCE_PERCENT, MAX_TDS_RATE, TDS_SECTION_RATES


def validate_payment_amount(credit_amount: float, tds_amount: float,
                             invoice_total: float, outstanding_amount: float) -> dict:
    """
    Validate whether a payment (credit + TDS) matches an invoice.

    Returns
    -------
    {
        "valid": bool,
        "effective_payment": float,
        "difference": float,
        "is_partial": bool,
        "is_overpayment": bool,
        "within_tolerance": bool,
    }
    """
    credit      = Decimal(str(credit_amount))
    tds         = Decimal(str(tds_amount))
    inv_total   = Decimal(str(invoice_total))
    outstanding = Decimal(str(outstanding_amount))
    effective   = credit + tds
    tol         = inv_total * AMOUNT_TOLERANCE_PERCENT

    within_tol   = abs(effective - outstanding) <= tol
    is_partial   = effective < outstanding - tol
    is_over      = effective > inv_total + tol

    return {
        "valid":             within_tol or is_partial,
        "effective_payment": float(effective),
        "difference":        float(effective - outstanding),
        "is_partial":        is_partial,
        "is_overpayment":   is_over,
        "within_tolerance": within_tol,
    }


def validate_tds_claim(tds_amount: float, invoice_total: float,
                        tds_section: str | None = None,
                        has_tds_certificate: bool = False,
                        taxable_amount: float | None = None) -> dict:
    """
    Validate a TDS claim against configured rates.

    Returns
    -------
    {
        "valid": bool,
        "tds_rate": float,
        "expected_tds": float,
        "tds_status": "claimed" | "verified" | "rejected",
        "reasons": [str],
    }
    """
    tds     = Decimal(str(tds_amount))
    inv     = Decimal(str(invoice_total))
    reasons = []

    if inv == 0:
        return {"valid": False, "tds_rate": 0.0, "expected_tds": 0.0,
                "tds_status": "rejected", "reasons": ["Invoice total is zero"]}

    actual_rate = tds / inv
    section_key = (tds_section or "").upper().strip()
    section_key = section_key if section_key in TDS_SECTION_RATES else "DEFAULT"
    expected_rate = TDS_SECTION_RATES[section_key]
    expected_tds  = (inv * expected_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    tol           = max(expected_tds * AMOUNT_TOLERANCE_PERCENT, Decimal("1"))

    rate_ok = abs(tds - expected_tds) <= tol
    # When GST is charged separately, TDS is deducted on the value excluding GST
    # (CBDT Circular 23/2017). Accept either basis.
    if not rate_ok and taxable_amount:
        taxable = Decimal(str(taxable_amount))
        exp_taxable = (taxable * expected_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if abs(tds - exp_taxable) <= max(exp_taxable * AMOUNT_TOLERANCE_PERCENT, Decimal("1")):
            rate_ok, expected_tds = True, exp_taxable
            actual_rate = tds / taxable

    if actual_rate > MAX_TDS_RATE:
        reasons.append(f"TDS rate {float(actual_rate):.1%} exceeds maximum allowed {float(MAX_TDS_RATE):.1%}")
        return {"valid": False, "tds_rate": float(actual_rate), "expected_tds": float(expected_tds),
                "tds_status": "rejected", "reasons": reasons}

    if not rate_ok:
        reasons.append(
            f"Claimed TDS ₹{tds_amount:,.2f} does not match expected ₹{float(expected_tds):,.2f} "
            f"at section {tds_section or 'DEFAULT'} rate ({float(expected_rate):.1%})"
        )

    # A claim is only 'verified' if the rate matches AND a certificate is attached
    if rate_ok and has_tds_certificate:
        status = "verified"
    elif rate_ok:
        status = "evidence_pending"
        reasons.append("TDS rate matches but certificate not yet uploaded")
    else:
        status = "claimed"
        reasons.append("Rate mismatch — certificate required for verification")

    return {
        "valid":        rate_ok,
        "tds_rate":     float(actual_rate),
        "expected_tds": float(expected_tds),
        "tds_status":   status,
        "reasons":      reasons,
    }


def calculate_outstanding(invoice_total: float, approved_allocations: list[dict]) -> float:
    """
    Recalculate outstanding balance after approved allocations.
    Only 'approved' allocations reduce the balance.
    """
    total = Decimal(str(invoice_total))
    paid  = sum(Decimal(str(a["allocated_amount"])) for a in approved_allocations)
    outstanding = total - paid
    return float(max(outstanding, Decimal("0")))


def check_overpayment(invoice_total: float, approved_allocations: list[dict]) -> dict:
    total   = Decimal(str(invoice_total))
    paid    = sum(Decimal(str(a["allocated_amount"])) for a in approved_allocations)
    excess  = paid - total
    return {
        "is_overpayment": excess > 0,
        "excess_amount":  float(max(excess, Decimal("0"))),
    }


def validate_currency(transaction_currency: str, invoice_currency: str) -> bool:
    """For MVP: only INR supported; raise if mismatch."""
    return transaction_currency.upper() == invoice_currency.upper()


def validate_allocation(credit_amount: float, tds_amount: float,
                         invoice: dict, tds_section: str | None = None,
                         has_tds_certificate: bool = False) -> dict:
    """
    Run all financial validation checks for a proposed allocation.
    Returns a comprehensive result dict.
    """
    amount_result = validate_payment_amount(
        credit_amount, tds_amount,
        invoice["total_amount"], invoice["outstanding_amount"]
    )
    tds_result = {"valid": True, "tds_status": "n/a", "reasons": []}
    if tds_amount > 0:
        tds_result = validate_tds_claim(
            tds_amount, invoice["total_amount"], tds_section, has_tds_certificate,
            taxable_amount=invoice.get("taxable_amount"),
        )

    return {
        "amount_validation": amount_result,
        "tds_validation":    tds_result,
        "overall_valid":     amount_result["valid"],
        "requires_review":   (
            amount_result["is_partial"] or
            amount_result["is_overpayment"] or
            (tds_amount > 0 and tds_result["tds_status"] not in ("verified",))
        ),
    }


def infer_tds_deduction(credit_amount: float, invoice: dict) -> dict | None:
    """
    Detect an *undeclared* TDS deduction: a short payment whose shortfall equals a
    standard TDS rate on the invoice's taxable value or total.

    Returns {"tds_amount", "sections", "basis", "rate"} or None.
    Only applies to invoices with nothing paid yet (TDS is computed on the whole bill).
    """
    total = Decimal(str(invoice.get("total_amount") or 0))
    outstanding = Decimal(str(invoice.get("outstanding_amount") or 0))
    taxable = Decimal(str(invoice.get("taxable_amount") or 0))
    credit = Decimal(str(credit_amount or 0))
    if total <= 0 or abs(outstanding - total) > Decimal("0.01"):
        return None
    shortfall = total - credit
    if shortfall <= 0:
        return None
    rates: dict[Decimal, list[str]] = {}
    for sec, rate in TDS_SECTION_RATES.items():
        if sec != "DEFAULT":
            rates.setdefault(rate, []).append(sec)
    for basis, base in (("taxable value", taxable), ("invoice total", total)):
        if base <= 0:
            continue
        for rate, sections in sorted(rates.items()):
            expected = (base * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if abs(shortfall - expected) <= max(expected * AMOUNT_TOLERANCE_PERCENT, Decimal("1")):
                return {"tds_amount": float(shortfall), "sections": sections, "basis": basis,
                        "rate": float(rate)}
    return None
