"""
ReconcileAI – Rule-based Extraction
Deterministic regex extraction for remittance emails and invoice text.

Used when no LLM is configured (offline / free deployments), and to fill gaps in
LLM output (e.g. a UTR the model missed). Returns the same shapes as
extraction.llm_extractor so callers don't care which path produced the data.
"""
from __future__ import annotations
import re
from typing import Any

_AMOUNT = r"(?:₹|rs\.?|inr)\s*([0-9][0-9,]*(?:\.\d{1,2})?)"
_INV_REF = re.compile(r"\b(?:INV|INVOICE)[\s\-_/#:]*(?:NO\.?\s*)?([A-Z]{0,3}\d{2,8})\b", re.IGNORECASE)
_GSTIN = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b")
_TDS_SECTION = re.compile(r"(?:section|sec\.?|u/s)\s*(19[24][A-Z]{1,2})\b", re.IGNORECASE)
_UTR_LABELLED = re.compile(
    r"(?:utr|transaction\s+ref(?:erence)?|txn\s+ref|ref(?:erence)?\s+no\.?|neft\s+ref|rtgs\s+ref|imps\s+ref)"
    r"\s*(?:no\.?|number|#)?\s*[:\-]?\s*([A-Z0-9]{10,25})", re.IGNORECASE)
_DATE = re.compile(r"\b(\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\b")
_COMPANY_HINT = re.compile(
    r"\b(pvt|private|ltd|limited|llp|corporation|corp|enterprises|solutions|technologies|"
    r"systems|industries|traders|infotech|logistics|consultants|co\.)\b", re.IGNORECASE)


def parse_amount(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        return float(str(text).replace(",", "").strip())
    except ValueError:
        return None


def normalise_invoice_ref(raw: str) -> str:
    """'INV 0012' / 'inv/12' / 'Invoice No. 12' → 'INV-0012' style (prefix + digits)."""
    m = _INV_REF.search(raw)
    if not m:
        return raw.strip().upper()
    return f"INV-{m.group(1).upper()}"


def find_invoice_refs(text: str) -> list[str]:
    seen: list[str] = []
    for m in _INV_REF.finditer(text or ""):
        ref = f"INV-{m.group(1).upper()}"
        if ref not in seen:
            seen.append(ref)
    return seen


def _to_iso(date_str: str) -> str | None:
    try:
        from dateutil import parser as dp
        return dp.parse(date_str, dayfirst=not re.match(r"\d{4}-", date_str)).date().isoformat()
    except Exception:
        return None


def _first_amount_after(pattern: str, text: str) -> float | None:
    m = re.search(pattern + r"[^₹\d]{0,40}?" + _AMOUNT, text, re.IGNORECASE)
    if m:
        return parse_amount(m.group(1))
    m = re.search(pattern + r"[^\d]{0,25}?([0-9][0-9,]*\.\d{2})", text, re.IGNORECASE)
    return parse_amount(m.group(1)) if m else None


# ── Remittance ────────────────────────────────────────────────────────────────

def extract_remittance_rules(text: str) -> dict[str, Any]:
    text = text or ""
    evidence: list[str] = []

    def _ev(m):
        if m:
            evidence.append(m.group(0).strip()[:160])
        return m

    tds_m = _ev(re.search(r"TDS[^₹\d\n]{0,40}?" + _AMOUNT, text, re.IGNORECASE)) or \
            _ev(re.search(_AMOUNT + r"[^\n]{0,30}?\bTDS\b", text, re.IGNORECASE))
    tds = parse_amount(tds_m.group(1)) if tds_m else 0.0

    pay = None
    for pat in (r"(?:transferred|remitted|paid|payment of|amount of|credited)",
                r"total\s+(?:transferred|paid|amount)"):
        m = _ev(re.search(pat + r"[^₹\n]{0,40}?" + _AMOUNT, text, re.IGNORECASE))
        if m:
            pay = parse_amount(m.group(1))
            break
    if pay is None:
        amounts = [parse_amount(a) for a in re.findall(_AMOUNT, text, re.IGNORECASE)]
        amounts = [a for a in amounts if a and a != tds]
        pay = max(amounts) if amounts else None

    utr = None
    m = _ev(_UTR_LABELLED.search(text))
    if m:
        utr = m.group(1).upper()

    sec_m = _ev(_TDS_SECTION.search(text))
    date = None
    dm = re.search(r"(?:on|dated|date[d]?\s*:?)\s*" + _DATE.pattern, text, re.IGNORECASE)
    if dm and not re.search(r"(?:invoice|inv)[^\n]{0,20}dated", dm.group(0), re.IGNORECASE):
        date = _to_iso(dm.group(1))

    gst_m = _GSTIN.search(text)
    return {
        "customer_name": _signature_company(text),
        "customer_gstin": gst_m.group(1) if gst_m else None,
        "invoice_references": find_invoice_refs(text),
        "payment_amount": pay,
        "tds_amount": tds or 0.0,
        "tds_section": sec_m.group(1).upper() if sec_m else None,
        "utr": utr,
        "payment_date": date,
        "currency": "INR",
        "evidence": evidence,
        "extractor": "rules",
    }


def _signature_company(text: str) -> str | None:
    """Best-effort payer name: a company-looking line in the sign-off, else the From domain."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    tail = lines[-6:]
    for line in reversed(tail):
        for part in reversed([p.strip() for p in line.split(",")]):
            if _COMPANY_HINT.search(part) and len(part) <= 80 and "@" not in part:
                return part
    m = re.search(r"^From:.*?@([a-z0-9\-]+)", text, re.IGNORECASE | re.MULTILINE)
    if m:
        dom = re.sub(r"example$", "", m.group(1), flags=re.IGNORECASE)
        return dom.replace("-", " ").title() or None
    return None


# ── Invoice ───────────────────────────────────────────────────────────────────

def _labelled(text: str, labels: str) -> str | None:
    m = re.search(rf"(?:{labels})\s*[:#\-]?\s*([^\n]+)", text, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _labelled_amount(text: str, labels: str) -> float | None:
    # optional rate like "@18%" / "(9%)" between the label and the amount is skipped
    m = re.search(rf"(?:{labels})[^\n\d₹]{{0,30}}(?:@?\s*\(?\d{{1,2}}(?:\.\d+)?\s*%\)?[^\n\d₹]{{0,20}})?"
                  rf"(?:₹|rs\.?|inr)?\s*([0-9][0-9,]*(?:\.\d{{1,2}})?)",
                  text, re.IGNORECASE)
    return parse_amount(m.group(1)) if m else None


def extract_invoice_rules(text: str) -> dict[str, Any]:
    text = text or ""
    inv_no = _labelled(text, r"invoice\s*(?:no\.?|number|#)|inv\s*no\.?|bill\s*no\.?")
    if inv_no:
        inv_no = re.split(r"\s{2,}|\s+(?:date|dated)\b", inv_no, flags=re.IGNORECASE)[0].strip()
    inv_date = _labelled(text, r"invoice\s*date|date\s*of\s*invoice|dated")
    due_date = _labelled(text, r"due\s*date|payment\s*due(?:\s*date)?|due\s*by")
    customer = _labelled(text, r"bill(?:ed)?\s*to|customer\s*name|customer|buyer")
    if customer:
        customer = re.split(r"\s{2,}|gstin", customer, flags=re.IGNORECASE)[0].strip(" :,")
    supplier = _labelled(text, r"supplier|seller|from")

    gstins = _GSTIN.findall(text)
    cust_gstin = None
    if customer:
        after = text[text.lower().find(customer.lower()):] if customer.lower() in text.lower() else text
        m = _GSTIN.search(after)
        cust_gstin = m.group(1) if m else None
    if not cust_gstin and len(gstins) >= 2:
        cust_gstin = gstins[1]

    taxable = _labelled_amount(text, r"taxable\s*(?:amount|value)|sub\s*-?\s*total|base\s*amount")
    igst = _labelled_amount(text, r"\bigst")
    cgst = _labelled_amount(text, r"\bcgst")
    sgst = _labelled_amount(text, r"\bsgst|\butgst")
    gst = None
    if any(v is not None for v in (igst, cgst, sgst)):
        gst = round(sum(v or 0 for v in (igst, cgst, sgst)), 2)
    else:
        gst = _labelled_amount(text, r"\bgst(?:\s*amount)?|tax\s*amount")
    total = _labelled_amount(text, r"grand\s*total|invoice\s*total|total\s*amount(?:\s*payable)?|amount\s*payable|net\s*payable")
    if total is None:
        total = _labelled_amount(text, r"\btotal\b")
    if total is None and taxable is not None and gst is not None:
        total = round(taxable + gst, 2)

    result = {
        "invoice_id": inv_no,
        "supplier_name": supplier,
        "customer_name": customer,
        "customer_gstin": cust_gstin,
        "invoice_date": _to_iso(inv_date) if inv_date else None,
        "due_date": _to_iso(due_date) if due_date else None,
        "taxable_amount": taxable,
        "gst_amount": gst,
        "total_amount": total,
        "currency": "INR",
        "extractor": "rules",
    }
    missing = [k for k in ("invoice_id", "customer_name", "total_amount") if not result.get(k)]
    result["missing_fields"] = missing
    result["extraction_status"] = "partial" if missing else "complete"
    return result


def merge_extractions(primary: dict, fallback: dict) -> dict:
    """Fill empty fields of `primary` (LLM output) from `fallback` (rules)."""
    out = dict(primary)
    for k, v in fallback.items():
        if k in ("extractor", "evidence"):
            continue
        if out.get(k) in (None, "", [], 0, 0.0) and v not in (None, "", []):
            out[k] = v
    if fallback.get("evidence") and not out.get("evidence"):
        out["evidence"] = fallback["evidence"]
    return out
