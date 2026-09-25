"""BytePlus Translate client.

The only module that talks to the Translate OpenAPI. Two callers today: the HR-facing
translated view of a transcript or document, and `rag.index_document`, which normalises
non-English reference material before embedding it.

Verified live by `scripts/probe_translate.py`: the host, region (ap-singapore-1, NOT the
RTC region), version and V4 signing are all confirmed correct against the real API - it
echoed Action/Version/Service/Region back, which it only does after authenticating.

Two things are NOT yet confirmed, both blocked on the account rather than on code:
  - VERIFY: that BytePlus Translate supports Bahasa Indonesia. `id` is not in the
    language list on the docs pages reachable without a console login, and the account
    could not be used to test it (see below). Error -415 is "unsupported language pair";
    if that is what `id` returns, translation should move to the ModelArk client instead.
  - VERIFY: the language-detection Action's name. The docs describe the endpoint without
    naming its Action anywhere we could read, so `_DETECT_ACTION` is the Volcengine name
    and `detect_language` falls back to reading `DetectedSourceLanguage` off a one-item
    TranslateText call if that name turns out to be wrong.

At the time of writing the account returns `-403 account status abnormal:
unsynchronized` for every call, which is what BytePlus returns for a service that has not
been activated in the console. Until that is fixed, every function here raises and the
features built on it stay dark: the HR translate toggle reports that translation is
unavailable, and documents index exactly as uploaded. Nothing that worked before breaks.
"""

import logging

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

_SERVICE = "translate"
_TRANSLATE_ACTION = "TranslateText"
# VERIFY: unconfirmed name, see the module docstring. Overridable without a code change.
_DETECT_ACTION = "LangDetect"

# Documented limits: 8 items per request, 5000 characters per item.
MAX_ITEMS_PER_REQUEST = 8
MAX_CHARS_PER_ITEM = 5000

# Only the first this many characters are used to work out what language a document is
# in. Detection does not get better with more input, and this keeps a 20MB PDF from
# turning a yes/no question into a paid translation of the whole thing.
DETECT_SAMPLE_CHARS = 800

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)

# One client for the life of the process, same reasoning as modelark.py: a transcript is
# translated in batches of 8, so a 60-turn transcript is 8 calls that would otherwise pay
# a fresh TLS handshake each.
_client: httpx.AsyncClient | None = None

# Translate reports failures as HTTP 200 with an Error object in the body - the -403 above
# arrived that way - so `raise_for_status()` alone would sail straight past a total
# failure and hand callers a list of empty strings. Every response goes through
# `_error_of` instead.
_ERRORS_ARE_IN_THE_BODY = True


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


def _error_of(body: dict) -> dict | None:
    return (body.get("ResponseMetadata") or {}).get("Error")


async def _call(action: str, payload: dict) -> dict:
    """One signed Translate call. Raises on transport failure or an in-body error."""
    from app.services import byteplus_sign

    body = await byteplus_sign.call(
        service=_SERVICE,
        base_url=settings.translate_openapi_base,
        region=settings.translate_region,
        action=action,
        version=settings.translate_api_version,
        payload=payload,
        client=_get_client(),
    )
    err = _error_of(body)
    if err:
        raise RuntimeError(f"{action} failed: {err.get('Code')} {err.get('Message')}")
    return body


def _split_oversized(text: str) -> list[str]:
    """Break one item that exceeds the per-item character cap into API-sized pieces.

    Splits on paragraph boundaries the way `rag.chunk_text` does, so a translated
    document reads as prose rather than as sentences severed mid-clause. Falls back to a
    hard slice only for a single paragraph that is itself over the cap.
    """
    if len(text) <= MAX_CHARS_PER_ITEM:
        return [text]

    pieces: list[str] = []
    buf = ""
    for para in text.split("\n\n"):
        if len(para) > MAX_CHARS_PER_ITEM:
            if buf:
                pieces.append(buf)
                buf = ""
            for i in range(0, len(para), MAX_CHARS_PER_ITEM):
                pieces.append(para[i : i + MAX_CHARS_PER_ITEM])
            continue
        candidate = f"{buf}\n\n{para}" if buf else para
        if len(candidate) > MAX_CHARS_PER_ITEM:
            pieces.append(buf)
            buf = para
        else:
            buf = candidate
    if buf:
        pieces.append(buf)
    return pieces


async def translate_texts(
    texts: list[str],
    target: str,
    source: str | None = None,
) -> list[str]:
    """Translate `texts` into `target`, one result per input, in order. Raises on failure.

    Raising rather than degrading is deliberate, and it is the one thing to preserve if
    this is ever refactored. Every caller STORES what comes back, so a version that
    quietly returned the originals on failure would write untranslated text into the
    cache as though it were a translation - indistinguishable from a real one, and served
    forever after. The place to soften a failure is the route (see api/translate.py,
    which answers 200 with `translated: false` so the reviewer keeps the original
    transcript and is told why), never here.

    `source` may be None to let the API detect it per item.
    """
    if not texts:
        return []

    if settings.mock_ai:
        return [f"[{target}] {t}" for t in texts]
    if not settings.translate_enabled:
        raise RuntimeError("BYTEPLUS_ACCESS_KEY / BYTEPLUS_SECRET_KEY are not set")

    # Expand oversized items, remembering which pieces belong to which input so they can
    # be rejoined afterwards.
    pieces: list[str] = []
    spans: list[tuple[int, int]] = []
    for text in texts:
        parts = _split_oversized(text) if text else [""]
        spans.append((len(pieces), len(pieces) + len(parts)))
        pieces.extend(parts)

    translated: list[str] = []
    for start in range(0, len(pieces), MAX_ITEMS_PER_REQUEST):
        batch = pieces[start : start + MAX_ITEMS_PER_REQUEST]
        payload: dict = {"TargetLanguage": target, "TextList": batch}
        if source:
            payload["SourceLanguage"] = source
        body = await _call(_TRANSLATE_ACTION, payload)

        results = [t.get("Translation", "") for t in (body.get("TranslationList") or [])]
        if len(results) != len(batch):
            # A short list here would silently misalign every later turn with the wrong
            # text, which is worse than not translating at all.
            raise RuntimeError(
                f"Translate returned {len(results)} results for {len(batch)} inputs"
            )
        translated.extend(results)

    return ["\n\n".join(translated[a:b]) for a, b in spans]


async def detect_language(text: str) -> tuple[str | None, float]:
    """Best guess at what language `text` is in, as (code, confidence).

    Returns (None, 0.0) when detection is unavailable rather than guessing, so callers
    can fall back to whatever language was declared instead of acting on a fabrication.
    """
    sample = (text or "").strip()[:DETECT_SAMPLE_CHARS]
    if not sample:
        return None, 0.0
    if settings.mock_ai:
        return "en", 1.0
    if not settings.translate_enabled:
        return None, 0.0

    try:
        body = await _call(_DETECT_ACTION, {"TextList": [sample]})
        row = (body.get("DetectedLanguageList") or [{}])[0]
        code = row.get("Language") or row.get("DetectedLanguage")
        if code:
            return code, float(row.get("Confidence", 0.0) or 0.0)
    except Exception:  # noqa: BLE001
        # Expected while _DETECT_ACTION is unverified - fall through to the translate
        # endpoint, which reports the language it detected as a side effect.
        log.debug("%s unavailable; detecting via TranslateText instead", _DETECT_ACTION)

    try:
        body = await _call(
            _TRANSLATE_ACTION,
            # Translating one short sample into English is the cheapest way to be told
            # what language it was. The translation itself is thrown away.
            {"TargetLanguage": "en", "TextList": [sample]},
        )
        row = (body.get("TranslationList") or [{}])[0]
        code = row.get("DetectedSourceLanguage")
        return (code, 1.0 if code else 0.0)
    except Exception:  # noqa: BLE001
        log.exception("Language detection failed; caller should assume nothing")
        return None, 0.0


async def translate_document(text: str, target: str, source: str | None = None) -> str:
    """Translate one long text as a unit. Raises on failure, like `translate_texts`."""
    if not text.strip():
        return text
    # A whole document is one logical item; _split_oversized handles the API's cap and
    # translate_texts rejoins the pieces.
    result = await translate_texts([text], target, source)
    return result[0] if result else text
