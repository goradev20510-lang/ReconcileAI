"""
ReconcileAI – Decision Thresholds
All numeric thresholds that drive matching and validation decisions.
Centralised here so that calibration changes don't scatter across modules.
"""
from decimal import Decimal

# ── Confidence scoring ────────────────────────────────────────────────────────
# Scores are in [0.0, 1.0]
AUTO_MATCH_THRESHOLD:   float = 0.80   # Score at which a match is auto-approved
REVIEW_THRESHOLD:       float = 0.50   # Score below which a case goes to human review
# 0.50–0.80 → suggested match requiring human approval

# ── Fuzzy matching ────────────────────────────────────────────────────────────
CUSTOMER_NAME_FUZZY_MIN: int = 75      # Minimum RapidFuzz score (0–100)

# ── Amount tolerance ─────────────────────────────────────────────────────────
# Allow ±0.5% rounding difference before flagging an amount discrepancy.
# Stored as Decimal for precision.
AMOUNT_TOLERANCE_PERCENT: Decimal = Decimal("0.005")

# ── TDS rules (India) ─────────────────────────────────────────────────────────
TDS_SECTION_RATES: dict[str, Decimal] = {
    "194C": Decimal("0.02"),    # Contractors – 2 %
    "194J": Decimal("0.10"),    # Professional services – 10 %
    "194H": Decimal("0.05"),    # Commission – 5 %
    "194I": Decimal("0.10"),    # Rent – 10 %
    "194A": Decimal("0.10"),    # Interest – 10 %
    "DEFAULT": Decimal("0.10"),
}

# Maximum TDS percentage accepted without escalating to review
MAX_TDS_RATE: Decimal = Decimal("0.20")

# ── Duplicate detection ───────────────────────────────────────────────────────
# Window within which same UTR + same amount is considered a duplicate
DUPLICATE_UTR_WINDOW_DAYS: int = 30

# ── Scoring weights ───────────────────────────────────────────────────────────
# Total of all weights = 1.0. invoice_ref_exact is the strongest deterministic signal.
SCORE_WEIGHTS: dict[str, float] = {
    "invoice_ref_exact":   0.50,   # Exact invoice reference in narration/remittance
    "customer_exact":      0.15,   # Customer name / GSTIN exact match
    "customer_fuzzy":      0.10,   # Fuzzy customer match (name similarity)
    "amount_exact":        0.15,   # Payment + TDS = invoice total exactly
    "amount_within_tol":   0.05,   # Within ±0.5% tolerance
    "date_proximity":      0.05,   # Transaction near invoice due date
}

# ── Partial payment ───────────────────────────────────────────────────────────
# A payment is "partial" if it covers less than this share of the invoice
PARTIAL_THRESHOLD: Decimal = Decimal("0.99")
