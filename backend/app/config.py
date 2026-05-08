from __future__ import annotations

import os
from dataclasses import dataclass


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


settings = Settings()
