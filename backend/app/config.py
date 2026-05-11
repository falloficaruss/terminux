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


def _env(primary: str, legacy: str, default: str) -> str:
    return os.getenv(primary, os.getenv(legacy, default))


@dataclass(frozen=True)
class Settings:
    sqlite_path: str = _env("TERMINUX_SQLITE_PATH", "TERMINUS_SQLITE_PATH", "./data/terminux.db")
    qdrant_url: str = _env("TERMINUX_QDRANT_URL", "TERMINUS_QDRANT_URL", "http://127.0.0.1:6333")
    qdrant_collection: str = _env("TERMINUX_QDRANT_COLLECTION", "TERMINUS_QDRANT_COLLECTION", "terminux_memory")
    qdrant_enabled: bool = _as_bool(_env("TERMINUX_QDRANT_ENABLED", "TERMINUS_QDRANT_ENABLED", "true"), True)

    session_gap_seconds: int = int(_env("TERMINUX_SESSION_GAP_SECONDS", "TERMINUS_SESSION_GAP_SECONDS", "1200"))
    recall_default_limit: int = int(_env("TERMINUX_RECALL_DEFAULT_LIMIT", "TERMINUS_RECALL_DEFAULT_LIMIT", "5"))

    embedding_backend: str = _env("TERMINUX_EMBEDDING_BACKEND", "TERMINUS_EMBEDDING_BACKEND", "gemini")
    embedding_dim: int = int(_env("TERMINUX_EMBEDDING_DIM", "TERMINUS_EMBEDDING_DIM", "768"))
    embedding_timeout_seconds: float = float(
        _env("TERMINUX_EMBEDDING_TIMEOUT_SECONDS", "TERMINUS_EMBEDDING_TIMEOUT_SECONDS", "10")
    )

    gemini_api_key: str = _env("TERMINUX_GEMINI_API_KEY", "TERMINUS_GEMINI_API_KEY", "")
    gemini_api_base: str = _env(
        "TERMINUX_GEMINI_API_BASE",
        "TERMINUS_GEMINI_API_BASE",
        "https://generativelanguage.googleapis.com/v1beta",
    )
    gemini_embedding_model: str = _env(
        "TERMINUX_GEMINI_EMBEDDING_MODEL",
        "TERMINUS_GEMINI_EMBEDDING_MODEL",
        "models/text-embedding-004",
    )

    ollama_api_base: str = _env(
        "TERMINUX_OLLAMA_API_BASE",
        "TERMINUS_OLLAMA_API_BASE",
        "http://localhost:11434",
    )
    ollama_embedding_model: str = _env(
        "TERMINUX_OLLAMA_EMBEDDING_MODEL",
        "TERMINUS_OLLAMA_EMBEDDING_MODEL",
        "nomic-embed-text",
    )


settings = Settings()
