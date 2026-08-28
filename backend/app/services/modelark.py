"""ModelArk LLM client.

ModelArk exposes an OpenAI-compatible /chat/completions surface, so `model` is the
endpoint id and auth is a bearer token.

VERIFY: confirm base URL and endpoint-id-as-model against your ModelArk console before
switching MOCK_AI off.
"""

import json
import logging
from collections.abc import AsyncIterator

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=5.0)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.modelark_api_key}",
        "Content-Type": "application/json",
    }


async def chat(
    messages: list[dict],
    *,
    temperature: float = 0.6,
    max_tokens: int = 800,
) -> str:
    """Single-shot completion. Used off the hot path (planning, classification)."""
    if settings.mock_ai:
        return _mock_reply(messages)

    payload = {
        "model": settings.modelark_endpoint_id,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{settings.modelark_base_url}/chat/completions",
            headers=_headers(),
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"]


async def chat_json(messages: list[dict], *, temperature: float = 0.2) -> dict | list:
    """Completion that must return JSON. Falls back to {} rather than raising, so a
    malformed model response degrades the feature instead of killing the request."""
    raw = await chat(messages, temperature=temperature, max_tokens=2000)
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        text = text.removeprefix("json").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("chat_json got non-JSON response: %s", text[:300])
        return {}


async def stream_chat(
    messages: list[dict],
    *,
    temperature: float = 0.6,
    max_tokens: int = 400,
) -> AsyncIterator[str]:
    """Token stream for the interview hot path. Streaming matters here: RTC hands each
    chunk to TTS as it arrives, so time-to-first-token is what the candidate perceives
    as responsiveness, not total completion time."""
    if settings.mock_ai:
        for piece in _mock_reply(messages).split(" "):
            yield piece + " "
        return

    payload = {
        "model": settings.modelark_endpoint_id,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        async with client.stream(
            "POST",
            f"{settings.modelark_base_url}/chat/completions",
            headers=_headers(),
            json=payload,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
                if delta:
                    yield delta


def _mock_reply(messages: list[dict]) -> str:
    """Deterministic stand-in so the whole pipeline is exercisable without credentials."""
    directive = ""
    for m in reversed(messages):
        if m["role"] == "system" and "NEXT ACTION" in m.get("content", ""):
            directive = m["content"]
            break
    if "ASK:" in directive:
        return directive.split("ASK:", 1)[1].strip().split("\n")[0]
    if "WRAP_UP" in directive:
        return (
            "That covers everything I wanted to walk through. Thank you for your time "
            "today - someone from the team will be in touch about next steps."
        )
    if "DEFLECT" in directive:
        return "Let's keep our focus on the role. Could you tell me more about your experience?"
    return "Thanks for that. Could you walk me through a specific example?"
