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

    async def synthesize_answer(
        self, query: str, items: list[RecallItem]
    ) -> str | None:
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
            part = (
                f"{i}. [relevance={item.score:.2f}] "
                f"Command: {item.command}\n"
                f"   Output: {item.summary}\n"
                f"   Time: {item.timestamp.isoformat()}"
            )
            if item.was_resolved:
                part += (
                    f"\n   [RESOLUTION] This event was part of a troubleshooting sequence."
                )
                if item.resolution_command:
                    part += (
                        f"\n   → Resolution Action: {item.resolution_command}"
                    )
                if item.resolution_summary:
                    part += (
                        f"\n   → Resolution Summary: {item.resolution_summary}"
                    )
            context_parts.append(part)

        context = "\n\n".join(context_parts)
        return (
            f"You are Terminux, a terminal memory assistant. "
            f'The user is asking: "{query}"\n\n'
            f"Below is terminal history retrieved for this query. "
            f"Each item has a relevance score (0=unrelated, 1=perfect match). "
            f"ONLY use items with high relevance to answer. "
            f"If NO item is clearly relevant, say:\n"
            f"I don't have any information about that in your terminal history.\n\n"
            f"Do NOT guess, do NOT fabricate information, do NOT connect unrelated context.\n\n"
            f"When a [RESOLUTION] section is present, describe the full troubleshooting "
            f"story: what failed, why, and what command fixed it.\n\n"
            f"Format your answer in structured Markdown. Always end with:\n"
            f"### Suggested Action\n"
            f"```bash\n<command>\n```\n"
            f"so that the CLI can parse and offer to run it.\n\n"
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
