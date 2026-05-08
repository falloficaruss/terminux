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


@dataclass(frozen=True)
class Settings:
    sqlite_path: str = os.getenv("TERMINUS_SQLITE_PATH", "./data/terminus.db")
    qdrant_url: str = os.getenv("TERMINUS_QDRANT_URL", "http://127.0.0.1:6333")
    qdrant_collection: str = os.getenv("TERMINUS_QDRANT_COLLECTION", "terminus_memory")
    qdrant_enabled: bool = _as_bool(os.getenv("TERMINUS_QDRANT_ENABLED", "true"), True)
    session_gap_seconds: int = int(os.getenv("TERMINUS_SESSION_GAP_SECONDS", "1200"))
    recall_default_limit: int = int(os.getenv("TERMINUS_RECALL_DEFAULT_LIMIT", "5"))


settings = Settings()
