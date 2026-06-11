from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from .config import Settings
    from .schemas import RecallItem

logger = logging.getLogger(__name__)


class SynthesisEngine:
    def __init__(self, cfg: Settings) -> None:
        self._cfg = cfg
        self._client = httpx.AsyncClient(timeout=cfg.synthesis_timeout_seconds)

    async def synthesize_answer(self, query: str, items: list[RecallItem]) -> str | None:
        if not self._cfg.gemini_api_key:
            logger.warning("Synthesis disabled: TERMINUX_GEMINI_API_KEY missing.")
            return None

        if not items:
            return "No relevant history found for your query."

        prompt = self._build_prompt(query, items)
        try:
            return await self._call_gemini(prompt)
        except Exception as exc:
            logger.error("Synthesis failed: %s", exc)
            return None

    def _build_prompt(self, query: str, items: list[RecallItem]) -> str:
        context_parts = []
        for i, item in enumerate(items, 1):
            part = f"{i}. Command: {item.command}\n   Summary: {item.summary}\n   Time: {item.timestamp.isoformat()}"
            context_parts.append(part)

        context = "\n\n".join(context_parts)
        return (
            f"You are Terminux, an AI memory layer for a Linux terminal. "
            f"The user is asking: \"{query}\"\n\n"
            f"Based on the following retrieved terminal history, provide a concise, natural language answer. "
            f"If the history reveals a specific fix or root cause for a problem, highlight it. "
            f"If the history is not relevant to the question, state that clearly.\n\n"
            f"Retrieved History:\n{context}\n\n"
            f"Answer:"
        )

    async def _call_gemini(self, prompt: str) -> str | None:
        model = self._cfg.gemini_generative_model
        if not model.startswith("models/"):
            model = f"models/{model}"

        url = f"{self._cfg.gemini_api_base.rstrip('/')}/{model}:generateContent?key={self._cfg.gemini_api_key}"
        headers = {"Content-Type": "application/json"}

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "topP": 0.8,
                "topK": 40,
                "maxOutputTokens": 256,
            },
        }

        response = await self._client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

        try:
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError) as exc:
            logger.error("Unexpected Gemini response format: %s", exc)
            return None
