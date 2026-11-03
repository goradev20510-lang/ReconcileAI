"""
ReconcileAI – LLM Extractor
Calls the configured LLM provider with structured prompts and returns validated JSON.
The LLM is used ONLY for language understanding; all financial decisions happen in Python.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Any

from config.settings import (
    LLM_PROVIDER, LLM_MODEL, LLM_ENABLED, LLM_TIMEOUT_SECONDS,
    OPENAI_API_KEY, OPENROUTER_API_KEY, OPENROUTER_BASE_URL, GEMINI_API_KEY,
)

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


class LLMUnavailable(RuntimeError):
    """Raised when no LLM provider is configured (callers fall back to rules)."""


def llm_available() -> bool:
    """True when the LLM is enabled and the configured provider has an API key."""
    if not LLM_ENABLED:
        return False
    key = {"openrouter": OPENROUTER_API_KEY, "gemini": GEMINI_API_KEY}.get(LLM_PROVIDER, OPENAI_API_KEY)
    return bool(key)


def llm_status() -> str:
    if not LLM_ENABLED:
        return "disabled (LLM_ENABLED=0) — rule-based extraction"
    if not llm_available():
        return f"no API key for '{LLM_PROVIDER}' — rule-based extraction"
    return f"{LLM_PROVIDER} · {LLM_MODEL}"


# ── Low-level LLM call ────────────────────────────────────────────────────────

def _call_llm(system_prompt: str, user_content: str, temperature: float = 0.0) -> str:
    """Send a request to the configured provider and return the raw text response."""
    if not llm_available():
        raise LLMUnavailable(llm_status())
    if LLM_PROVIDER == "openrouter":
        return _call_openai_compat(
            base_url=OPENROUTER_BASE_URL,
            api_key=OPENROUTER_API_KEY,
            model=LLM_MODEL,
            system=system_prompt,
            user=user_content,
            temperature=temperature,
        )
    elif LLM_PROVIDER == "gemini":
        return _call_gemini(system_prompt, user_content)
    else:  # default: openai
        return _call_openai_compat(
            base_url="https://api.openai.com/v1",
            api_key=OPENAI_API_KEY,
            model=LLM_MODEL,
            system=system_prompt,
            user=user_content,
            temperature=temperature,
        )


def _call_openai_compat(base_url: str, api_key: str | None,
                         model: str, system: str, user: str, temperature: float) -> str:
    if not api_key:
        raise LLMUnavailable("No API key configured. Set your key in .env and restart.")
    try:
        import httpx
        from openai import OpenAI

        # OpenRouter requires these headers to identify the app
        extra_headers = {}
        if "openrouter.ai" in base_url:
            extra_headers = {
                "HTTP-Referer": "https://github.com/ReconcileAI",
                "X-Title": "ReconcileAI",
            }

        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers=extra_headers,
            timeout=LLM_TIMEOUT_SECONDS,
        )
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user",   "content": user}],
            temperature=temperature,
            max_tokens=1024,
        )
        return resp.choices[0].message.content or ""
    except ImportError:
        raise RuntimeError(
            "openai package not installed correctly. Run: pip install 'openai>=1.54.0'"
        )


def _call_gemini(system_prompt: str, user_content: str) -> str:
    """Call Google Gemini via the openai-compatible REST API."""
    import httpx
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY or ""}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{LLM_MODEL}:generateContent"
    body = {
        "contents": [{"parts": [{"text": f"{system_prompt}\n\n{user_content}"}]}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 1024},
    }
    r = httpx.post(url, json=body, headers=headers, timeout=LLM_TIMEOUT_SECONDS)
    r.raise_for_status()
    data = r.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


# ── JSON extraction helper ────────────────────────────────────────────────────

def _extract_json(raw: str) -> dict:
    """Strip markdown code fences and parse JSON from LLM response."""
    # Remove ```json ... ``` wrappers
    cleaned = re.sub(r"```(?:json)?", "", raw, flags=re.IGNORECASE).replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to find the first {...} block
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            return json.loads(m.group())
        raise ValueError(f"Could not parse JSON from LLM response: {raw[:200]}")


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


# ── Public extraction functions ───────────────────────────────────────────────

def extract_invoice_data(document_text: str) -> dict[str, Any]:
    """
    Extract structured invoice fields from raw document text.
    Returns a dict matching the InvoiceExtraction schema.
    """
    system = _load_prompt("invoice_extraction.txt")
    raw    = _call_llm(system, document_text)
    result = _extract_json(raw)

    # Basic validation: amounts must be numeric
    for field in ("taxable_amount", "gst_amount", "total_amount"):
        if field in result and result[field] is not None:
            try:
                result[field] = float(result[field])
            except (ValueError, TypeError):
                result[field] = None

    missing = [k for k, v in result.items()
               if v is None and k in ("invoice_id", "customer_name", "total_amount")]
    result["missing_fields"] = missing
    result["extraction_status"] = "partial" if missing else result.get("extraction_status", "complete")
    return result


def extract_remittance_data(email_text: str) -> dict[str, Any]:
    """
    Extract payment details from a remittance email or message.
    Returns a dict matching the RemittanceExtraction schema.
    """
    system = _load_prompt("remittance_extraction.txt")
    raw    = _call_llm(system, email_text)
    result = _extract_json(raw)

    for field in ("payment_amount", "tds_amount"):
        if field in result and result[field] is not None:
            try:
                result[field] = float(result[field])
            except (ValueError, TypeError):
                result[field] = 0.0 if field == "tds_amount" else None

    result["tds_amount"] = result.get("tds_amount") or 0.0
    refs = result.get("invoice_references") or []
    result["invoice_references"] = [refs] if isinstance(refs, str) else list(refs)
    sec = (result.get("tds_section") or "").upper().replace("SECTION", "").strip() or None
    result["tds_section"] = sec
    result.setdefault("evidence", [])
    return result


def interpret_bank_narration(narration: str, amount: float,
                              date: str | None = None) -> dict[str, Any]:
    """
    Use LLM to extract clues from a bank transaction narration.
    Returns candidates — not verified facts.
    """
    system = _load_prompt("narration_interpretation.txt")
    user   = f"Narration: {narration}\nAmount: ₹{amount:,.2f}\nDate: {date or 'unknown'}"
    raw    = _call_llm(system, user)
    result = _extract_json(raw)
    result.setdefault("ambiguities", [])
    # Map the prompt's field names onto the keys the candidate generator reads.
    ref = result.get("invoice_reference_candidate")
    result["invoice_references"] = [ref] if isinstance(ref, str) and ref.strip() else []
    result["likely_payer"] = result.get("payer_name_candidate")
    result.setdefault("tds_amount", 0.0)
    return result


def generate_match_explanation(evidence: dict[str, Any]) -> str:
    """
    Generate a human-readable explanation for a proposed match.
    All facts must come from the evidence dict — the LLM must not invent supporting details.
    """
    system = (
        "You are an accounts-receivable reconciliation assistant. "
        "Generate a concise, professional explanation (2–4 sentences) of why the system "
        "proposed this payment-to-invoice match. Base your explanation ONLY on the evidence "
        "provided. Do not invent or embellish facts."
    )
    user = f"Matching evidence:\n{json.dumps(evidence, indent=2)}"
    return _call_llm(system, user)


def classify_exception(exception_data: dict[str, Any]) -> dict[str, Any]:
    """
    Use LLM to classify and summarise a reconciliation exception.
    Deterministic checks (amount discrepancy, duplicate) must already be done by Python.
    """
    system = (
        "You are a financial exception analyst. Given the exception data, "
        "return JSON with:\n"
        "- exception_type: one of [missing_invoice_reference, amount_discrepancy, "
        "possible_tds_deduction, multiple_candidate_invoices, unknown_customer, "
        "possible_duplicate, missing_remittance_advice, conflicting_evidence]\n"
        "- exception_summary: one sentence explanation\n"
        "- missing_evidence: list of evidence items needed to resolve\n"
        "Return JSON only."
    )
    user = json.dumps(exception_data, indent=2)
    raw  = _call_llm(system, user)
    return _extract_json(raw)


def generate_reconciliation_report_summary(stats: dict[str, Any]) -> str:
    """
    Generate a management-level summary paragraph.
    All numbers must come from the stats dict passed in.
    """
    system = (
        "You are a financial reporting assistant. Write a concise management summary "
        "(3–5 sentences) of the reconciliation results. Use ONLY the statistics provided. "
        "Do not calculate or invent numbers."
    )
    user = f"Reconciliation statistics:\n{json.dumps(stats, indent=2)}"
    return _call_llm(system, user)


def answer_finance_query(query: str, backend_result: dict[str, Any]) -> str:
    """
    Convert structured backend query results into a natural-language answer.
    The LLM explains the data — it does not alter it.
    """
    system = (
        "You are a finance assistant answering questions about accounts-receivable data. "
        "Explain the data returned by the backend in clear, professional language. "
        "Do not make financial changes. Do not invent data."
    )
    user = f"User question: {query}\n\nBackend data:\n{json.dumps(backend_result, indent=2, default=str)}"
    return _call_llm(system, user)
