"""
End-to-end smoke test on the demo dataset, compared with its ground truth.
Uses a throw-away database, so your real data is never touched.

Run:  python -m scripts.smoke_reconcile            (LLM calls disabled)
      python -m scripts.smoke_reconcile --llm      (use the configured LLM)
      python -m scripts.smoke_reconcile --keep demo.db   (keep the DB for inspection)
"""
from __future__ import annotations
import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", action="store_true", help="allow LLM calls (narration hints, explanations)")
    ap.add_argument("--keep", metavar="PATH", help="write the database here instead of a temp file")
    args = ap.parse_args()
    if not args.llm:
        os.environ["LLM_ENABLED"] = "0"

    from database.db import set_database_path, initialise_database
    from config import thresholds as T
    db_path = Path(args.keep) if args.keep else Path(tempfile.mkdtemp()) / "smoke.db"
    if db_path.exists():
        db_path.unlink()
    set_database_path(db_path)
    initialise_database()

    from extraction.ingestion import load_synthetic_dataset
    from decision.decision_engine import run_batch_reconciliation
    from database.repositories import get_transaction, get_allocations_for_transaction

    print(f"params: {'learned' if T.LEARNED_PARAMS_ACTIVE else 'default'} | auto ≥ {T.AUTO_MATCH_THRESHOLD} "
          f"| review ≥ {T.REVIEW_THRESHOLD} | margin {T.AMBIGUITY_MARGIN}")
    loaded = load_synthetic_dataset()
    stats = run_batch_reconciliation(use_llm=args.llm)
    print(f"loaded {loaded['invoices']} invoices, {loaded['transactions']} transactions, "
          f"{loaded['remittances_stored']} remittances → {stats}\n")

    ok = 0
    print(f"{'TXN':8} {'SCENARIO':24} {'EXPECTED':10} {'GOT':10} {'INVOICE(S)':22} ")
    print("-" * 82)
    for gt in loaded["ground_truth"]:
        txn = get_transaction(gt["transaction_id"])
        got = txn["status"] if txn else "missing"
        allocs = [a["invoice_id"] for a in get_allocations_for_transaction(gt["transaction_id"])
                  if a["allocation_status"] in ("approved", "proposed")]
        want = gt["expected_outcome"]
        good = got == want or (want == "unmatched" and got == "review" and not allocs)
        ok += good
        print(f"{gt['transaction_id']:8} {gt['scenario'][:24]:24} {want:10} {got:10} "
              f"{','.join(allocs)[:22]:22} {'✓' if good else '✗'}")
    print("-" * 82)
    print(f"{ok}/{len(loaded['ground_truth'])} outcomes as expected · database: {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
