"""
ReconcileAI – Fuzzy Customer Matching
Uses RapidFuzz to find likely customer candidates when names don't match exactly.
Identity is confirmed only if corroborated by GSTIN or additional evidence.
"""
from __future__ import annotations
import re
from rapidfuzz import fuzz, process

from config.thresholds import CUSTOMER_NAME_FUZZY_MIN


# Common Indian company suffixes to normalise before fuzzy matching
_SUFFIX_MAP = {
    r"\bpvt\.?\b":      "private",
    r"\bltd\.?\b":      "limited",
    r"\bllp\.?\b":      "llp",
    r"\bco\.?\b":       "company",
    r"\bcorp\.?\b":     "corporation",
    r"\btech\.?\b":     "technologies",
    r"\btechnologies\b": "technologies",
}


def normalize_customer_name(name: str) -> str:
    """Lowercase, strip punctuation, and expand abbreviations."""
    n = name.lower().strip()
    n = re.sub(r"[^\w\s]", " ", n)
    for pattern, replacement in _SUFFIX_MAP.items():
        n = re.sub(pattern, replacement, n, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", n).strip()


def fuzzy_match_customer(candidate_name: str, all_customers: list[dict],
                          threshold: int = CUSTOMER_NAME_FUZZY_MIN) -> list[dict]:
    """
    Return customers whose normalised name is within fuzzy threshold of candidate_name.
    Each result includes the original customer dict plus a 'fuzzy_score' key.
    """
    if not candidate_name or not all_customers:
        return []

    norm_candidate = normalize_customer_name(candidate_name)
    customer_norms = {c["customer_id"]: normalize_customer_name(c["name"]) for c in all_customers}

    # Build list of (norm_name, customer_id) for process.extract
    choices = {cid: name for cid, name in customer_norms.items()}
    results = process.extract(
        norm_candidate,
        choices,
        scorer=fuzz.token_set_ratio,
        limit=5,
    )
    # results → [(matched_name, score, customer_id), ...]

    matched = []
    for matched_name, score, cid in results:
        if score >= threshold:
            customer = next((c for c in all_customers if c["customer_id"] == cid), None)
            if customer:
                matched.append({**customer, "fuzzy_score": score})

    matched.sort(key=lambda x: x["fuzzy_score"], reverse=True)
    return matched


def match_customer_identity(payer_name: str | None, payer_gstin: str | None,
                             all_customers: list[dict]) -> list[dict]:
    """
    Attempt to identify the customer behind a bank transaction.
    Prioritises GSTIN match, then falls back to fuzzy name matching.
    Returns candidate customer dicts with a 'match_method' key.
    """
    if not payer_name and not payer_gstin:
        return []

    # GSTIN exact match – highest confidence
    if payer_gstin:
        gstin_matches = [c for c in all_customers
                         if c.get("gstin") and c["gstin"].upper() == payer_gstin.upper()]
        if gstin_matches:
            return [{**c, "match_method": "gstin_exact", "fuzzy_score": 100} for c in gstin_matches]

    # Fuzzy name match
    if payer_name:
        fuzzy = fuzzy_match_customer(payer_name, all_customers)
        return [{**c, "match_method": "name_fuzzy"} for c in fuzzy]

    return []
