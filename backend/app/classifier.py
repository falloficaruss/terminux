from __future__ import annotations

import re

CATEGORY_PATTERNS: dict[str, list[str]] = {
    "git-workflow": [r"\bgit\b", r"\bgh\b"],
    "deployment": [r"\bkubectl\b", r"\bhelm\b", r"\bterraform\b"],
    "container": [
        r"\bdocker\b",
        r"\bdocker-compose\b",
        r"\bcompose\b\s+(?:up|down|run|ps|build|exec|logs|images|pull|push|start|stop|restart)\b",
        r"\bpodman\b",
        r"\bpodman-compose\b",
    ],
    "networking": [r"\bping\b", r"\bcurl\b", r"\bifconfig\b", r"\bip\b"],
    "service": [r"\bsystemctl\b", r"\bservice\b", r"\bjournalctl\b"],
    "python-dev": [
        r"\bpytest\b",
        r"\buvicorn\b",
        r"\bpython\b",
        r"\bpoetry\b",
        r"\buv\s+(?:pip|run|sync|lock|add|remove|venv|tool|init|tree)\b",
        r"^uv\b(?!\s+(?:index|rays?|levels?|light|map|radiation)\b)",
    ],
    "package-management": [r"\bapt\b", r"\byum\b", r"\bpip\b", r"\bnpm\b", r"\bpnpm\b"],
    "gpu": [r"\bnvidia\b", r"\bcuda\b", r"\brocm\b"],
    "auth": [
        r"permission denied",
        r"unauthorized",
        r"forbidden",
        r"\b(?:access|auth|jwt|api|bearer|csrf|session|secret)[_-]?token\b",
        r"\btoken[_-]?(?:expired|invalid|missing)\b",
        r"\b(?:expired|invalid|missing)[_-]?token\b",
        r"\btoken\s+expired\b",
    ],
    "filesystem": [r"\brm\b", r"\bmv\b", r"\bcp\b", r"\bchmod\b", r"\bchown\b"],
}


def classify_event(command: str, output: str) -> str:
    blob = f"{command}\n{output}".lower()
    for category, patterns in CATEGORY_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, blob):
                return category
    if "error" in blob or "failed" in blob:
        return "debugging"
    return "general"


def likely_root_cause(output: str) -> str | None:
    lowered = output.lower()
    if "address already in use" in lowered or "port is already allocated" in lowered:
        return "port conflict"
    if "permission denied" in lowered:
        return "permission issue"
    if "connection refused" in lowered:
        return "service unavailable"
    if "module not found" in lowered or "no module named" in lowered:
        return "missing dependency"
    if "authentication" in lowered or "unauthorized" in lowered:
        return "authentication failure"
    if "no such file" in lowered:
        return "missing file or path"
    return None
