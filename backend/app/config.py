from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)


def _as_bool(value: str, default: bool) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


@dataclass(frozen=True)
class Settings:
    sqlite_path: str = os.getenv("TERMINUX_SQLITE_PATH", "./data/terminux.db")
    vector_store_enabled: bool = _as_bool(
        os.getenv("TERMINUX_VECTOR_STORE_ENABLED", "true"), True
    )

    session_gap_seconds: int = int(os.getenv("TERMINUX_SESSION_GAP_SECONDS", "1200"))
    recall_default_limit: int = int(os.getenv("TERMINUX_RECALL_DEFAULT_LIMIT", "5"))

    embedding_backend: str = os.getenv("TERMINUX_EMBEDDING_BACKEND", "gemini")
    embedding_dim: int = int(os.getenv("TERMINUX_EMBEDDING_DIM", "768"))
    embedding_timeout_seconds: float = float(
        os.getenv("TERMINUX_EMBEDDING_TIMEOUT_SECONDS", "10")
    )
    synthesis_timeout_seconds: float = float(
        os.getenv("TERMINUX_SYNTHESIS_TIMEOUT_SECONDS", "15")
    )

    gemini_api_key: str = os.getenv("TERMINUX_GEMINI_API_KEY", "")
    gemini_api_base: str = os.getenv(
        "TERMINUX_GEMINI_API_BASE",
        "https://generativelanguage.googleapis.com/v1beta",
    )
    gemini_embedding_model: str = os.getenv(
        "TERMINUX_GEMINI_EMBEDDING_MODEL",
        "models/gemini-embedding-001",
    )
    gemini_generative_model: str = os.getenv(
        "TERMINUX_GEMINI_GENERATIVE_MODEL",
        "gemini-2.5-flash",
    )

    ollama_api_base: str = os.getenv(
        "TERMINUX_OLLAMA_API_BASE",
        "http://localhost:11434",
    )
    ollama_embedding_model: str = os.getenv(
        "TERMINUX_OLLAMA_EMBEDDING_MODEL",
        "nomic-embed-text",
    )


settings = Settings()
