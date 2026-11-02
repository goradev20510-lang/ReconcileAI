"""
ReconcileAI – Document Classifier
Determines the document type (invoice / remittance / bank_statement / tds_certificate / unknown)
from text or file metadata.  CSV/Excel bank statements are classified by structure, not LLM.
"""
from __future__ import annotations
import re
from pathlib import Path


# ── Keyword heuristics ────────────────────────────────────────────────────────
_BANK_KEYWORDS   = {"narration", "balance", "bank statement", "account statement",
                     "withdrawal", "deposit", "closing balance", "utr"}
_INVOICE_KEYWORDS = {"invoice", "gstin", "taxable amount", "gst", "igst", "cgst", "sgst",
                      "invoice no", "invoice date", "tax invoice", "total amount"}
_REMITTANCE_KEYWORDS = {"remittance", "payment advice", "tds deducted", "we have transferred",
                         "neft transfer", "rtgs transfer", "utr no", "against invoice",
                         "payment reference"}
_TDS_KEYWORDS    = {"tds certificate", "form 16", "form 26q", "tan", "deductor",
                     "deductee", "tax deducted at source", "section 194"}


def classify_document_by_text(text: str) -> dict:
    """
    Rule-based document classification from extracted text.
    Returns {"document_type": str, "confidence": float}.
    """
    lower = text.lower()
    words = set(re.split(r"\W+", lower))

    scores: dict[str, float] = {
        "bank_statement":  _score(lower, words, _BANK_KEYWORDS),
        "invoice":         _score(lower, words, _INVOICE_KEYWORDS),
        "remittance":      _score(lower, words, _REMITTANCE_KEYWORDS),
        "tds_certificate": _score(lower, words, _TDS_KEYWORDS),
    }

    best_type = max(scores, key=scores.get)  # type: ignore[arg-type]
    best_score = scores[best_type]

    if best_score < 0.10:
        return {"document_type": "unknown", "confidence": 0.0}

    # Normalise to a rough 0–1 confidence
    total = sum(scores.values()) or 1
    confidence = round(scores[best_type] / total, 2)

    return {"document_type": best_type, "confidence": confidence}


def classify_by_file_type(filepath: str | Path) -> dict | None:
    """
    Classify CSV / Excel files by extension and headers without LLM.
    Returns a result dict or None if classification cannot be determined.
    """
    p = Path(filepath)
    ext = p.suffix.lower()

    if ext == ".eml":
        return {"document_type": "remittance", "confidence": 0.95}
    if ext == ".txt":
        return None  # Need to read content

    if ext in {".csv", ".xlsx", ".xls"}:
        try:
            return _classify_tabular(p, ext)
        except Exception:
            return None

    if ext == ".pdf":
        return None  # Need text extraction first

    return {"document_type": "unknown", "confidence": 0.0}


def _classify_tabular(p: Path, ext: str) -> dict:
    import pandas as pd
    if ext == ".csv":
        df = pd.read_csv(str(p), nrows=3)
    else:
        df = pd.read_excel(str(p), nrows=3)

    cols_lower = " ".join(str(c).lower() for c in df.columns)
    bank_signals = sum(1 for kw in ["narration", "balance", "credit", "debit", "utr"] if kw in cols_lower)
    inv_signals  = sum(1 for kw in ["invoice", "gstin", "taxable", "gst"] if kw in cols_lower)

    if bank_signals >= 2:
        return {"document_type": "bank_statement", "confidence": 0.90}
    if inv_signals >= 2:
        return {"document_type": "invoice", "confidence": 0.85}
    return {"document_type": "unknown", "confidence": 0.0}


def _score(lower_text: str, words: set, keywords: set) -> float:
    """Count how many keywords appear in the text (full phrases and single words)."""
    hits = 0
    for kw in keywords:
        if " " in kw:  # multi-word phrase
            if kw in lower_text:
                hits += 1
        else:
            if kw in words:
                hits += 1
    return hits / len(keywords)
