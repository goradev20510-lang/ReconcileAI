"""Shared pytest setup: offline LLM, and a throw-away SQLite database per test."""
import os
import sys

os.environ["LLM_ENABLED"] = "0"                     # never call a real LLM from tests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


@pytest.fixture()
def db(tmp_path):
    """Fresh, initialised database for one test."""
    from database.db import set_database_path, initialise_database, close_connection
    set_database_path(tmp_path / "test.db")
    initialise_database()
    yield tmp_path / "test.db"
    close_connection()
    set_database_path(None)


@pytest.fixture()
def demo_db(db):
    """Database loaded with the demo dataset (not yet reconciled). Yields the ground truth."""
    from extraction.ingestion import load_synthetic_dataset
    return load_synthetic_dataset()["ground_truth"]


@pytest.fixture()
def reconciled_demo(demo_db):
    from decision.decision_engine import run_batch_reconciliation
    stats = run_batch_reconciliation(use_llm=False)
    return demo_db, stats
