from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Any

import httpx

from .config import Settings

logger = logging.getLogger(__name__)
TOKEN_PATTERN = re.compile(r"[a-z0-9_./-]+")
SUPPORTED_BACKENDS = {"hash", "gemini"}


def _tokenize(text: str) -> list[str]:
    return TOKEN_PATTERN.findall(text.lower())


def _hash_embed(text: str, dim: int) -> list[float]:
    vector = [0.0] * dim
    tokens = _tokenize(text)
    if not tokens:
        return vector

    for token in tokens:
        digest = hashlib.sha1(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[idx] += sign

    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


class EmbeddingEngine:
    def __init__(self, cfg: Settings) -> None:
        self._cfg = cfg
        requested = cfg.embedding_backend.strip().lower()
        self._requested_backend = requested if requested in SUPPORTED_BACKENDS else "hash"
        self._runtime_backend = self._requested_backend
        self._client = httpx.Client(timeout=cfg.embedding_timeout_seconds)

        if self._runtime_backend == "gemini" and not cfg.gemini_api_key:
            logger.warning("TERMINUX_GEMINI_API_KEY is missing. Falling back to hash embeddings.")
            self._runtime_backend = "hash"

    @property
    def backend(self) -> str:
        return self._runtime_backend

    @property
    def dim(self) -> int:
        return self._cfg.embedding_dim

    def embed_text(self, text: str) -> list[float]:
        if self._runtime_backend == "gemini":
            try:
                return self._embed_with_gemini(text)
            except Exception as exc:  # pragma: no cover
                logger.warning("Gemini embeddings failed (%s). Falling back to hash backend.", exc)
                self._runtime_backend = "hash"

        return _hash_embed(text, dim=self._cfg.embedding_dim)

    def _embed_with_gemini(self, text: str) -> list[float]:
        model = self._cfg.gemini_embedding_model
        if not model.startswith("models/"):
            model = f"models/{model}"

        url = f"{self._cfg.gemini_api_base.rstrip('/')}/{model}:embedContent"
        headers = {"x-goog-api-key": self._cfg.gemini_api_key, "Content-Type": "application/json"}

        payloads = [
            {
                "content": {"parts": [{"text": text}]},
                "outputDimensionality": self._cfg.embedding_dim,
            },
            {
                "contents": [{"parts": [{"text": text}]}],
                "outputDimensionality": self._cfg.embedding_dim,
            },
        ]

        last_error: Exception | None = None
        for payload in payloads:
            try:
                response = self._client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                values = self._extract_values(response.json())
                return self._normalize_dim(values)
            except Exception as exc:
                last_error = exc
                continue

        if last_error is None:
            raise RuntimeError("Unexpected Gemini embedding failure")
        raise last_error

    def _extract_values(self, data: dict[str, Any]) -> list[float]:
        embedding = data.get("embedding")
        if isinstance(embedding, dict) and isinstance(embedding.get("values"), list):
            return [float(v) for v in embedding["values"]]

        embeddings = data.get("embeddings")
        if isinstance(embeddings, list) and embeddings:
            first = embeddings[0]
            if isinstance(first, dict):
                nested = first.get("embedding", first)
                if isinstance(nested, dict) and isinstance(nested.get("values"), list):
                    return [float(v) for v in nested["values"]]

        raise ValueError("Gemini response missing embedding values")

    def _normalize_dim(self, values: list[float]) -> list[float]:
        target = self._cfg.embedding_dim
        if len(values) == target:
            return values
        if len(values) > target:
            return values[:target]
        return values + [0.0] * (target - len(values))
