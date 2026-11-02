"""
Clean reconciliation smoke-test.
Loads the synthetic dataset via the same function the app uses,
runs reconcile_transaction() on all transactions, and prints results.
"""
import sys
sys.path.insert(0, ".")

from config.thresholds import AUTO_MATCH_THRESHOLD, REVIEW_THRESHOLD
print(f"AUTO_MATCH_THRESHOLD = {AUTO_MATCH_THRESHOLD}")
print(f"REVIEW_THRESHOLD     = {REVIEW_THRESHOLD}\n")

# Init DB schema first
from database.db import initialise_database
initialise_database()

# Load synthetic data
from extraction.ingestion import load_synthetic_dataset
result = load_synthetic_dataset()
print(f"Loaded: {result['invoices']} invoices, {result['transactions']} transactions\n")

from database.repositories import get_all_transactions
from decision.decision_engine import reconcile_transaction

txns = get_all_transactions()
print(f"{'TXN':8s}  {'OUTCOME':12s}  {'SCORE':6s}  {'INVOICE':12s}  NARRATION")
print("-" * 85)

matched = review = unmatched = duplicate = 0
for txn in txns:
    tid  = txn["transaction_id"]
    narr = (txn.get("narration") or "")[:30]
    result = reconcile_transaction(tid)
    outcome = result.get("outcome", "?")
    score   = result.get("score", result.get("confidence_score", 0.0))
    inv     = result.get("invoice_id", "")

    if   outcome == "matched":   matched   += 1
    elif outcome == "review":    review    += 1
    elif outcome == "unmatched": unmatched += 1
    elif outcome == "duplicate": duplicate += 1

    score_str = f"{score:.3f}" if isinstance(score, (int, float)) else str(score)
    marker = " ✓" if outcome == "matched" else (" ~" if outcome == "review" else " ✗")
    print(f"{tid:8s}  {outcome:12s}  {score_str:6s}  {str(inv):12s}  {narr}{marker}")

print("-" * 85)
print(f"\nSUMMARY: MATCHED={matched}  REVIEW={review}  UNMATCHED={unmatched}  DUPLICATE={duplicate}")
total = matched + review + unmatched + duplicate
print(f"Auto-match rate: {matched/total*100:.0f}%  |  Total: {total}")
