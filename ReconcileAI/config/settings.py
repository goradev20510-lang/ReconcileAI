"""
ReconcileAI – Application Settings
All configuration is read from environment variables (.env file).
"""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root (real environment variables take precedence)
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


def _flag(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


# ── LLM ──────────────────────────────────────────────────────────────────────
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openrouter").strip().lower()   # openrouter | openai | gemini
_DEFAULT_MODELS = {
    "openrouter": "google/gemini-2.5-flash",
    "openai":     "gpt-4o-mini",
    "gemini":     "gemini-2.5-flash",
}
LLM_MODEL: str = (os.getenv("LLM_MODEL") or os.getenv("OPENROUTER_MODEL")
                  or _DEFAULT_MODELS.get(LLM_PROVIDER, "gpt-4o-mini"))

OPENAI_API_KEY:      str | None = os.getenv("OPENAI_API_KEY") or None
OPENROUTER_API_KEY:  str | None = os.getenv("OPENROUTER_API_KEY") or None
OPENROUTER_BASE_URL: str        = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
GEMINI_API_KEY:      str | None = os.getenv("GEMINI_API_KEY") or None
LLM_TIMEOUT_SECONDS: float      = float(os.getenv("LLM_TIMEOUT_SECONDS", 60))

# Master switch. When off (or no key is configured) every LLM feature falls back
# to deterministic rule-based extraction and template explanations.
LLM_ENABLED: bool = _flag("LLM_ENABLED", True)
# Ask the LLM to read bank narrations that carry no invoice reference (one call per such line).
LLM_NARRATION_HINTS: bool = _flag("LLM_NARRATION_HINTS", True)
# Ask the LLM to phrase match explanations (one call per auto-match). Off by default:
# the deterministic explanation is precise and free; turn on for prose.
LLM_MATCH_EXPLANATIONS: bool = _flag("LLM_MATCH_EXPLANATIONS", False)

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_PATH: Path = ROOT_DIR / os.getenv("DATABASE_PATH", "database/reconcile.db")

# ── File upload limits ────────────────────────────────────────────────────────
MAX_FILE_SIZE_BYTES: int = int(os.getenv("MAX_FILE_SIZE_MB", 25)) * 1024 * 1024
ALLOWED_EXTENSIONS = {
    "bank_statement":  {".csv", ".xlsx", ".xls"},
    "invoice":         {".pdf", ".xlsx", ".xls", ".csv"},
    "remittance":      {".eml", ".txt", ".pdf"},
    "tds_certificate": {".pdf", ".png", ".jpg", ".jpeg"},
}

# ── Application ───────────────────────────────────────────────────────────────
APP_ENV:   str = os.getenv("APP_ENV",   "development")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
DEFAULT_REVIEWER: str = os.getenv("DEFAULT_REVIEWER", "Finance Team")

# ── Directories ───────────────────────────────────────────────────────────────
DATA_RAW_DIR: Path = ROOT_DIR / "data" / "raw"
DATA_SYNTHETIC_DIR: Path = ROOT_DIR / "data" / "synthetic"
DATA_PROCESSED_DIR: Path = ROOT_DIR / "data" / "processed"
UPLOADS_DIR: Path = ROOT_DIR / "data" / "uploads"
