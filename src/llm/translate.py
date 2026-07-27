from __future__ import annotations
import logging

from openai import AsyncOpenAI

from src.config import config

logger = logging.getLogger(__name__)

_client = AsyncOpenAI(api_key=config.openai_api_key)


async def translate(text: str, source_lang: str, target_lang: str) -> str:
    """One-shot translation via OpenAI. Returns only the translated text."""
    if not text.strip() or source_lang == target_lang:
        return text

    resp = await _client.chat.completions.create(
        model=config.openai_model,
        messages=[
            {
                "role": "system",
                "content": (
                    f"Translate the user's message from {source_lang} to {target_lang}. "
                    "Return only the translation — no notes, quotes, or language labels."
                ),
            },
            {"role": "user", "content": text},
        ],
        temperature=0,
    )
    translated = (resp.choices[0].message.content or "").strip()
    logger.info("translate %s→%s: %r → %r", source_lang, target_lang, text, translated)
    return translated
