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

# One client, reused for the life of the process, instead of opening a fresh TCP+TLS
# connection to ModelArk on every single candidate turn. httpx pools keep-alive
# connections per host, so the second and later calls skip the handshake entirely - on a
# hot path where the model's own time-to-first-token already runs several seconds, paying
# a repeated handshake on top of that on every turn was pure waste.
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=_TIMEOUT)
    return _client


async def aclose() -> None:
    """Release the pooled connection on process shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.modelark_api_key}",
        "Content-Type": "application/json",
    }


# Explicitly off rather than left to the account/model default. Some ModelArk-hosted
# models (deep-reasoning Skylark/DeepSeek variants) emit hidden "thinking" tokens before
# the first visible one, which is indistinguishable from plain slowness in a
# time-to-first-token measurement. A live spoken interview never wants chain-of-thought
# read aloud, so this is a strict latency win (or a no-op if the endpoint already
# defaults to it) with no downside here.
_NO_THINKING = {"thinking": {"type": "disabled"}}


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
        **_NO_THINKING,
    }
    resp = await _get_client().post(
        f"{settings.modelark_base_url}/chat/completions",
        headers=_headers(),
        json=payload,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


async def chat_json(
    messages: list[dict], *, temperature: float = 0.2, max_tokens: int = 2000
) -> dict | list:
    """Completion that must return JSON. Falls back to {} rather than raising, so a
    malformed model response degrades the feature instead of killing the request."""
    raw = await chat(messages, temperature=temperature, max_tokens=max_tokens)
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
        **_NO_THINKING,
    }
    async with _get_client().stream(
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
    system = next((m.get("content", "") for m in messages if m["role"] == "system"), "")

    # The two JSON-shaped callers get real parseable JSON rather than prose, so rubric
    # drafting and candidate scoring are exercisable end to end in mock mode. The
    # interview turn path below deliberately keeps returning prose: its callers stream it
    # to TTS, and there is no JSON to be right about.
    if "THE THREE FIXED DIMENSIONS" in system:
        return _mock_rubric_draft()
    if "TRANSCRIPT QUALITY RATINGS" in system:
        return _mock_evaluation(messages)

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


def _mock_rubric_draft() -> str:
    """A complete, plausibly-worded rubric so the draft-then-review-then-approve flow is
    clickable without credentials. Generic on purpose - it is not derived from the job
    spec, and HR editing it before approval is exactly the intended workflow."""
    # Every band is parenthesised individually. In a nine-element structure of long
    # strings, a dropped comma would silently glue two bands together instead of raising,
    # and the result would look plausible enough to ship.
    bands = {
        "background_fit": (
            (
                "Describes concrete past work that maps onto the core duties in the job "
                "description, with enough specifics - the systems, the scale, their own "
                "part in it - to be credible."
            ),
            (
                "Describes adjacent or partially relevant work, or relevant work at a "
                "level of detail too thin to confirm they did it themselves."
            ),
            (
                "Describes no work that touches the core duties of the role, or describes "
                "it in terms that do not survive a follow-up question."
            ),
        ),
        "on_the_spot_reasoning": (
            (
                "Works through a scenario put to them during the interview, naming what "
                "they would check first and why, and adjusts when the interviewer adds a "
                "constraint."
            ),
            (
                "Offers a reasonable but generic approach, or reasons well on one prompt "
                "and falls back to a rehearsed answer on the next."
            ),
            (
                "Restates the question, gives an answer unrelated to what was asked, or "
                "cannot engage with a scenario beyond a prepared story."
            ),
        ),
        "communication_clarity": (
            (
                "Explains their own experience in an order a listener can follow, defines "
                "the terms specific to their old employer, and answers the question that "
                "was asked."
            ),
            (
                "Understandable overall, but rambles, buries the answer, or leans on "
                "internal jargon the interviewer would have to already know."
            ),
            (
                "Answers are hard to follow on their own terms - not merely mistranscribed "
                "- leaving the listener unable to tell what the candidate actually did."
            ),
        ),
    }
    return json.dumps(
        {
            "dimensions": [
                {"key": key, "strong": s, "decent": d, "not_fit": n}
                for key, (s, d, n) in bands.items()
            ]
        }
    )


def _mock_evaluation(messages: list[dict]) -> str:
    """A scoring pass that cites turns which really are in the transcript it was handed.

    Reading the candidate turn numbers back out of the prompt rather than hardcoding them
    means the evidence gate in services/evaluator.py is genuinely exercised in mock mode:
    if that validation ever broke, these citations would start being dropped.
    """
    import re

    transcript = next((m.get("content", "") for m in messages if m["role"] == "user"), "")
    turns = [int(n) for n in re.findall(r"^\[(\d+)\] CANDIDATE:", transcript, re.MULTILINE)]
    cite = [{"turn": t, "quote": "mock-mode paraphrase of this answer"} for t in turns[:2]]

    return json.dumps(
        {
            "dimensions": [
                {
                    "key": "background_fit",
                    "note": (
                        "Mock evaluation - no model was called. The candidate's stated "
                        "background is treated as partially matching the role."
                    ),
                    "evidence": cite,
                    "verdict": "decent",
                },
                {
                    "key": "on_the_spot_reasoning",
                    "note": (
                        "Mock evaluation - the candidate engaged with the scenario put "
                        "to them without going deep."
                    ),
                    "evidence": cite[:1],
                    "verdict": "decent",
                },
                {
                    "key": "communication_clarity",
                    "note": "Mock evaluation - answers were followable end to end.",
                    "evidence": cite[:1],
                    "verdict": "strong",
                },
            ],
            "transcript_quality": {
                "rating": "usable",
                "note": "Mock mode: the transcript was not actually assessed.",
            },
            "criteria_verdict": "decent_fit",
            "summary": (
                "This is a mock evaluation produced with MOCK_AI on - no language model was "
                "called and none of it reflects the real candidate. Turn MOCK_AI off and "
                "re-run scoring to get a real verdict. It exists so the scoring flow can be "
                "clicked through end to end without credentials."
            ),
        }
    )
