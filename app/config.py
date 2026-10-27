"""Typed settings read from .env. Missing LLM keys fail fast at startup."""
from __future__ import annotations

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = REPO_ROOT / "dataset"
DATA_DIR = REPO_ROOT / "data"
LLM_CACHE_DIR = DATA_DIR / "llm_cache"
LLM_LEDGER_PATH = DATA_DIR / "llm_ledger.jsonl"
OCR_CACHE_DIR = DATA_DIR / "ocr_cache"
EVALUATION_DIR = REPO_ROOT / "evaluation"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    # Required: no defaults, so a missing key raises a ValidationError immediately.
    LLM_PROVIDER: str
    LLM_MODEL: str
    OLLAMA_HOST: str
    LLM_NUM_CTX: int

    # Defaults are the values Phase 0 starts from; override in .env.
    LLM_NUM_PREDICT: int = 2048
    LLM_TIMEOUT_S: float = 300.0
    LLM_SEED: int = 42
    LLM_KEEP_ALIVE: str = "30m"
    MAX_IMAGE_SIDE: int = 1600
    PDF_DPI: int = 150

    # Only needed for the Phase 6 upload. SecretStr keeps it out of reprs and logs.
    HF_TOKEN: SecretStr | None = None


def get_settings() -> Settings:
    return Settings()
