"""
ReconcileAI – Bank Statement Parser
Reads CSV or Excel bank statements and returns a list of normalised transaction dicts.
"""
from __future__ import annotations
import hashlib
import re
from pathlib import Path

import pandas as pd

# Possible column name aliases (case-insensitive)
_COL_MAP = {
    "utr":         ["utr", "ref no", "reference", "transaction ref", "txn ref", "chq/ref no",
                    "reference number", "transaction id", "utr/ref"],
    "narration":   ["narration", "description", "particulars", "remarks", "transaction details",
                    "transaction narration", "details"],
    "credit":      ["credit", "credit amount", "cr amount", "deposit", "deposits", "inward",
                    "cr", "credit (inr)", "received"],
    "debit":       ["debit", "debit amount", "dr amount", "withdrawal", "withdrawals",
                    "outward", "dr", "debit (inr)"],
    "date":        ["date", "transaction date", "value date", "txn date", "posting date"],
    "balance":     ["balance", "closing balance", "running balance"],
}


def _resolve_columns(df: pd.DataFrame) -> dict[str, str | None]:
    """Return a mapping from canonical name to actual column in df."""
    lower_cols = {c.strip().lower(): c for c in df.columns}
    result: dict[str, str | None] = {}
    for canonical, aliases in _COL_MAP.items():
        for alias in aliases:
            if alias in lower_cols:
                result[canonical] = lower_cols[alias]
                break
        else:
            result[canonical] = None
    return result


def _clean_amount(val) -> float:
    """Convert a cell value to float, stripping commas/currency symbols."""
    if pd.isna(val) or val == "":
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    val = str(val).replace(",", "").replace("₹", "").replace("INR", "").strip()
    try:
        return float(val)
    except ValueError:
        return 0.0


def read_bank_csv(filepath: str | Path, bank_name: str = "Unknown",
                  account_number: str | None = None) -> list[dict]:
    """Parse a bank statement CSV file into transaction dicts."""
    df = pd.read_csv(str(filepath), skiprows=_detect_header_row(filepath, ".csv"))
    return _parse_bank_df(df, bank_name, account_number)


def read_bank_excel(filepath: str | Path, bank_name: str = "Unknown",
                    account_number: str | None = None) -> list[dict]:
    """Parse a bank statement Excel file into transaction dicts."""
    # Try each sheet until we find one with financial headers
    xf = pd.ExcelFile(str(filepath))
    for sheet in xf.sheet_names:
        df = xf.parse(sheet)
        col_map = _resolve_columns(df)
        if col_map["date"] or col_map["credit"] or col_map["narration"]:
            return _parse_bank_df(df, bank_name, account_number)
    return []


def _detect_header_row(filepath: str | Path, ext: str) -> int:
    """Skip rows that are bank metadata/headers, return row index of actual header."""
    with open(str(filepath), "r", encoding="utf-8", errors="ignore") as f:
        for i, line in enumerate(f):
            lower = line.lower()
            if any(kw in lower for kw in ["date", "narration", "amount", "credit", "debit"]):
                return i
    return 0


def _parse_bank_df(df: pd.DataFrame, bank_name: str, account_number: str | None) -> list[dict]:
    df = df.dropna(how="all")
    col = _resolve_columns(df)
    transactions = []
    for _, row in df.iterrows():
        credit = _clean_amount(row.get(col["credit"]) if col["credit"] else 0)
        debit  = _clean_amount(row.get(col["debit"])  if col["debit"]  else 0)
        if credit == 0 and debit == 0:
            continue
        narration = str(row.get(col["narration"], "")).strip() if col["narration"] else ""
        date_raw  = str(row.get(col["date"], "")).strip() if col["date"] else ""
        utr_raw   = str(row.get(col["utr"], "")).strip() if col["utr"] else ""

        # Extract UTR from narration if not in its own column
        if not utr_raw or utr_raw.lower() in ("nan", ""):
            utr_raw = _extract_utr_from_narration(narration)

        # Generate stable transaction hash for dedup
        raw_sig = f"{date_raw}|{narration}|{credit}|{debit}".encode()
        txn_hash = hashlib.md5(raw_sig).hexdigest()[:12].upper()

        transactions.append({
            "transaction_id": f"TXN{txn_hash}",
            "utr": utr_raw if utr_raw and utr_raw.lower() != "nan" else None,
            "narration": narration,
            "credit_amount": credit,
            "debit_amount": debit,
            "transaction_date": _normalise_date_str(date_raw),
            "bank_name": bank_name,
            "account_number": account_number,
            "currency": "INR",
            "status": "unmatched",
        })
    return transactions


def _extract_utr_from_narration(narration: str) -> str | None:
    """Try to pull a UTR/reference number from a narration string."""
    patterns = [
        r"\b([A-Z]{4}\d{10,22})\b",      # NEFT/RTGS UTR format
        r"\b(\d{12,22})\b",               # Pure numeric ref
    ]
    for pat in patterns:
        m = re.search(pat, narration.upper())
        if m:
            return m.group(1)
    return None


def _normalise_date_str(date_str: str) -> str | None:
    """Try to parse common date formats and return ISO YYYY-MM-DD."""
    from dateutil import parser as dp
    if not date_str or date_str.lower() in ("nan", "none", ""):
        return None
    try:
        return dp.parse(date_str, dayfirst=True).date().isoformat()
    except Exception:
        return None
