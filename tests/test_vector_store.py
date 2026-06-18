"""Tests for backend/app/vector_store.py.

VectorStore uses SQLite as a local vector database, which operates out-of-the-box
without any external services.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.vector_store import VectorHit, VectorStore


# ---------------------------------------------------------------------------
# Settings Stubs for Testing
# ---------------------------------------------------------------------------
@dataclass
class _DisabledSettings:
    """Minimal Settings lookalike with vector store turned off."""

    sqlite_path: str = ":memory:"
    vector_store_enabled: bool = False
    session_gap_seconds: int = 1200
    recall_default_limit: int = 5
    embedding_backend: str = "hash"
    embedding_dim: int = 64
    embedding_timeout_seconds: float = 2.0
    synthesis_timeout_seconds: float = 2.0
    gemini_api_key: str = ""
    gemini_api_base: str = ""
    gemini_embedding_model: str = "models/text-embedding-004"
    gemini_generative_model: str = "gemini-2.5-flash"
    ollama_api_base: str = "http://localhost:11434"
    ollama_embedding_model: str = "nomic-embed-text"


@dataclass
class _EnabledSettings:
    """Minimal Settings lookalike with vector store turned on."""

    sqlite_path: str = ":memory:"
    vector_store_enabled: bool = True
    session_gap_seconds: int = 1200
    recall_default_limit: int = 5
    embedding_backend: str = "hash"
    embedding_dim: int = 64
    embedding_timeout_seconds: float = 2.0
    synthesis_timeout_seconds: float = 2.0
    gemini_api_key: str = ""
    gemini_api_base: str = ""
    gemini_embedding_model: str = "models/text-embedding-004"
    gemini_generative_model: str = "gemini-2.5-flash"
    ollama_api_base: str = "http://localhost:11434"
    ollama_embedding_model: str = "nomic-embed-text"


# ---------------------------------------------------------------------------
# VectorStore  –  disabled / guard-clause paths
# ---------------------------------------------------------------------------
class TestVectorStoreDisabled:
    """When vector_store_enabled=False every public method should be a safe no-op."""

    @pytest.fixture()
    def vs(self) -> VectorStore:
        cfg = _DisabledSettings()
        return VectorStore(cfg)

    def test_enabled_property(self, vs: VectorStore) -> None:
        assert vs.enabled is False

    def test_ready_property(self, vs: VectorStore) -> None:
        assert vs.ready is False

    async def test_upsert_is_noop(self, vs: VectorStore) -> None:
        # Should not raise
        await vs.upsert_event_memory(event_id=1, text="hello", payload={"a": 1})

    async def test_search_returns_empty(self, vs: VectorStore) -> None:
        assert await vs.search(query="anything", limit=5) == []

    async def test_search_failures_returns_empty(self, vs: VectorStore) -> None:
        assert await vs.search_failures(query="crash") == []

    async def test_find_similar_failure_returns_none(self, vs: VectorStore) -> None:
        assert await vs.find_similar_failure(command="build", project_root="/x") is None

    def test_embedding_backend_exposed(self, vs: VectorStore) -> None:
        assert vs.embedding_backend == "hash"

    def test_embedding_dim_exposed(self, vs: VectorStore) -> None:
        assert vs.embedding_dim == 64


# ---------------------------------------------------------------------------
# VectorStore  –  enabled / SQLite functionality tests
# ---------------------------------------------------------------------------
class TestVectorStoreEnabled:
    """Verify that the SQLite vector store works perfectly when enabled."""

    @pytest.fixture()
    def vs(self) -> VectorStore:
        cfg = _EnabledSettings()
        return VectorStore(cfg)

    def test_enabled_and_ready(self, vs: VectorStore) -> None:
        assert vs.enabled is True
        assert vs.ready is True

    async def test_upsert_and_search(self, vs: VectorStore) -> None:
        await vs.upsert_event_memory(
            event_id=1,
            text="docker command failed",
            payload={"event_id": 1, "exit_code": 1, "project_root": "/app"},
        )
        await vs.upsert_event_memory(
            event_id=2,
            text="git push successful",
            payload={"event_id": 2, "exit_code": 0, "project_root": "/app"},
        )

        # Normal search should find matches
        results = await vs.search(query="docker", limit=5)
        assert len(results) == 2
        # Verify that score is computed and correct event ID is returned
        assert results[0].point_id == "1"
        assert results[0].score > 0.0

    async def test_search_failures(self, vs: VectorStore) -> None:
        await vs.upsert_event_memory(
            event_id=1,
            text="docker command failed",
            payload={"event_id": 1, "exit_code": 1, "project_root": "/app"},
        )
        await vs.upsert_event_memory(
            event_id=2,
            text="git push successful",
            payload={"event_id": 2, "exit_code": 0, "project_root": "/app"},
        )

        # search_failures should only return event 1 (since event 2 exit_code = 0)
        results = await vs.search_failures(query="command", limit=5)
        assert len(results) == 1
        assert results[0].point_id == "1"

    async def test_find_similar_failure(self, vs: VectorStore) -> None:
        await vs.upsert_event_memory(
            event_id=10,
            text="make build failed error 127",
            payload={"event_id": 10, "exit_code": 127, "project_root": "/proj1"},
        )
        await vs.upsert_event_memory(
            event_id=11,
            text="make build failed error 127",
            payload={"event_id": 11, "exit_code": 127, "project_root": "/proj2"},
        )

        # Similar failure in /proj1 should find event 10
        hit = await vs.find_similar_failure(
            command="make", project_root="/proj1", threshold=0.3
        )
        assert hit is not None
        assert hit.point_id == "10"

        # In a different project, it should not match event 10
        hit_other = await vs.find_similar_failure(command="make", project_root="/proj3")
        assert hit_other is None

    async def test_set_payload_fields(self, vs: VectorStore) -> None:
        await vs.upsert_event_memory(
            event_id=5,
            text="npm install error",
            payload={"event_id": 5, "category": "npm", "exit_code": 1},
        )
        vs.set_payload_fields(event_id=5, fields={"category": "yarn", "updated": True})

        results = await vs.search(query="npm", limit=1)
        assert len(results) == 1
        assert results[0].payload["category"] == "yarn"
        assert results[0].payload["updated"] is True


# ---------------------------------------------------------------------------
# VectorHit dataclass
# ---------------------------------------------------------------------------
class TestVectorHit:
    def test_construction(self) -> None:
        hit = VectorHit(point_id="42", score=0.95, payload={"cmd": "git push"})
        assert hit.point_id == "42"
        assert hit.score == 0.95
        assert hit.payload == {"cmd": "git push"}

    def test_equality(self) -> None:
        a = VectorHit(point_id="1", score=0.9, payload={})
        b = VectorHit(point_id="1", score=0.9, payload={})
        assert a == b
