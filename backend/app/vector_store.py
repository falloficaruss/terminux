from __future__ import annotations

import json
import logging
import math
import sqlite3
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .text_vector import EmbeddingEngine

logger = logging.getLogger(__name__)


# Simple mock models namespace to mimic Qdrant's Filter objects.
# This keeps existing filter-construction code fully compatible without external dependencies.
class MatchValue:
    def __init__(self, value: Any) -> None:
        self.value = value


class FieldCondition:
    def __init__(self, key: str, match: MatchValue) -> None:
        self.key = key
        self.match = match


class Filter:
    def __init__(
        self,
        must: list[FieldCondition] | None = None,
        must_not: list[FieldCondition] | None = None,
    ) -> None:
        self.must = must or []
        self.must_not = must_not or []


class ModelsNamespace:
    MatchValue = MatchValue
    FieldCondition = FieldCondition
    Filter = Filter


models = ModelsNamespace()


@dataclass
class VectorHit:
    point_id: str
    score: float
    payload: dict[str, Any]


class VectorStore:
    def __init__(self, cfg: Settings) -> None:
        self._cfg = cfg
        self._embedder = EmbeddingEngine(cfg)
        self._enabled = cfg.vector_store_enabled
        self._ready = False
        self._conn: sqlite3.Connection | None = None

        if not self._enabled:
            return

        try:
            self._conn = sqlite3.connect(cfg.sqlite_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            with self._conn:
                self._conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS event_vectors (
                        event_id INTEGER PRIMARY KEY,
                        vector TEXT NOT NULL,
                        payload TEXT NOT NULL
                    )
                    """
                )
            self._ready = True
        except Exception as exc:  # pragma: no cover
            logger.warning("SQLite vector store initialization failed: %s", exc)
            self._enabled = False
            self._ready = False
            self._conn = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def embedding_backend(self) -> str:
        return self._embedder.backend

    @property
    def embedding_dim(self) -> int:
        return self._embedder.dim

    def upsert_event_memory(self, event_id: int, text: str, payload: dict[str, Any]) -> None:
        if not self._enabled or not self._conn:
            return

        try:
            vector = self._embedder.embed_text(text)
            with self._conn:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO event_vectors (event_id, vector, payload)
                    VALUES (?, ?, ?)
                    """,
                    (int(event_id), json.dumps(vector), json.dumps(payload)),
                )
        except Exception as exc:  # pragma: no cover
            logger.warning("Vector upsert failed: %s", exc)

    def set_payload_fields(self, event_id: int, fields: dict[str, Any]) -> None:
        if not self._enabled or not self._conn:
            return

        try:
            row = self._conn.execute(
                "SELECT payload FROM event_vectors WHERE event_id = ?",
                (int(event_id),),
            ).fetchone()
            if row:
                payload = json.loads(row["payload"])
                payload.update(fields)
                with self._conn:
                    self._conn.execute(
                        "UPDATE event_vectors SET payload = ? WHERE event_id = ?",
                        (json.dumps(payload), int(event_id)),
                    )
        except Exception as exc:  # pragma: no cover
            logger.warning("Set payload failed: %s", exc)

    def _matches_filter(self, payload: dict[str, Any], query_filter: Any) -> bool:
        if query_filter is None:
            return True

        try:
            # Check 'must' conditions
            if hasattr(query_filter, "must") and query_filter.must:
                for cond in query_filter.must:
                    if hasattr(cond, "key") and hasattr(cond, "match"):
                        match_val = getattr(cond.match, "value", None)
                        if payload.get(cond.key) != match_val:
                            return False

            # Check 'must_not' conditions
            if hasattr(query_filter, "must_not") and query_filter.must_not:
                for cond in query_filter.must_not:
                    if hasattr(cond, "key") and hasattr(cond, "match"):
                        match_val = getattr(cond.match, "value", None)
                        if payload.get(cond.key) == match_val:
                            return False
        except Exception as exc:  # pragma: no cover
            logger.warning("Error evaluating query filter: %s", exc)
            return False

        return True

    def search(self, query: str, limit: int, query_filter: Any = None) -> list[VectorHit]:
        if not self._enabled or not self._conn:
            return []

        try:
            query_vector = self._embedder.embed_text(query)

            # Fetch all stored vectors
            rows = self._conn.execute("SELECT event_id, vector, payload FROM event_vectors").fetchall()

            hits: list[VectorHit] = []
            for row in rows:
                payload = json.loads(row["payload"])
                if not self._matches_filter(payload, query_filter):
                    continue

                vector = json.loads(row["vector"])

                # Compute cosine similarity
                dot_product = sum(q * v for q, v in zip(query_vector, vector))
                norm_q = math.sqrt(sum(q * q for q in query_vector))
                norm_v = math.sqrt(sum(v * v for v in vector))

                score = 0.0
                if norm_q > 0 and norm_v > 0:
                    score = dot_product / (norm_q * norm_v)

                hits.append(
                    VectorHit(
                        point_id=str(row["event_id"]),
                        score=score,
                        payload=payload,
                    )
                )

            # Sort by score descending
            hits.sort(key=lambda h: h.score, reverse=True)
            return hits[:limit]
        except Exception as exc:  # pragma: no cover
            logger.warning("Vector search failed; falling back: %s", exc)
            return []

    def search_failures(self, query: str, limit: int = 5) -> list[VectorHit]:
        """Semantic search scoped to failure events only (exit_code != 0)."""
        if not self._enabled or not self._conn:
            return []

        query_filter = models.Filter(
            must_not=[
                models.FieldCondition(key="exit_code", match=models.MatchValue(value=0)),
            ]
        )
        return self.search(query=query, limit=limit, query_filter=query_filter)

    def find_similar_failure(self, command: str, project_root: str, threshold: float = 0.8) -> VectorHit | None:
        if not self._enabled or not self._conn:
            return None

        # Build filter for failures in the same project
        query_filter = models.Filter(
            must=[
                models.FieldCondition(key="project_root", match=models.MatchValue(value=project_root)),
            ],
            must_not=[
                models.FieldCondition(key="exit_code", match=models.MatchValue(value=0)),
            ],
        )

        hits = self.search(query=command, limit=1, query_filter=query_filter)
        if hits and hits[0].score >= threshold:
            return hits[0]
        return None
