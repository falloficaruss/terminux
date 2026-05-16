from __future__ import annotations

import re

REDACTION_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[=:]\s*[^\s\"']+"),
    re.compile(r"(?i)authorization:\s*bearer\s+[a-z0-9._-]+"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    re.compile(r"(?i)[a-z0-9+.-]+://[^:\s\"']+:[^@\s\"']+@[^\s\"']+"),
    re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)[A-Z0-9]{16}\b"),
    re.compile(r"\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b"),
]


REDACTED_MARKER = "[REDACTED]"


def redact_sensitive_text(text: str) -> str:
    cleaned = text
    for pattern in REDACTION_PATTERNS:
        cleaned = pattern.sub(REDACTED_MARKER, cleaned)
    return cleaned


def redact_environment(env: dict[str, str] | None) -> dict[str, str]:
    if not env:
        return {}

    redacted: dict[str, str] = {}
    for key, value in env.items():
        normalized_key = key.upper()
        if any(token in normalized_key for token in ["KEY", "TOKEN", "SECRET", "PASSWORD"]):
            redacted[key] = REDACTED_MARKER
            continue
        redacted[key] = redact_sensitive_text(value)
    return redacted
