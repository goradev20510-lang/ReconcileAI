# ReconcileAI

**AI-powered accounts receivable reconciliation** — built with Python, Streamlit, and OpenRouter (Gemini 2.5 Flash).

A finance executive can upload bank statements, invoices, and payment emails, click **Reconcile**, and receive a full report showing which payments match which invoices, partial payments, TDS deductions, and which transactions need human review.

---

## Features

- **Intelligent Matching** — Exact invoice reference matching, fuzzy customer name matching (RapidFuzz), amount + TDS reconciliation
- **AI Extraction** — LLM (Gemini 2.5 Flash via OpenRouter) reads unstructured invoices, remittance emails, and bank narrations
- **Indian Tax Support** — TDS deduction validation (Sections 194C, 194J, 194H, 194I, 194A)
- **Human Review Queue** — Uncertain matches routed to finance team with AI-generated explanation
- **Audit Trail** — Every decision logged with signals, scores, and reasoning
- **Reports & Export** — Excel workbook export, AI-generated summary, Finance Assistant chat

---

## Architecture

```
LLM (OpenRouter)          →  Language understanding only
Python (deterministic)    →  All financial decisions
SQLite                    →  Persistence
Streamlit                 →  UI
```

**Design principle:** *The LLM understands documents. Python controls money.*

---

## Quick Start

### 1. Clone & install
```bash
git clone https://github.com/goradev20510-lang/ReconcileAI.git
cd ReconcileAI
pip install -r requirements.txt
```

### 2. Configure API key
```bash
cp .env.example .env
# Edit .env and set your OpenRouter API key
```

Get a free API key at [openrouter.ai](https://openrouter.ai)

### 3. Run
```bash
python -m streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501)

### 4. Load demo data
Click **"Load Demo Dataset"** in the sidebar, then **"Run Reconciliation"**.

---

## Project Structure

```
ReconcileAI/
├── app.py                      # Streamlit UI
├── config/
│   ├── settings.py             # LLM provider config
│   └── thresholds.py           # All matching thresholds (centralised)
├── database/
│   ├── schema.sql              # SQLite schema
│   ├── db.py                   # Thread-safe connection manager
│   └── repositories.py         # All DB operations
├── extraction/
│   ├── bank_parser.py          # Parse bank statement CSV/PDF
│   ├── invoice_parser.py       # Parse invoice PDF/CSV
│   ├── email_parser.py         # Parse remittance emails
│   ├── document_classifier.py  # Classify uploaded documents
│   ├── llm_extractor.py        # OpenRouter LLM calls
│   └── ingestion.py            # Orchestrate document ingestion
├── matching/
│   ├── exact_match.py          # Invoice reference exact lookup
│   ├── fuzzy_match.py          # RapidFuzz customer matching
│   ├── candidate_generator.py  # Build candidate invoice list
│   └── scoring.py              # Confidence score calculation
├── validation/
│   ├── financial_rules.py      # Payment/TDS validation rules
│   └── duplicate_detection.py  # Duplicate UTR/narration detection
├── decision/
│   └── decision_engine.py      # Final reconciliation decisions
├── reporting/
│   ├── dashboard.py            # KPI metrics
│   ├── audit_trail.py          # Audit log formatting
│   ├── excel_export.py         # Excel workbook export
│   └── nl_query.py             # Natural language query router
├── data/
│   └── synthetic/
│       └── generate_dataset.py # Synthetic demo dataset generator
└── prompts/                    # LLM prompt templates
```

---

## Configuration

All numeric thresholds are in [`config/thresholds.py`](config/thresholds.py):

| Parameter | Default | Description |
|-----------|---------|-------------|
| `AUTO_MATCH_THRESHOLD` | 0.80 | Score above which matches are auto-approved |
| `REVIEW_THRESHOLD` | 0.50 | Score below which cases go to human review |
| `CUSTOMER_NAME_FUZZY_MIN` | 75 | Minimum fuzzy name match score (0–100) |
| `AMOUNT_TOLERANCE_PERCENT` | 0.5% | Allowed rounding difference |

---

## Scoring Logic

Each candidate invoice is scored 0.0–1.0:

| Signal | Weight | Triggered when |
|--------|--------|----------------|
| Invoice ref exact | 0.50 | Narration/remittance contains `INV-xxx` matching the invoice |
| Customer exact | 0.15 | Customer name/GSTIN is exact or 100% fuzzy match |
| Customer fuzzy | 0.10 | Fuzzy name match above threshold |
| Amount exact | 0.15 | Payment + TDS = invoice total exactly |
| Amount within tolerance | 0.05 | Within ±0.5% |
| Date proximity | 0.05 | Transaction within 30 days of due date |

**Score ≥ 0.80** → Auto-matched | **0.50–0.79** → Human review | **< 0.50** → Unmatched

---

## License

MIT
