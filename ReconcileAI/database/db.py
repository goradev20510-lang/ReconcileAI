"""
ReconcileAI – Database Connection Manager
Provides a thread-safe SQLite connection with WAL mode enabled, schema creation
and lightweight in-place migrations for databases created by older versions.
"""
from __future__ import annotations
import sqlite3
import threading
from pathlib import Path

from config import settings


# One connection per thread
_local = threading.local()
_path_override: Path | None = None


def get_database_path() -> Path:
    return _path_override or settings.DATABASE_PATH


def set_database_path(path: str | Path | None) -> None:
    """Point the app at a different database file (used by tests and scripts)."""
    global _path_override
    close_connection()
    _path_override = Path(path) if path else None


def get_connection() -> sqlite3.Connection:
    """Return (or create) a thread-local SQLite connection."""
    path = get_database_path()
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) != path:
        conn.close()
        conn = None
    if conn is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), detect_types=sqlite3.PARSE_DECLTYPES, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        _local.conn = conn
        _local.path = path
    return conn


def initialise_database() -> None:
    """Create all tables if they don't already exist, then apply migrations."""
    schema_path = Path(__file__).parent / "schema.sql"
    conn = get_connection()
    _migrate(conn)                       # must run before CREATE INDEX on new columns
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    conn.commit()


def reset_database() -> None:
    """Delete every row in every table (keeps the schema). Used by 'Reset data'."""
    conn = get_connection()
    conn.execute("PRAGMA foreign_keys = OFF")
    for table in ("settlement_components", "payment_allocations", "review_cases",
                  "remittance_records", "extractions", "audit_logs", "reconciliation_batches",
                  "bank_transactions", "invoices", "customers", "documents"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


def close_connection() -> None:
    """Close the thread-local connection."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
    _local.conn = None
    _local.path = None


# ── Migrations ────────────────────────────────────────────────────────────────

def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,)).fetchone() is not None


def _migrate(conn: sqlite3.Connection) -> None:
    # v1 → v2: bank_transactions.utr was UNIQUE, which silently dropped a second
    # credit carrying the same UTR instead of letting duplicate detection flag it.
    if _table_exists(conn, "bank_transactions"):
        unique_utr = False
        for idx in conn.execute("PRAGMA index_list(bank_transactions)").fetchall():
            if idx["unique"] and idx["origin"] == "u":
                cols = [c["name"] for c in conn.execute(f"PRAGMA index_info('{idx['name']}')")]
                unique_utr |= cols == ["utr"]
        if unique_utr:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.executescript("""
                BEGIN;
                CREATE TABLE bank_transactions_new (
                    transaction_id   TEXT PRIMARY KEY,
                    utr              TEXT,
                    narration        TEXT,
                    credit_amount    REAL NOT NULL DEFAULT 0,
                    debit_amount     REAL NOT NULL DEFAULT 0,
                    transaction_date TEXT,
                    bank_name        TEXT,
                    account_number   TEXT,
                    currency         TEXT NOT NULL DEFAULT 'INR',
                    status           TEXT NOT NULL DEFAULT 'unmatched',
                    batch_id         TEXT,
                    source_document_id TEXT,
                    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
                );
                INSERT INTO bank_transactions_new SELECT
                    transaction_id, utr, narration, credit_amount, debit_amount, transaction_date,
                    bank_name, account_number, currency, status, batch_id, source_document_id, created_at
                FROM bank_transactions;
                DROP TABLE bank_transactions;
                ALTER TABLE bank_transactions_new RENAME TO bank_transactions;
                COMMIT;
            """)
            conn.execute("PRAGMA foreign_keys = ON")

    # v2: remittances carry the TDS section so the right rate is validated
    if _table_exists(conn, "remittance_records") and "tds_section" not in _columns(conn, "remittance_records"):
        conn.execute("ALTER TABLE remittance_records ADD COLUMN tds_section TEXT")
    if _table_exists(conn, "remittance_records") and "customer_gstin" not in _columns(conn, "remittance_records"):
        conn.execute("ALTER TABLE remittance_records ADD COLUMN customer_gstin TEXT")

    # v2: older engines opened a new case on every re-run. Keep only the newest open
    # case per transaction; the rest are marked 'superseded' (idempotent).
    if _table_exists(conn, "review_cases"):
        conn.execute("""
            UPDATE review_cases SET status='superseded',
                   resolution_notes=COALESCE(resolution_notes, 'Superseded by a newer case (migration)')
            WHERE status='open' AND rowid NOT IN (
                SELECT MAX(rowid) FROM review_cases WHERE status='open' GROUP BY transaction_id)""")
        # A credit that is already matched (or excluded) has nothing left to decide.
        if _table_exists(conn, "bank_transactions"):
            conn.execute("""
                UPDATE review_cases SET status='superseded',
                       resolution_notes=COALESCE(resolution_notes, 'Credit already resolved (migration)')
                WHERE status='open' AND transaction_id IN (
                    SELECT transaction_id FROM bank_transactions WHERE status IN ('matched','excluded'))""")
    conn.commit()
