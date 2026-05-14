"""Tests for backend/app/vector_store.py.

VectorStore depends on Qdrant + an embedding backend, both of which are
external services.  These tests cover the guard-clause / disabled-path logic
that can be exercised without standing up infrastructure.
"""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from app.vector_store import VectorHit, VectorStore


# ---------------------------------------------------------------------------
# Helpers: a Settings stub that keeps Qdrant disabled
# ---------------------------------------------------------------------------
@dataclass
class _DisabledSettings:
    """Minimal Settings lookalike with Qdrant turned off."""
    sqlite_path: str = ":memory:"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "test_collection"
    qdrant_enabled: bool = False
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
    """When qdrant_enabled=False every public method should be a safe no-op."""

    @pytest.fixture()
    def vs(self) -> VectorStore:
        cfg = _DisabledSettings(qdrant_enabled=False)
        return VectorStore(cfg)

    def test_enabled_property(self, vs: VectorStore) -> None:
        assert vs.enabled is False

    def test_ready_property(self, vs: VectorStore) -> None:
        assert vs.ready is False

    def test_upsert_is_noop(self, vs: VectorStore) -> None:
        # Should not raise
        vs.upsert_event_memory(event_id=1, text="hello", payload={"a": 1})

    def test_search_returns_empty(self, vs: VectorStore) -> None:
        assert vs.search(query="anything", limit=5) == []

    def test_search_failures_returns_empty(self, vs: VectorStore) -> None:
        assert vs.search_failures(query="crash") == []

    def test_find_similar_failure_returns_none(self, vs: VectorStore) -> None:
        assert vs.find_similar_failure(command="build", project_root="/x") is None

    def test_embedding_backend_exposed(self, vs: VectorStore) -> None:
        assert vs.embedding_backend == "hash"

    def test_embedding_dim_exposed(self, vs: VectorStore) -> None:
        assert vs.embedding_dim == 64


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


# ---------------------------------------------------------------------------
# VectorStore  –  connection failure graceful degradation
# ---------------------------------------------------------------------------
class TestVectorStoreConnectionFailure:
    """If Qdrant is enabled but unreachable, the store should degrade
    gracefully to disabled state rather than crashing."""

    def test_unreachable_qdrant_disables_store(self) -> None:
        cfg = _DisabledSettings(qdrant_enabled=True, qdrant_url="http://localhost:1")
        vs = VectorStore(cfg)
        # Should have fallen back to disabled
        assert vs.enabled is False
        assert vs.ready is False
        # All operations should be safe no-ops
        assert vs.search("test", limit=5) == []
        assert vs.find_similar_failure("cmd", "/root") is None


# ---------------------------------------------------------------------------
# find_similar_failure  –  threshold logic (mocked)
# ---------------------------------------------------------------------------
class TestFindSimilarFailureThreshold:
    """Test threshold filtering without a real Qdrant instance."""

    def _make_vs_with_mock_search(self, hits: list[VectorHit]) -> VectorStore:
        cfg = _DisabledSettings(qdrant_enabled=False)
        vs = VectorStore(cfg)
        # Force enabled so the method body runs
        vs._enabled = True
        vs._client = MagicMock()
        vs.search = MagicMock(return_value=hits)  # type: ignore[assignment]
        return vs

    def test_above_threshold_returns_hit(self) -> None:
        hit = VectorHit(point_id="10", score=0.92, payload={"cmd": "build"})
        vs = self._make_vs_with_mock_search([hit])
        result = vs.find_similar_failure("build", "/proj", threshold=0.8)
        assert result is not None
        assert result.score == 0.92

    def test_below_threshold_returns_none(self) -> None:
        hit = VectorHit(point_id="10", score=0.5, payload={"cmd": "build"})
        vs = self._make_vs_with_mock_search([hit])
        result = vs.find_similar_failure("build", "/proj", threshold=0.8)
        assert result is None

    def test_empty_results_returns_none(self) -> None:
        vs = self._make_vs_with_mock_search([])
        result = vs.find_similar_failure("build", "/proj", threshold=0.8)
        assert result is None
