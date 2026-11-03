"""
ReconcileAI – Email Parser
Handles .eml files and plain-text email content.
Structured extraction is delegated to llm_extractor.py.
"""
from __future__ import annotations
import email
from pathlib import Path


def parse_eml_file(filepath: str | Path) -> str:
    """Extract the plain-text body from an .eml file."""
    with open(str(filepath), "rb") as f:
        msg = email.message_from_bytes(f.read())

    parts = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    parts.append(payload.decode("utf-8", errors="ignore"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            parts.append(payload.decode("utf-8", errors="ignore"))

    # Also include subject as context
    subject = msg.get("subject", "")
    if subject:
        parts.insert(0, f"Subject: {subject}")

    return "\n".join(parts)


def parse_text_file(filepath: str | Path) -> str:
    """Read a plain .txt email file."""
    return Path(filepath).read_text(encoding="utf-8", errors="ignore")


def parse_eml_bytes(raw: bytes) -> str:
    """Extract subject, sender and plain-text body from raw .eml bytes (falls back to HTML text)."""
    import re
    from email import policy
    msg = email.message_from_bytes(raw, policy=policy.default)
    header = []
    for h in ("From", "To", "Subject", "Date"):
        if msg.get(h):
            header.append(f"{h}: {msg.get(h)}")
    body = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None:
        text = body.get_content()
        if body.get_content_type() == "text/html":
            text = re.sub(r"<br\s*/?>|</p>", "\n", text, flags=re.IGNORECASE)
            text = re.sub(r"<[^>]+>", " ", text)
    return "\n".join(header) + "\n\n" + text.strip()
