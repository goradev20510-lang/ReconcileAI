"""
ReconcileAI – Application Settings
All configuration is read from environment variables (.env file).
"""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

# ── LLM ──────────────────────────────────────────────────────────────────────
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai")        # openai | openrouter | gemini
# LLM_MODEL reads OPENROUTER_MODEL or LLM_MODEL (unified override)
_default_model = "google/gemini-flash-1.5" if os.getenv("LLM_PROVIDER") == "openrouter" else "gpt-4o-mini"
LLM_MODEL: str    = os.getenv("LLM_MODEL", os.getenv("OPENROUTER_MODEL", _default_model))

OPENAI_API_KEY:      str | None = os.getenv("OPENAI_API_KEY")
OPENROUTER_API_KEY:  str | None = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL: str        = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
GEMINI_API_KEY:      str | None = os.getenv("GEMINI_API_KEY")

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_PATH: Path = ROOT_DIR / os.getenv("DATABASE_PATH", "database/reconcile.db")

# ── File upload limits ────────────────────────────────────────────────────────
MAX_FILE_SIZE_BYTES: int = int(os.getenv("MAX_FILE_SIZE_MB", 25)) * 1024 * 1024
ALLOWED_EXTENSIONS = {
    "bank_statement": {".csv", ".xlsx", ".xls"},
    "invoice":        {".pdf", ".xlsx", ".xls"},
    "remittance":     {".eml", ".txt", ".pdf"},
}

# ── Application ───────────────────────────────────────────────────────────────
APP_ENV:   str = os.getenv("APP_ENV",   "development")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# ── Directories ───────────────────────────────────────────────────────────────
DATA_RAW_DIR: Path = ROOT_DIR / "data" / "raw"
DATA_SYNTHETIC_DIR: Path = ROOT_DIR / "data" / "synthetic"
DATA_PROCESSED_DIR: Path = ROOT_DIR / "data" / "processed"
