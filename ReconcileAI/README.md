# ReconcileAI

**Accounts-receivable reconciliation for Indian businesses** — Python, Streamlit, SQLite and (optionally) an LLM via OpenRouter, OpenAI or Gemini.

Upload bank statements, invoices and payment emails, click **Run Reconciliation**, and get every bank credit matched to its invoice(s) — or routed to a human with the evidence and a proposed allocation. Partial payments, TDS deductions, bulk payments, overpayments and duplicate credits are all handled, and every decision is audited.

**Design principle:** *the LLM reads documents; Python decides about money.* Every amount, match and status is computed deterministically. With no API key the whole app still works: rule-based extractors replace the LLM.

→ Architecture, design trade-offs and weakest points: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

---

## Quick start

```bash
cd ReconcileAI
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # optional: add an OpenRouter / OpenAI / Gemini key
streamlit run app.py
```

Open http://localhost:8501, use **Load demo dataset**, then **Run reconciliation**.

Or with Docker: `docker build -t reconcileai . && docker run -p 8501:8501 --env-file .env reconcileai`

---

## The app

| Page | What it does |
|---|---|
| **Overview** | Receipts, matched value, outstanding receivables, review backlog, TDS awaiting certificates. |
| **Review queue** | Uncertain credits, largest first and filterable by reason, beside a detail panel: the bank line next to the suggested invoice, the computed shortfall or excess, exactly what approving will post, and the remittance excerpt it rests on. Actions: approve, reject, apply to one or several invoices (with TDS), mark as not a receivable, or confirm/release a duplicate. A named reviewer is required and anything other than approving needs a note. |
| **Bank credits** | Every credit, what it was allocated to, and why. Reconcile one line now, or reverse a match. |
| **Import** | Bank statements (CSV/Excel), invoices (PDF, CSV or Excel register), remittance emails (.eml/.txt/.pdf or pasted text). The same file is never imported twice. |
| **Reports** | Summary (with an optional model-drafted management note, labelled as such), receivables ageing (not due / 1–30 / 31–60 / 61–90 / 90+, by bucket and by customer), customer balances, TDS register (verify with Form 16A or reject), invoices, Excel export. |
| **Audit trail** | Every import, decision and human action, with its evidence. |
| **Ask the data** | Plain-language questions ("what's overdue?", "status of INV-005", "how much does Apex owe?"). Python fetches the data; the LLM, if configured, only rephrases it. |

### UI design

The interface follows the "Invoice & Billing Tool" profile from the UI/UX Pro Max skill (Swiss minimalism with a data-dense dashboard). Navy is the primary colour; green, amber and red are reserved for status and always appear with a text label. Type is IBM Plex Sans and Mono. The default theme is light, with a matching dark theme that follows the OS. The theme lives in [`.streamlit/config.toml`](.streamlit/config.toml); `app.py` has only a few lines of CSS for the hero figure and the value-split bar. Money uses Indian digit grouping (₹1,06,200.00), and timestamps are shown in IST.

Text that may come from the model is labelled as such: the management summary, assistant answers, and match explanations. Match explanations are deterministic and evidence-only by default; they come from the model only when `LLM_MATCH_EXPLANATIONS=1`.

---

## How a bank credit is decided

1. **Excluded?** Debits and zero lines are kept for completeness but never matched.
2. **Duplicate?** Same UTR as an earlier credit, or (when a UTR is missing) the same amount with a near-identical narration within 30 days. Two different UTRs are never duplicates, so split payments are safe. Flagged copies go to review, where they can be confirmed or released.
3. **Remittance advice:** found by UTR, or by a unique amount + date match when the email quoted no UTR. It contributes invoice references, the payer, the TDS amount and the TDS section.
4. **Candidates:** invoice references parsed from the narration (NEFT/RTGS/IMPS/UPI, slash, hyphen and UTR-first layouts) or the remittance; customers matched by GSTIN or fuzzy name; and invoices whose outstanding balance equals payment + TDS. Invoices a reviewer rejected for this credit are excluded.
5. **Score:** a weighted sum of six signals (see below).
6. **Decide:**
   - **Auto-match** when the score clears the auto threshold *and* the amount validates (within ±0.5 %), and the top two candidates aren't within the ambiguity margin. A part-payment with an exact invoice reference may also auto-apply, but never when TDS is involved.
   - **Bulk payment:** if 2–4 of the customer's (or the referenced) invoices add up to the payment, propose a split across them.
   - **Review**, with a proposed allocation, for: close candidates, TDS (declared, or inferred from a short payment equal to 2/5/10 % of the taxable value or total), part and over-payments, already-settled invoices, low confidence.
   - **Unmatched** otherwise, with an open case so a human can allocate or dismiss it.

Invoice balances are always recomputed from approved allocations (settled, partially paid, overpaid); they are never edited directly. Re-importing an invoice never resets its balance.

### TDS (Indian tax deducted at source)

The rate is validated for sections 194C (2 %), 194J/194I/194A (10 %) and 194H (5 %), on either the taxable value or the invoice total (CBDT Circular 23/2017). A TDS claim stays **evidence pending** until a reviewer verifies it against Form 16A / 26AS. A rejected claim re-opens the invoice for the deducted amount.

---

## Matching parameters & training

| Signal | Default weight | Learned weight |
|---|---|---|
| Invoice reference exact | 0.50 | 0.306 |
| Customer exact (100 % name / GSTIN) | 0.15 | 0.072 |
| Customer fuzzy (× similarity) | 0.10 | 0.058 |
| Amount exact (payment + TDS = outstanding) | 0.15 | 0.306 |
| Amount within ±0.5 % | 0.05 | 0.255 |
| Date within 7 / 30 / 90 days of due date | 0.05 | 0.003 |
| **Auto-match threshold** | 0.80 | **0.464** |
| **Review threshold** | 0.50 | **0.083** |
| **Ambiguity margin** (top-2 gap) | 0.10 | **0.144** |
| Customer fuzzy minimum | 75 | 60 |

The learned values live in [`config/learned_params.json`](config/learned_params.json) and are used automatically. Set `RECONCILE_USE_LEARNED_PARAMS=0` to use the hand-set defaults in [`config/thresholds.py`](config/thresholds.py). Under the learned values, an auto-match needs an exact invoice reference **and** a matching amount. A name + amount match alone always goes to a human.

```bash
python -m data.synthetic.generate_training_data          # ≈11.6k labelled credits, 22 scenarios (~3 s)
python -m training.train_matcher                         # fit weights + thresholds (~30 s)
python -m training.train_matcher --dry-run               # evaluate only
```

The trainer runs the real candidate generator and validation on every transaction. It replays the decision engine's gates in numpy (no DB, no LLM; a test asserts the replay agrees with the engine) and searches for the parameters that minimise a business cost:

| Outcome | Cost |
|---|---|
| Money auto-posted to the wrong invoice | 10 |
| Evidence existed but the credit was left "unmatched" | 4 |
| Auto-posted something policy says a human must check | 3 |
| Avoidable manual review | 1 |
| Review with a wrong suggestion / review of a non-receivable | 0.5 |

Splits are by batch (70/15/15). Results on the held-out test split (see the evaluation section for the per-scenario breakdown):

| | Hand-set defaults | Learned |
|---|---|---|
| Mean cost | 0.882 | 0.062 |
| Outcome correct | 58.8 % | 94.8 % |
| Auto-matched | 20.7 % | 44.3 % |
| Wrong auto-matches | 0 | 0 |
| Evidence-backed credits left unmatched | 264 | 9 |

---

## Evaluation: examples tested and where it fails

**All data is synthetic, made up for this prototype.** No real client or bank data is used. Customer names, GSTINs, UTRs, invoices and emails are generated by [`data/synthetic/generate_dataset.py`](data/synthetic/generate_dataset.py) (demo) and [`generate_training_data.py`](data/synthetic/generate_training_data.py) (training/evaluation). GSTIN and UTR formats are realistic, but the numbers are random.

**Jurisdiction:** India. That means INR, GST invoices, NEFT/RTGS/IMPS/UPI narrations, and TDS under sections 194C/J/H/I/A. Another country would need changes to the TDS rate table and certificate workflow (withholding tax rules), the narration parser (SEPA/ACH/BACS reference formats), the tax-ID matcher (VAT numbers instead of GSTIN), and multi-currency support (only INR today).

**Model:** `google/gemini-2.5-flash` via OpenRouter, used for reading PDFs, emails and narrations. All decisions are made in Python. The offline test suite and CI run with the LLM switched off, so they test the rule-based path.

### 1. Demo dataset: 30 hand-designed cases (`python -m scripts.smoke_reconcile`)

| # | Scenario | Expected | Result |
|---|---|---|---|
| 1 | Exact invoice reference + exact amount | matched | ✅ matched |
| 2 | Customer name variant, no invoice reference | review | ✅ review (name-only payments always need a human) |
| 3 | Payment without invoice number | review | ✅ review |
| 4 | Partial payment (60 %) | review | ✅ review |
| 5 | TDS deducted (10 %, 194J, remittance email) | review | ✅ review, TDS ₹ and section captured, *evidence pending* |
| 6 | One payment for two invoices | review | ✅ review, split proposed across both invoices |
| 7–8 | Two part-payments of one invoice | review | ✅ review (not mistaken for duplicates) |
| 9–10 | Same UTR twice | matched + duplicate | ✅ original matched, copy flagged |
| 11 | Unknown payer | unmatched | ✅ unmatched (dismissable as non-receivable) |
| 12 | Two invoices with the same amount | review | ✅ review |
| 13 | Amount short by ₹5,000 | review | ✅ review |
| 14 | No remittance, generic narration | review | ✅ review |
| 15 | Wrong invoice number in narration | review | ✅ review |
| 16 | Overpayment (+₹10,000) | review | ✅ review; approving marks the invoice *overpaid* |
| 17–30 | Clean payments | matched | ✅ matched |

**30 / 30**, offline and with the LLM enabled. The generator's scenario 15, re-uploading the same file, is covered by the test suite (`tests/test_pipeline.py`), along with CSV/PDF/.eml ingestion, every review action and the database migration.

### 2. Generated test split: 1,714 credits across 22 scenarios, never seen in training

0 wrong auto-matches. 94.8 % of outcomes are what a reviewer would want. When a credit has a true invoice, the top suggestion is that invoice 97.9 % of the time.

**Where it fails, honestly:**

| Scenario | n | What happens | Why |
|---|---|---|---|
| Messy reference formats (`INV 0012` works; `0012`, `BILL 0012`, `INV12` don't) | 113 | 62 % auto-matched, **38 % sent to review** instead | The offline regex only trusts `INV` + 3–6 digits. The LLM narration hint recovers some of these when a key is set; this was not measured at scale. |
| Bulk payment, no remittance advice | 43 | 79 % review, **21 % left unmatched**; the right split was found 53 % of the time | When the bank truncates the payer to one word, no customer clears the fuzzy floor, so there are no candidates to combine. |
| Unknown payer | 48 | 35 % go to review instead of unmatched | Coincidental fuzzy name hits. Harmless (a human dismisses them), but it adds noise to the queue. |
| Unknown payer whose amount equals an open invoice | 19 | 100 % review | By design: it could be a third party paying for a customer. |
| Non-receivable credits (interest, refunds) | 21 | 5 % review | Same cause as unknown payer. |

**Known limits not covered by the data:**
- Real bank narrations vary more than the generator does.
- OCR on scanned invoice PDFs has not been tested on real scans.
- TDS rates are a static table, so a rate change needs a code/config update and there is no 26AS reconciliation.
- Only INR is supported.
- The LLM paths were verified live on a handful of examples only: 3 extraction calls and the 30-case demo.

---

## Configuration

See [`.env.example`](.env.example). The key settings:

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `openrouter` | `openrouter`, `openai` or `gemini` |
| `OPENROUTER_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY` | – | Leave empty to run offline (rule-based extraction) |
| `LLM_MODEL` | `google/gemini-2.5-flash` | Model id for the provider |
| `LLM_ENABLED` | `1` | Master switch for all LLM calls |
| `LLM_NARRATION_HINTS` | `1` | LLM reads narrations that have no invoice reference |
| `LLM_MATCH_EXPLANATIONS` | `0` | LLM-phrased explanations (default: deterministic, evidence-only) |
| `DATABASE_PATH` | `database/reconcile.db` | SQLite file. Older databases are migrated in place on start-up. |

---

## Project structure

```
ReconcileAI/
├── app.py                         # Streamlit UI
├── config/
│   ├── settings.py                # env-driven settings, LLM switches
│   ├── thresholds.py              # hand-set defaults + learned-parameter loader
│   └── learned_params.json        # output of training/train_matcher.py
├── database/
│   ├── schema.sql · db.py         # schema, connection, in-place migrations
│   └── repositories.py            # all SQL lives here
├── extraction/
│   ├── bank_parser.py             # CSV / Excel statements (header detection, UTR from narration)
│   ├── invoice_parser.py          # PDF text (+ OCR for scans), CSV / Excel registers
│   ├── email_parser.py            # .eml bodies
│   ├── rule_extractor.py          # regex extraction of invoices & remittances (offline fallback)
│   ├── llm_extractor.py           # LLM calls (invoice, remittance, narration, summaries)
│   ├── document_classifier.py     # bank statement / invoice / remittance detection
│   └── ingestion.py               # validation, dedup, routing, demo loader
├── matching/                      # narration parsing, exact & fuzzy matching, scoring
├── validation/                    # payment & TDS rules, duplicates, settlement ledger
├── decision/
│   ├── decision_engine.py         # evaluate_transaction() / reconcile_transaction() / batch
│   └── review_manager.py          # approve, reject, correct, reverse, dismiss, duplicates, TDS
├── reporting/                     # aging, customer balances, TDS register, Excel, finance assistant
├── training/train_matcher.py      # parameter calibration
├── data/synthetic/                # demo dataset + labelled training-data generator
├── scripts/                       # smoke_reconcile.py (demo vs ground truth), check_llm.py
└── tests/                         # pytest suite (offline, throw-away databases)
```

## Tests

```bash
python -m pytest tests -q              # offline; each test gets its own temporary database
python -m scripts.smoke_reconcile      # demo dataset vs ground truth (30/30)
python scripts/check_llm.py            # live check of the configured LLM provider (uses your key)
```

CI (`.github/workflows/ci.yml`) runs both on every push.

## Deploying on Render

`render.yaml` deploys the Docker image. Set `OPENROUTER_API_KEY` in the dashboard, or leave it empty for offline mode. The free plan has no persistent disk, so the database starts empty after each deploy. Attach a disk and point `DATABASE_PATH` at it to keep data.

## License

MIT
