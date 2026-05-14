"""Shared fixtures for the Terminux test suite."""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Ensure the backend package is importable without install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.db import Store  # noqa: E402


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    """Return a fresh in-memory Store with a 20-minute session gap."""
    db_path = str(tmp_path / "test.db")
    return Store(sqlite_path=db_path, session_gap_seconds=1200)


@pytest.fixture()
def utc_now() -> datetime:
    return datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)
