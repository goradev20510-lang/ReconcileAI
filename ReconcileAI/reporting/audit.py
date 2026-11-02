"""
ReconcileAI – Audit Trail Module
Provides formatted audit history views for the UI.
"""
from __future__ import annotations
import json
from database.repositories import get_audit_logs, create_audit_log


def get_formatted_audit_trail(entity_id: str | None = None, limit: int = 200) -> list[dict]:
    """Return audit logs with formatted details for display."""
    logs = get_audit_logs(entity_id=entity_id, limit=limit)
    formatted = []
    for log in logs:
        details = log.get("details", "{}")
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except Exception:
                details = {"raw": details}
        formatted.append({
            **log,
            "details_parsed": details,
            "details_summary": _summarise_details(details),
        })
    return formatted


def _summarise_details(details: dict) -> str:
    """Create a one-line readable summary of audit details."""
    if not details:
        return "—"
    parts = []
    for k, v in list(details.items())[:3]:
        parts.append(f"{k}: {v}")
    return " | ".join(parts)


def log_user_action(entity_type: str, entity_id: str, action: str,
                     actor: str, details: dict | None = None) -> str:
    """Convenience wrapper to log a user-initiated action."""
    return create_audit_log(entity_type, entity_id, action, actor=actor, details=details)
