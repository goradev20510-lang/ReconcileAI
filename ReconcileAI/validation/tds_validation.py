"""
ReconcileAI – TDS Validation (extended)
Standalone TDS-specific utilities beyond the basic rules in financial_rules.py.
"""
from __future__ import annotations
from decimal import Decimal

from config.thresholds import TDS_SECTION_RATES, MAX_TDS_RATE


def validate_tds_amount(claimed_tds: float, invoice_total: float,
                         section: str | None, certificate_uploaded: bool) -> dict:
    """
    Full TDS validation pipeline:
    1. Check rate against section table.
    2. Determine certificate requirement.
    3. Return status: claimed → evidence_pending → verified | rejected
    """
    from validation.financial_rules import validate_tds_claim
    result = validate_tds_claim(claimed_tds, invoice_total, section, certificate_uploaded)

    recommendations = []
    if result["tds_status"] == "claimed":
        recommendations.append("Upload Form 16A or TDS certificate to move to evidence_pending.")
    elif result["tds_status"] == "evidence_pending":
        recommendations.append("Upload Form 16A or TDS certificate to verify the claim.")
    elif result["tds_status"] == "rejected":
        recommendations.append("TDS rate does not match the applicable section. Review with customer.")

    result["recommendations"] = recommendations
    return result


def get_tds_summary(invoice_id: str) -> dict:
    """Return TDS component summary for a given invoice's approved allocations."""
    from database.db import get_connection
    conn = get_connection()
    rows = conn.execute(
        """SELECT sc.* FROM settlement_components sc
           JOIN payment_allocations pa ON sc.allocation_id = pa.allocation_id
           WHERE pa.invoice_id = ? AND sc.component_type = 'tds'""",
        (invoice_id,)
    ).fetchall()

    components = [dict(r) for r in rows]
    total_tds = sum(float(c.get("amount", 0)) for c in components)
    verified  = sum(float(c.get("amount", 0)) for c in components if c.get("tds_status") == "verified")

    return {
        "invoice_id":          invoice_id,
        "total_tds_claimed":   total_tds,
        "total_tds_verified":  verified,
        "total_tds_pending":   total_tds - verified,
        "components":          components,
    }
