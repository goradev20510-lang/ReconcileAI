"""
Diagnostic: why are transactions going to REVIEW instead of MATCHED?
Prints scores and validation results for the first 5 transactions.
"""
import sys
sys.path.insert(0, ".")

from database.repositories import get_all_transactions, get_all_invoices
from matching.candidate_generator import generate_candidate_invoices, detect_multi_invoice_payment
from matching.scoring import score_candidates
from validation.financial_rules import validate_allocation

txns = get_all_transactions()[:5]
print(f"Checking first {len(txns)} transactions...\n")

for txn in txns:
    tid = txn["transaction_id"]
    narr = txn.get("narration", "")
    amt = txn.get("credit_amount", 0)

    candidates = generate_candidate_invoices(txn, None, None)
    if not candidates:
        print(f"{tid}: NO CANDIDATES (unmatched)")
        continue

    ranked = score_candidates(txn, candidates)
    best   = ranked[0]
    inv    = best["invoice"]
    score  = best["score"]
    sigs   = best["signals"]
    tds    = float(sigs.get("tds_amount", 0))

    validation = validate_allocation(amt, tds, inv, tds_section=None, has_tds_certificate=False)

    # Ambiguity check
    ambiguous = (len(ranked) > 1 and ranked[0]["score"] - ranked[1]["score"] < 0.10)

    print(f"{tid} | narr={narr[:30]:30s} | amt={amt:>10.2f}")
    print(f"  Best match: {inv['invoice_id']:12s} | score={score:.3f} | tds={tds:.2f}")
    print(f"  Signals: {sigs}")
    print(f"  Validation: overall_valid={validation['overall_valid']} "
          f"requires_review={validation['requires_review']}")
    print(f"  Ambiguous (top-2 within 0.10): {ambiguous}")
    if len(ranked) > 1:
        print(f"  2nd best: {ranked[1]['invoice']['invoice_id']} score={ranked[1]['score']:.3f}")
    print(f"  --> OUTCOME: ", end="")
    if ambiguous:
        print("REVIEW (ambiguous)")
    elif score >= 0.85 and validation["overall_valid"] and not validation["requires_review"]:
        print("AUTO-MATCH ✓")
    elif score >= 0.50 or validation["requires_review"]:
        print(f"REVIEW (score={score:.2f}, valid={validation['overall_valid']}, req_review={validation['requires_review']})")
    else:
        print("UNMATCHED")
    print()
