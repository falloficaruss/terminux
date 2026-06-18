"""Integration tests for the Terminux HTTP API.

Exercises the full POST /v1/events -> classify -> embed -> upsert pipeline
end-to-end using FastAPI's TestClient with isolated in-memory stores.
"""

from __future__ import annotations

import os
from typing import Generator

import pytest
from fastapi.testclient import TestClient

# Set test environment BEFORE importing app modules (config reads env at import time)
os.environ["TERMINUX_EMBEDDING_BACKEND"] = "hash"
os.environ["TERMINUX_EMBEDDING_DIM"] = "64"
os.environ["TERMINUX_SQLITE_PATH"] = ":memory:"
os.environ["TERMINUX_VECTOR_STORE_ENABLED"] = "true"

import app.main as _app_main
from app.config import Settings
from app.db import Store
from app.vector_store import VectorStore


@pytest.fixture(autouse=True)
def _fresh_store_and_vs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each test its own Store / VectorStore so tests never share state."""
    test_store = Store(sqlite_path=":memory:", session_gap_seconds=1200)
    test_settings = Settings(
        sqlite_path=":memory:",
        vector_store_enabled=True,
        embedding_backend="hash",
        embedding_dim=64,
    )
    test_vs = VectorStore(test_settings)
    monkeypatch.setattr(_app_main, "store", test_store)
    monkeypatch.setattr(_app_main, "vector_store", test_vs)


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    with TestClient(_app_main.app) as c:
        yield c


# ---------------------------------------------------------------------------
#  POST /v1/events  ->  classify  ->  embed  ->  upsert
# ---------------------------------------------------------------------------
class TestIngestPipeline:
    """Verify the full ingest chain: raw payload -> classified -> stored -> recallable."""

    def test_happy_path_returns_event_out(self, client: TestClient) -> None:
        payload = {
            "command": "docker build .",
            "output": "Error: build failed with exit code 127",
            "exit_code": 127,
            "duration_ms": 5000,
            "cwd": "/home/user/project",
        }
        resp = client.post("/v1/events", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["event_id"] >= 1
        assert data["session_id"] >= 1
        assert data["captured_at"] is not None

    def test_classifier_runs_and_sets_category(self, client: TestClient) -> None:
        payload = {
            "command": "npm install",
            "output": "error installing package",
            "exit_code": 1,
            "cwd": "/tmp",
        }
        resp = client.post("/v1/events", json=payload)
        assert resp.status_code == 200
        assert resp.json()["category"] == "package-management"

    def test_event_persisted_in_db_and_vector_store(self, client: TestClient) -> None:
        payload = {
            "command": "npm test",
            "output": "all tests passed",
            "exit_code": 0,
            "duration_ms": 5000,
            "cwd": "/home/user/project",
        }
        resp = client.post("/v1/events", json=payload)
        assert resp.status_code == 200
        event_id = resp.json()["event_id"]

        event = _app_main.store.get_event(event_id)
        assert event is not None
        assert event["command"] == "npm test"
        assert event["category"] is not None

        recall = client.get("/v1/recall", params={"query": "npm test", "limit": 5})
        assert recall.status_code == 200
        results = recall.json()["results"]
        assert any(r["event_id"] == event_id for r in results)

    def test_redaction_applied_before_storage(self, client: TestClient) -> None:
        payload = {
            "command": "curl -H 'Authorization: Bearer ghp_abc123def456' http://example.com",
            "output": "token worked",
            "exit_code": 0,
            "cwd": "/tmp",
        }
        resp = client.post("/v1/events", json=payload)
        assert resp.status_code == 200
        event_id = resp.json()["event_id"]

        event = _app_main.store.get_event(event_id)
        assert event is not None
        assert "ghp_abc123def456" not in event["command"]
        assert "[REDACTED]" in event["command"]

    def test_root_cause_extracted_for_failures(self, client: TestClient) -> None:
        payload = {
            "command": "cp /nonexistent /target",
            "output": "cp: cannot stat '/nonexistent': No such file or directory",
            "exit_code": 1,
            "cwd": "/tmp",
        }
        resp = client.post("/v1/events", json=payload)
        assert resp.status_code == 200
        event_id = resp.json()["event_id"]

        event = _app_main.store.get_event(event_id)
        assert event is not None
        assert event["root_cause"] == "missing file or path"
        assert event["category"] == "filesystem"

    def test_success_triggers_failure_fix_chain(self, client: TestClient) -> None:
        payload_failure = {
            "command": "make build",
            "output": "make: permission denied",
            "exit_code": 1,
            "duration_ms": 1000,
            "cwd": "/tmp",
        }
        resp = client.post("/v1/events", json=payload_failure)
        assert resp.status_code == 200

        payload_success = {
            "command": "make build",
            "output": "Build complete",
            "exit_code": 0,
            "duration_ms": 5000,
            "cwd": "/tmp",
        }
        resp = client.post("/v1/events", json=payload_success)
        assert resp.status_code == 200
        success_event_id = resp.json()["event_id"]

        cursor = _app_main.store.conn.execute(
            "SELECT * FROM failure_fixes WHERE success_event_id = ?",
            (success_event_id,),
        )
        fix = cursor.fetchone()
        assert fix is not None, "Expected a failure-fix link to be created"
        assert fix["summary"].startswith("Recovered command")


class TestHealthEndpoint:
    def test_health(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["db_ready"] is True
        assert data["vector_store_enabled"] is True
        assert data["vector_store_ready"] is True
        assert data["embedding_backend"] == "hash"
        assert data["embedding_dim"] == 64
        assert data["version"]
