from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models

from .config import Settings
from .text_vector import VECTOR_DIM, embed_text

logger = logging.getLogger(__name__)


@dataclass
class VectorHit:
    point_id: str
    score: float
    payload: dict[str, Any]


class VectorStore:
    def __init__(self, cfg: Settings) -> None:
        self._cfg = cfg
        self._enabled = cfg.qdrant_enabled
        self._ready = False
        self._client: QdrantClient | None = None

        if not self._enabled:
            return

        try:
            self._client = QdrantClient(url=cfg.qdrant_url)
            if not self._client.collection_exists(cfg.qdrant_collection):
                self._client.create_collection(
                    collection_name=cfg.qdrant_collection,
                    vectors_config=models.VectorParams(size=VECTOR_DIM, distance=models.Distance.COSINE),
                )
            self._ready = True
        except Exception as exc:  # pragma: no cover
            logger.warning("Qdrant disabled at runtime: %s", exc)
            self._enabled = False
            self._ready = False
            self._client = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def ready(self) -> bool:
        return self._ready

    def upsert_event_memory(self, event_id: int, text: str, payload: dict[str, Any]) -> None:
        if not self._enabled or not self._client:
            return

        vector = embed_text(text)
        point = models.PointStruct(id=str(event_id), vector=vector, payload=payload)
        self._client.upsert(collection_name=self._cfg.qdrant_collection, points=[point], wait=False)

    def search(self, query: str, limit: int) -> list[VectorHit]:
        if not self._enabled or not self._client:
            return []

        vector = embed_text(query)

        if hasattr(self._client, "query_points"):
            result = self._client.query_points(
                collection_name=self._cfg.qdrant_collection,
                query=vector,
                limit=limit,
                with_payload=True,
            )
            points = list(getattr(result, "points", []))
        else:
            points = self._client.search(
                collection_name=self._cfg.qdrant_collection,
                query_vector=vector,
                limit=limit,
                with_payload=True,
            )

        hits: list[VectorHit] = []
        for point in points:
            hits.append(
                VectorHit(
                    point_id=str(point.id),
                    score=float(point.score),
                    payload=dict(point.payload or {}),
                )
            )
        return hits
