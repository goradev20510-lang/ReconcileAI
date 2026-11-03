"""
ReconcileAI – Matcher Calibration ("training")
Learns the scoring weights and decision thresholds used by matching/scoring.py
and decision/decision_engine.py from labelled synthetic batches.

Pipeline
  1. Load batches written by data/synthetic/generate_training_data.py.
  2. For every transaction, run the *real* candidate generator
     (matching.candidate_generator.generate_candidate_invoices) against that
     batch's in-memory ledger and the real financial validation
     (validation.financial_rules.validate_allocation). No DB, no LLM.
  3. Turn each candidate into the six scoring features used by scoring.py.
  4. Replay the decision engine's gates (ambiguity margin → auto-match →
     partial-with-ref → review → unmatched) vectorised in numpy, so thousands
     of parameter settings can be evaluated per second.
  5. Search weights + thresholds that minimise a business cost on the train
     split (a wrong auto-match costs 10× an unnecessary review), pick by
     validation cost, report on a held-out test split.
  6. Write config/learned_params.json, which config/thresholds.py loads.

Run:
  python -m data.synthetic.generate_training_data     # once, creates the data
  python -m training.train_matcher                    # trains + writes params
  python -m training.train_matcher --dry-run          # evaluate only
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ["RECONCILE_USE_LEARNED_PARAMS"] = "0"   # baseline = hand-set defaults

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config.thresholds as T                              # noqa: E402
from decision.decision_engine import is_auto_approvable_partial, _find_bulk_combo  # noqa: E402
import matching.candidate_generator as cg                  # noqa: E402
from matching.scoring import score_candidates              # noqa: E402
from validation.financial_rules import validate_allocation, infer_tds_deduction  # noqa: E402

DATA_PATH = ROOT / "data" / "training" / "batches.jsonl"
FEATURES = ["invoice_ref_exact", "customer_exact", "customer_fuzzy",
            "amount_exact", "amount_within_tol", "date_proximity"]
FUZZY_FLOOR = 55          # candidates are generated at this floor; the learned min filters on top
OUTCOMES = ("matched", "review", "unmatched")
MIN_AMBIGUITY_MARGIN = 0.02

# ── Business cost ─────────────────────────────────────────────────────────────
COST_WRONG_AUTO      = 10.0   # money posted to the wrong invoice
COST_AUTO_NEEDS_CHECK = 3.0   # right invoice, but policy says a human should have looked
COST_UNMATCHED_MISS  = 4.0    # evidence existed but we dropped it into "unmatched"
COST_REVIEW_INSTEAD_OF_AUTO = 1.0   # avoidable manual work
COST_REVIEW_WRONG_SUGGESTION = 0.5
COST_REVIEW_UNMATCHED = 0.5


# ═════════════════════════════════════════════════════════════════════════════
# 1–3  Feature extraction with the real candidate generator
# ═════════════════════════════════════════════════════════════════════════════

def _date_frac(txn_date: str | None, due_date: str | None) -> float:
    # Same buckets as scoring._date_proximity_score, as a fraction of the weight.
    from dateutil import parser as dp
    if not txn_date or not due_date:
        return 0.0
    diff = abs((dp.parse(txn_date).date() - dp.parse(due_date).date()).days)
    return 1.0 if diff <= 7 else 0.5 if diff <= 30 else 0.2 if diff <= 90 else 0.0


def _features(txn: dict, inv: dict, sig: dict) -> tuple[list[float], bool]:
    """Return (feature vector, amount_exact flag) matching scoring._score_candidate."""
    from decimal import Decimal
    credit = Decimal(str(txn["credit_amount"])) + Decimal(str(sig.get("tds_amount", 0)))
    total = Decimal(str(inv["total_amount"]))
    out = Decimal(str(inv["outstanding_amount"]))
    tol = total * T.AMOUNT_TOLERANCE_PERCENT
    amt_exact = amt_tol = 0.0
    if abs(credit - total) <= tol or abs(credit - out) <= tol:
        if credit == total or credit == out:
            amt_exact = 1.0
        else:
            amt_tol = 1.0
    return [
        1.0 if sig["invoice_ref_exact"] else 0.0,
        0.0, 0.0,                         # customer features filled per fuzzy-min at eval time
        amt_exact, amt_tol,
        _date_frac(txn.get("transaction_date"), inv.get("due_date")),
    ], bool(amt_exact)


def extract(batches: list[dict]) -> dict:
    """Run the real candidate generator + validation for every transaction."""
    import matching.fuzzy_match as fm
    fm._thresholds.CUSTOMER_NAME_FUZZY_MIN = FUZZY_FLOOR     # generate wide; filter later

    rows = []   # one per transaction
    for b in batches:
        invoices = b["invoices"]
        customers = b["customers"]
        cg.get_all_invoices = lambda inv=invoices: inv
        cg.get_all_customers = lambda c=customers: c
        rem_by_utr = {r["utr"]: r for r in b["remittances"]}
        labels = {lab["transaction_id"]: lab for lab in b["labels"]}

        for txn in b["transactions"]:
            rem = rem_by_utr.get(txn["utr"])
            cands = cg.generate_candidate_invoices(txn, None, rem)
            cands.sort(key=lambda c: c["invoice"]["invoice_id"])  # deterministic order
            tds_section = (rem or {}).get("tds_section")
            cand_rows = []
            for c in cands:
                inv, sig = c["invoice"], c["signals"]
                f, _ = _features(txn, inv, sig)
                tds = float(sig.get("tds_amount", 0))
                v = validate_allocation(txn["credit_amount"], tds, inv,
                                        tds_section=sig.get("tds_section"), has_tds_certificate=False)
                implied = (infer_tds_deduction(txn["credit_amount"], inv)
                           if tds == 0 and v["amount_validation"]["is_partial"] else None)
                partial_only = is_auto_approvable_partial(v, tds, implied)
                cand_rows.append({
                    "invoice_id": inv["invoice_id"], "f": f,
                    "fuzzy": float(sig["fuzzy_score"]) if sig["customer_match"] else 0.0,
                    "gstin": sig["match_method"] == "gstin_exact",
                    "from_ref": sig["invoice_ref_exact"], "from_amount": sig["amount_match"],
                    "valid": v["overall_valid"], "needs_review": v["requires_review"],
                    "partial_only": partial_only,
                    "within_tol": v["amount_validation"]["within_tolerance"],
                    # raw inputs for the fidelity check against scoring.py
                    "_inv": inv, "_sig": sig,
                })
            # Bulk-payment rescue (engine: _find_bulk_combo) only depends on the candidate set
            bulk = bool(_find_bulk_combo(txn, [{**c, "score": 0.0} for c in cands],
                                         float(cands[0]["signals"].get("tds_amount", 0)) if cands else 0.0))
            rows.append({"txn": txn, "label": labels[txn["transaction_id"]], "cands": cand_rows,
                         "batch_id": b["batch_id"], "tds_section": tds_section, "bulk": bulk})
    return _pack(rows)


def _pack(rows: list[dict]) -> dict:
    n = len(rows)
    cmax = max(1, max(len(r["cands"]) for r in rows))
    F = np.zeros((n, cmax, len(FEATURES)))
    fuzzy = np.zeros((n, cmax))
    gstin = np.zeros((n, cmax), bool)
    exists = np.zeros((n, cmax), bool)
    other_src = np.zeros((n, cmax), bool)     # candidate survives without the customer signal
    valid = np.zeros((n, cmax), bool)
    needs_review = np.zeros((n, cmax), bool)
    partial_only = np.zeros((n, cmax), bool)
    within_tol = np.zeros((n, cmax), bool)
    is_true = np.zeros((n, cmax), bool)
    for i, r in enumerate(rows):
        true_ids = set(r["label"]["true_invoice_ids"])
        for j, c in enumerate(r["cands"]):
            F[i, j] = c["f"]
            fuzzy[i, j] = c["fuzzy"]
            gstin[i, j] = c["gstin"]
            exists[i, j] = True
            other_src[i, j] = c["from_ref"] or c["from_amount"]
            valid[i, j] = c["valid"]
            needs_review[i, j] = c["needs_review"]
            partial_only[i, j] = c["partial_only"]
            within_tol[i, j] = c["within_tol"]
            is_true[i, j] = c["invoice_id"] in true_ids
    exp = np.array([OUTCOMES.index(r["label"]["expected_outcome"]) for r in rows])
    return {
        "rows": rows, "F": F, "fuzzy": fuzzy, "gstin": gstin, "exists": exists,
        "other_src": other_src, "valid": valid, "needs_review": needs_review,
        "partial_only": partial_only, "within_tol": within_tol, "is_true": is_true, "expected": exp,
        "bulk": np.array([r["bulk"] for r in rows], bool),
        "auto_ok": np.array([r["label"]["auto_ok"] for r in rows]),
        "has_truth": np.array([bool(r["label"]["true_invoice_ids"]) for r in rows]),
        "scenario": np.array([r["label"]["scenario"] for r in rows]),
        "batch": np.array([r["batch_id"] for r in rows]),
    }


def subset(D: dict, mask: np.ndarray) -> dict:
    out = {}
    for k, v in D.items():
        if k == "rows":
            out[k] = [r for r, m in zip(v, mask) if m]
        else:
            out[k] = v[mask]
    return out


# ═════════════════════════════════════════════════════════════════════════════
# 4  Vectorised replay of decision_engine.reconcile_transaction
# ═════════════════════════════════════════════════════════════════════════════

def decide(D: dict, p: dict) -> dict:
    w = np.array([p["score_weights"][k] for k in
                  ("invoice_ref_exact", "customer_exact", "customer_fuzzy",
                   "amount_exact", "amount_within_tol", "date_proximity")])
    cust_on = D["exists"] & (D["fuzzy"] >= p["customer_name_fuzzy_min"])
    alive = D["exists"] & (D["other_src"] | cust_on)
    F = D["F"].copy()
    exact_c = cust_on & ((D["fuzzy"] == 100) | D["gstin"])
    F[..., 1] = exact_c
    F[..., 2] = np.where(cust_on & ~exact_c, D["fuzzy"] / 100.0, 0.0)
    s = np.round(np.minimum(F @ w, 1.0), 4)
    s = np.where(alive, s, -np.inf)

    n = s.shape[0]
    idx = np.arange(n)
    top = np.argmax(s, axis=1)                       # first max == stable sort order
    s0 = s[idx, top]
    s_rest = s.copy()
    s_rest[idx, top] = -np.inf
    s1 = s_rest.max(axis=1)
    any_c = np.isfinite(s0)
    n_alive = alive.sum(axis=1)

    with np.errstate(invalid="ignore"):
        gap = np.where(n_alive > 1, s0 - s1, np.inf)
    ambiguous = any_c & (gap < p["ambiguity_margin"])
    v_ok = D["valid"][idx, top] & ~D["needs_review"][idx, top]
    ref = F[idx, top, 0] > 0
    part = D["partial_only"][idx, top]
    hi = s0 >= p["auto_match_threshold"]
    auto = any_c & ~ambiguous & hi & (v_ok | (ref & part))
    review = any_c & ~ambiguous & ~auto & ((s0 >= p["review_threshold"]) | D["needs_review"][idx, top])
    review |= ambiguous
    # Bulk combo found and the best single invoice doesn't fit the amount → review
    review |= any_c & ~auto & D["bulk"] & (~D["within_tol"][idx, top] | ambiguous)
    outcome = np.where(auto, 0, np.where(review, 1, 2))
    return {"outcome": outcome, "top_true": any_c & D["is_true"][idx, top], "score": s0}


def cost(D: dict, dec: dict) -> np.ndarray:
    o, exp, tt = dec["outcome"], D["expected"], dec["top_true"]
    c = np.zeros(len(o))
    m = o == 0
    c[m & ~tt] = COST_WRONG_AUTO
    c[m & tt & ~D["auto_ok"] & (exp != 0)] = COST_AUTO_NEEDS_CHECK
    r = o == 1
    c[r & (exp == 0)] = COST_REVIEW_INSTEAD_OF_AUTO
    c[r & (exp == 1) & D["has_truth"] & ~tt] = COST_REVIEW_WRONG_SUGGESTION
    c[r & (exp == 2)] = COST_REVIEW_UNMATCHED
    u = o == 2
    c[u & (exp != 2)] = COST_UNMATCHED_MISS
    return c


def metrics(D: dict, p: dict) -> dict:
    dec = decide(D, p)
    o, exp, tt = dec["outcome"], D["expected"], dec["top_true"]
    auto = o == 0
    c = cost(D, dec)
    return {
        "transactions": int(len(o)),
        "mean_cost": float(c.mean()),
        "outcome_accuracy": float((o == exp).mean()),
        "zero_cost_rate": float((c == 0).mean()),
        "auto_match_rate": float(auto.mean()),
        "auto_match_precision": float(tt[auto].mean()) if auto.any() else 1.0,
        "wrong_auto_matches": int((auto & ~tt).sum()),
        "review_rate": float((o == 1).mean()),
        "unmatched_rate": float((o == 2).mean()),
        "missed_to_unmatched": int(((o == 2) & (exp != 2)).sum()),
        "top1_accuracy_when_truth": float(tt[D["has_truth"]].mean()),
    }


def per_scenario(D: dict, p: dict) -> dict[str, dict]:
    dec = decide(D, p)
    out = {}
    for sc in sorted(set(D["scenario"])):
        m = D["scenario"] == sc
        o = dec["outcome"][m]
        out[sc] = {
            "n": int(m.sum()),
            "expected": OUTCOMES[int(np.bincount(D["expected"][m]).argmax())],
            **{k: float((o == i).mean()) for i, k in enumerate(OUTCOMES)},
            "top_true": float(dec["top_true"][m].mean()),
        }
    return out


# ═════════════════════════════════════════════════════════════════════════════
# 5  Search
# ═════════════════════════════════════════════════════════════════════════════

def _vec_to_params(x: np.ndarray, fuzzy_min: int) -> dict:
    w = np.clip(x[:6], 1e-4, None)
    w = w / w.sum()
    auto = float(np.clip(x[6], 0.30, 0.99))
    rev = float(np.clip(x[7], 0.02, auto - 0.01))
    return {
        "score_weights": {k: round(float(v), 4) for k, v in zip(T.SCORE_WEIGHTS, w)},
        "auto_match_threshold": round(auto, 4),
        "review_threshold": round(rev, 4),
        # Floor > 0: two candidates with *identical* scores must always go to review,
        # even though the training data may never contain such a tie.
        "ambiguity_margin": round(float(np.clip(x[8], MIN_AMBIGUITY_MARGIN, 0.40)), 4),
        "customer_name_fuzzy_min": int(fuzzy_min),
    }


def _params_to_vec(p: dict) -> np.ndarray:
    return np.array([*p["score_weights"].values(), p["auto_match_threshold"],
                     p["review_threshold"], p["ambiguity_margin"]], float)


def search(train: dict, val: dict, start: dict, *, n_random: int, n_local: int, seed: int,
           log=print) -> dict:
    rng = np.random.default_rng(seed)
    fuzzy_grid = list(range(60, 96, 5))

    def J(p, D=train):
        return float(cost(D, decide(D, p)).mean())

    best_p, best_j = start, J(start)
    pool: list[tuple[float, dict]] = [(best_j, start)]
    t0 = time.time()

    # Global random search
    for _ in range(n_random):
        x = np.concatenate([rng.dirichlet(np.ones(6)),
                            [rng.uniform(0.35, 0.95), rng.uniform(0.03, 0.6), rng.uniform(0.0, 0.3)]])
        p = _vec_to_params(x, rng.choice(fuzzy_grid))
        j = J(p)
        pool.append((j, p))
        if j < best_j:
            best_p, best_j = p, j
    log(f"   random search: {n_random} trials, best train cost {best_j:.4f} ({time.time()-t0:.1f}s)")

    # Local refinement from the top few starts (simple annealed hill-climb)
    pool.sort(key=lambda t: t[0])
    starts = [p for _, p in pool[:5]]
    for si, p0 in enumerate(starts):
        p, j = p0, J(p0)
        step = 0.08
        for it in range(n_local):
            x = _params_to_vec(p) + rng.normal(0, step, 9) * np.array([1, 1, 1, 1, 1, 1, .5, .5, .3])
            fz = p["customer_name_fuzzy_min"]
            if rng.random() < 0.1:
                fz = int(np.clip(fz + rng.choice([-5, 5]), 60, 95))
            q = _vec_to_params(x, fz)
            jq = J(q)
            if jq <= j:
                p, j = q, jq
            step = max(0.005, step * 0.997)
        pool.append((j, p))
        if j < best_j:
            best_p, best_j = p, j
    log(f"   local search:  {len(starts)}×{n_local} steps, best train cost {best_j:.4f} ({time.time()-t0:.1f}s)")

    # Pick the final model by validation cost among the best train candidates
    pool.sort(key=lambda t: t[0])
    shortlist = pool[:25]
    chosen = min(shortlist, key=lambda t: (J(t[1], val), t[0]))[1]
    log(f"   chosen by validation cost: {J(chosen, val):.4f}")
    return chosen


# ═════════════════════════════════════════════════════════════════════════════
# Fidelity check: the vectorised replay must agree with scoring.py
# ═════════════════════════════════════════════════════════════════════════════

def fidelity_check(D: dict, p: dict, n: int = 400) -> int:
    """Compare replayed scores against matching.scoring.score_candidates (default weights)."""
    w_backup = dict(T.SCORE_WEIGHTS)
    import matching.scoring as sc_mod
    sc_mod.SCORE_WEIGHTS.clear()
    sc_mod.SCORE_WEIGHTS.update(p["score_weights"])
    mismatches = 0
    try:
        dec = decide(D, {**p, "customer_name_fuzzy_min": FUZZY_FLOOR})
        for i, r in enumerate(D["rows"][:n]):
            if not r["cands"]:
                continue
            ranked = score_candidates(r["txn"], [{"invoice": c["_inv"], "signals": c["_sig"]}
                                                 for c in r["cands"]])
            if abs(ranked[0]["score"] - dec["score"][i]) > 1e-3:   # float summation order
                mismatches += 1
    finally:
        sc_mod.SCORE_WEIGHTS.clear()
        sc_mod.SCORE_WEIGHTS.update(w_backup)
    return mismatches


# ═════════════════════════════════════════════════════════════════════════════
# CLI
# ═════════════════════════════════════════════════════════════════════════════

def _fmt(m: dict) -> str:
    return (f"cost {m['mean_cost']:.3f} | outcome acc {m['outcome_accuracy']:.1%} | "
            f"auto {m['auto_match_rate']:.1%} @ precision {m['auto_match_precision']:.2%} "
            f"({m['wrong_auto_matches']} wrong) | review {m['review_rate']:.1%} | "
            f"unmatched {m['unmatched_rate']:.1%} ({m['missed_to_unmatched']} missed)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=DATA_PATH)
    ap.add_argument("--out", type=Path, default=T.LEARNED_PARAMS_PATH)
    ap.add_argument("--random-trials", type=int, default=4000)
    ap.add_argument("--local-steps", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true", help="evaluate defaults (and current learned file) only")
    args = ap.parse_args()

    if not args.data.exists():
        sys.exit(f"No training data at {args.data}. Run: python -m data.synthetic.generate_training_data")

    batches = [json.loads(line) for line in args.data.read_text(encoding="utf-8").splitlines() if line]
    print(f"📦 {len(batches)} batches loaded from {args.data}")
    t0 = time.time()
    D = extract(batches)
    print(f"🔎 features for {len(D['rows']):,} transactions extracted in {time.time()-t0:.1f}s")

    # Split by batch so no ledger leaks across splits: 70 / 15 / 15
    rng = np.random.default_rng(args.seed)
    ids = sorted(set(D["batch"]))
    rng.shuffle(ids)
    n = len(ids)
    split_of = {b: ("train" if k < 0.7 * n else "val" if k < 0.85 * n else "test") for k, b in enumerate(ids)}
    split = np.array([split_of[b] for b in D["batch"]])
    train, val, test = (subset(D, split == s) for s in ("train", "val", "test"))
    print(f"   split → train {len(train['rows']):,} | val {len(val['rows']):,} | test {len(test['rows']):,}")

    baseline = dict(T.DEFAULT_PARAMS)
    bad = fidelity_check(D, {**baseline, "customer_name_fuzzy_min": FUZZY_FLOOR})
    print(f"✔︎  fidelity check vs scoring.py: {bad} mismatches in sample")
    if bad:
        sys.exit("Replay disagrees with matching/scoring.py — fix before trusting results.")

    print("\nBaseline (hand-set) params on test:\n   " + _fmt(metrics(test, baseline)))
    if args.dry_run:
        if args.out.exists():
            cur = json.loads(args.out.read_text())["params"]
            print("Current learned params on test:\n   " + _fmt(metrics(test, cur)))
        return

    print("\n🏋️  training…")
    learned = search(train, val, baseline, n_random=args.random_trials,
                     n_local=args.local_steps, seed=args.seed)

    report = {s: {"baseline": metrics(d, baseline), "learned": metrics(d, learned)}
              for s, d in (("train", train), ("val", val), ("test", test))}
    print("\nLearned params on test:\n   " + _fmt(report["test"]["learned"]))

    print("\nPer-scenario outcomes on test (baseline → learned):")
    pb, pl = per_scenario(test, baseline), per_scenario(test, learned)
    print(f"   {'scenario':<28}{'n':>5}  {'want':<9} {'baseline M/R/U':<18} {'learned M/R/U':<18}")
    for sc in pb:
        b, l = pb[sc], pl[sc]
        f = lambda d: f"{d['matched']:.0%}/{d['review']:.0%}/{d['unmatched']:.0%}"
        print(f"   {sc:<28}{b['n']:>5}  {b['expected']:<9} {f(b):<18} {f(l):<18}")

    print("\nLearned parameters:")
    print(json.dumps(learned, indent=2))

    payload = {
        "params": learned,
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "data": {"path": str(args.data.relative_to(ROOT)) if args.data.is_relative_to(ROOT) else str(args.data),
                 "batches": len(batches), "transactions": len(D["rows"])},
        "search": {"random_trials": args.random_trials, "local_steps": args.local_steps, "seed": args.seed},
        "cost_model": {k: v for k, v in globals().items() if k.startswith("COST_")},
        "baseline_params": baseline,
        "metrics": report,
        "per_scenario_test": {"baseline": pb, "learned": pl},
    }
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\n💾 wrote {args.out}")


if __name__ == "__main__":
    main()
