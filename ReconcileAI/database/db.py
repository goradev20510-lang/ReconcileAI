"""
ReconcileAI – Database Connection Manager
Provides a thread-safe SQLite connection with WAL mode enabled.
"""
from __future__ import annotations
import sqlite3
import threading
from pathlib import Path
from config.settings import DATABASE_PATH


# One connection per thread
_local = threading.local()


def get_connection() -> sqlite3.Connection:
    """Return (or create) a thread-local SQLite connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DATABASE_PATH), detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        _local.conn = conn
    return _local.conn


def initialise_database() -> None:
    """Create all tables if they don't already exist."""
    schema_path = Path(__file__).parent / "schema.sql"
    conn = get_connection()
    with open(schema_path, "r", encoding="utf-8") as f:
        sql = f.read()
    conn.executescript(sql)
    conn.commit()


def close_connection() -> None:
    """Close the thread-local connection."""
    if hasattr(_local, "conn") and _local.conn:
        _local.conn.close()
        _local.conn = None
