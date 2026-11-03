"""Tests for the training-data generator and the matcher calibration pipeline."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import math


def test_generator_is_deterministic_and_labelled():
    from data.synthetic.generate_training_data import generate
    a, b = generate(5, seed=123), generate(5, seed=123)
    assert json.dumps(a) == json.dumps(b)
    for batch in a:
        inv_ids = {i["invoice_id"] for i in batch["invoices"]}
        assert len(batch["labels"]) == len(batch["transactions"])
        for lab in batch["labels"]:
            assert lab["expected_outcome"] in ("matched", "review", "unmatched")
            assert set(lab["true_invoice_ids"]) <= inv_ids
            if lab["expected_outcome"] == "unmatched":
                assert lab["true_invoice_ids"] == []


def test_replay_matches_real_scoring():
    """The vectorised decision replay must score exactly like matching/scoring.py."""
    from data.synthetic.generate_training_data import generate
    from training import train_matcher as tm
    D = tm.extract(generate(8, seed=7))
    p = {**tm.T.DEFAULT_PARAMS, "customer_name_fuzzy_min": tm.FUZZY_FLOOR}
    assert tm.fidelity_check(D, p, n=len(D["rows"])) == 0


def test_learned_params_are_sane():
    from config import thresholds as t
    if not t.LEARNED_PARAMS_PATH.exists():
        return
    p = json.loads(t.LEARNED_PARAMS_PATH.read_text())["params"]
    assert set(p["score_weights"]) == set(t.SCORE_WEIGHTS)
    assert math.isclose(sum(p["score_weights"].values()), 1.0, abs_tol=1e-3)
    assert all(w >= 0 for w in p["score_weights"].values())
    assert 0 < p["review_threshold"] < p["auto_match_threshold"] <= 1
    assert p["ambiguity_margin"] > 0, "exact ties must always be sent to review"
    # An invoice reference alone must never be enough to auto-match;
    # it needs a corroborating amount.
    assert p["score_weights"]["invoice_ref_exact"] < p["auto_match_threshold"]


def test_learned_params_beat_baseline_without_wrong_auto_matches():
    from data.synthetic.generate_training_data import generate
    from training import train_matcher as tm
    if not tm.T.LEARNED_PARAMS_PATH.exists():
        return
    learned = json.loads(tm.T.LEARNED_PARAMS_PATH.read_text())["params"]
    D = tm.extract(generate(40, seed=2024))      # unseen seed
    base, new = tm.metrics(D, tm.T.DEFAULT_PARAMS), tm.metrics(D, learned)
    assert new["mean_cost"] < base["mean_cost"]
    assert new["auto_match_precision"] >= 0.995
