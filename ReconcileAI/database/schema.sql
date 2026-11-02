-- ReconcileAI – SQLite Schema
-- Run this file once to initialise the database.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ── Customers ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS customers (
    customer_id   TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    name_variants TEXT,              -- JSON array of known aliases
    gstin         TEXT,
    pan           TEXT,
    email         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Invoices ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS invoices (
    invoice_id        TEXT PRIMARY KEY,
    customer_id       TEXT REFERENCES customers(customer_id),
    supplier_name     TEXT,
    customer_name     TEXT,
    customer_gstin    TEXT,
    invoice_date      TEXT,
    due_date          TEXT,
    taxable_amount    REAL NOT NULL DEFAULT 0,
    gst_amount        REAL NOT NULL DEFAULT 0,
    total_amount      REAL NOT NULL,
    outstanding_amount REAL NOT NULL,
    currency          TEXT NOT NULL DEFAULT 'INR',
    status            TEXT NOT NULL DEFAULT 'open',
    -- open | partially_paid | settled | overpaid
    source_document_id TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Bank Transactions ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bank_transactions (
    transaction_id   TEXT PRIMARY KEY,
    utr              TEXT UNIQUE,
    narration        TEXT,
    credit_amount    REAL NOT NULL DEFAULT 0,
    debit_amount     REAL NOT NULL DEFAULT 0,
    transaction_date TEXT,
    bank_name        TEXT,
    account_number   TEXT,
    currency         TEXT NOT NULL DEFAULT 'INR',
    status           TEXT NOT NULL DEFAULT 'unmatched',
    -- unmatched | matched | review | duplicate
    batch_id         TEXT,
    source_document_id TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Uploaded Documents ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS documents (
    document_id    TEXT PRIMARY KEY,
    filename       TEXT NOT NULL,
    document_type  TEXT,
    -- bank_statement | invoice | remittance | tds_certificate | unknown
    file_hash      TEXT NOT NULL,
    file_path      TEXT,
    upload_time    TEXT NOT NULL DEFAULT (datetime('now')),
    batch_id       TEXT,
    UNIQUE(file_hash)
);

-- ── Extraction Results ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS extractions (
    extraction_id   TEXT PRIMARY KEY,
    document_id     TEXT REFERENCES documents(document_id),
    extractor       TEXT,           -- llm | csv_parser | pdf_parser
    raw_output      TEXT,           -- JSON from LLM / parser
    validated_output TEXT,          -- JSON after Pydantic validation
    extraction_status TEXT,         -- complete | partial | failed
    missing_fields  TEXT,           -- JSON array
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Remittance Records ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS remittance_records (
    remittance_id      TEXT PRIMARY KEY,
    document_id        TEXT REFERENCES documents(document_id),
    customer_name      TEXT,
    invoice_references TEXT,        -- JSON array
    payment_amount     REAL,
    tds_amount         REAL DEFAULT 0,
    utr                TEXT,
    payment_date       TEXT,
    currency           TEXT DEFAULT 'INR',
    evidence_text      TEXT,        -- JSON array of verbatim excerpts
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Payment Allocations ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS payment_allocations (
    allocation_id      TEXT PRIMARY KEY,
    transaction_id     TEXT NOT NULL REFERENCES bank_transactions(transaction_id),
    invoice_id         TEXT NOT NULL REFERENCES invoices(invoice_id),
    allocated_amount   REAL NOT NULL,
    allocation_status  TEXT NOT NULL DEFAULT 'proposed',
    -- proposed | approved | rejected | reversed
    confidence_score   REAL,
    decision_reason    TEXT,
    approved_by        TEXT,        -- 'system' or user name
    approved_at        TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Settlement Components ─────────────────────────────────────────────────────
-- Breaks an allocation into its constituent parts (cash + TDS + adjustments)
CREATE TABLE IF NOT EXISTS settlement_components (
    component_id     TEXT PRIMARY KEY,
    allocation_id    TEXT NOT NULL REFERENCES payment_allocations(allocation_id),
    component_type   TEXT NOT NULL,   -- bank_credit | tds | adjustment
    amount           REAL NOT NULL,
    tds_section      TEXT,
    tds_status       TEXT,            -- claimed | evidence_pending | verified | rejected
    supporting_doc   TEXT,
    notes            TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Human Review Queue ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS review_cases (
    case_id           TEXT PRIMARY KEY,
    transaction_id    TEXT REFERENCES bank_transactions(transaction_id),
    invoice_id        TEXT,           -- best candidate (may be null)
    exception_type    TEXT NOT NULL,
    exception_summary TEXT,
    suggested_action  TEXT,
    status            TEXT NOT NULL DEFAULT 'open',
    -- open | approved | rejected | corrected | escalated
    reviewer          TEXT,
    reviewed_at       TEXT,
    resolution_notes  TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Audit Logs ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_logs (
    log_id        TEXT PRIMARY KEY,
    entity_type   TEXT NOT NULL,   -- transaction | invoice | allocation | review_case
    entity_id     TEXT NOT NULL,
    action        TEXT NOT NULL,
    actor         TEXT NOT NULL DEFAULT 'system',
    details       TEXT,            -- JSON
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Reconciliation Batches ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reconciliation_batches (
    batch_id          TEXT PRIMARY KEY,
    started_at        TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at      TEXT,
    total_transactions INTEGER DEFAULT 0,
    matched           INTEGER DEFAULT 0,
    needs_review      INTEGER DEFAULT 0,
    unmatched         INTEGER DEFAULT 0,
    status            TEXT DEFAULT 'running'
);

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_invoices_customer    ON invoices(customer_id);
CREATE INDEX IF NOT EXISTS idx_invoices_status      ON invoices(status);
CREATE INDEX IF NOT EXISTS idx_txn_status           ON bank_transactions(status);
CREATE INDEX IF NOT EXISTS idx_txn_utr              ON bank_transactions(utr);
CREATE INDEX IF NOT EXISTS idx_alloc_txn            ON payment_allocations(transaction_id);
CREATE INDEX IF NOT EXISTS idx_alloc_invoice        ON payment_allocations(invoice_id);
CREATE INDEX IF NOT EXISTS idx_audit_entity         ON audit_logs(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_review_status        ON review_cases(status);
