"""Live credential probe for Seed Speech (ASR + TTS).

Why this exists: StartVoiceChat accepts ANY ProviderParams. A payload carrying wrong
speech credentials - or a deliberately invented field name - still returns
`{"Result": "ok"}`, because RTC forwards ProviderParams to the speech service opaquely
rather than validating them. Confirmed by probe: four different ASR/TTS shapes, one with
a `ZZZNotAField` key, all returned Result: ok.

The consequence is the worst kind of failure. The agent task starts, joins the room, and
then silently authenticates as nobody: no welcome audio, no transcription, and - because
ASR never emits text - no call to our /v1/chat/completions at all. Every server-side
signal says healthy. The candidate just sits in silence.

So presence-checking the settings (`if not value`) is not enough; the values have to be
*tried*. Both Seed Speech gateways answer a plain WebSocket upgrade with 101 when the
credentials are good and 401/400 when they are not, before any audio is exchanged and
before anything is billed - which makes the handshake a free, decisive health check.

Credential form matters as much as credential validity. BytePlus has two vintages:
  - legacy: an App ID + Access Token pair, sent as X-Api-App-Key + X-Api-Access-Key
  - current console: a single API key, sent as X-Api-Key
StartVoiceChat's ProviderParams only carry the legacy pair (`app.appid` / `app.token` for
TTS, `AppId` / `AccessToken` for ASR), so a current-console API key cannot be handed to
the RTC agent at all. This probe reports which form the configured value actually is, so
that distinction shows up as a sentence instead of as an interview full of silence.
"""

import asyncio
import base64
import logging
import os
import re
import ssl

from app.core.config import settings

log = logging.getLogger(__name__)

# Single-sourced from settings so the probe can never drift from what the ASR and TTS
# clients actually connect to - a probe that checks a different endpoint than the code
# uses is worse than no probe.
_HOST = settings.seed_speech_host
_TTS_PATH = settings.seed_tts_path
_ASR_PATH = settings.seed_asr_path
_TTS_RESOURCE = settings.seed_tts_resource_id
_ASR_RESOURCE = settings.seed_asr_resource_id

_TIMEOUT_SECONDS = 10.0


async def _handshake(path: str, headers: dict[str, str]) -> tuple[int, str]:
    """Open a WebSocket upgrade and read only the HTTP response line.

    Nothing is sent on the socket beyond the handshake, so this neither synthesises
    speech nor starts a recognition session - it is not billable.
    """
    request = "\r\n".join(
        [
            f"GET {path} HTTP/1.1",
            f"Host: {_HOST}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {base64.b64encode(os.urandom(16)).decode()}",
            "Sec-WebSocket-Version: 13",
            *(f"{key}: {value}" for key, value in headers.items()),
        ]
    ) + "\r\n\r\n"

    reader, writer = await asyncio.open_connection(_HOST, 443, ssl=ssl.create_default_context())
    try:
        writer.write(request.encode())
        await writer.drain()
        raw = (await reader.read(4096)).decode("utf-8", "replace")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001 - close errors are not interesting here
            pass

    status = int(raw.split(" ", 2)[1]) if raw.startswith("HTTP/") else 0
    detail = ""
    if match := re.search(r"X-Api-Message: (.*)", raw, re.IGNORECASE):
        detail = match.group(1).strip()
    elif "\r\n\r\n" in raw:
        detail = raw.split("\r\n\r\n", 1)[1].strip()[:200]
    return status, detail


async def probe() -> dict:
    """Try the configured Seed Speech credentials against the live ASR and TTS gateways.

    Which credential form counts as "good" depends on voice_mode, because the two
    pipelines authenticate differently:
      - local mode calls Seed Speech itself and sends the modern single `X-Api-Key`
      - rtc mode hands credentials to StartVoiceChat, which can only carry the legacy
        `X-Api-App-Key` + `X-Api-Access-Key` pair
    Probing the wrong one reports a working setup as broken, which is how a green
    deployment ends up looking red (and vice versa).

    Returns a dict with `ok`, a human-readable `summary`, and per-leg detail. Never
    raises: a probe that cannot reach BytePlus reports `reachable: False` rather than
    taking down startup or /health.
    """
    if settings.local_voice:
        return await _probe_modern()

    app_id = settings.seed_speech_app_id
    token = settings.seed_speech_access_token

    if not app_id or not token:
        return {
            "ok": False,
            "reachable": None,
            "summary": "SEED_SPEECH_APP_ID / SEED_SPEECH_ACCESS_TOKEN are not both set.",
        }

    legacy_tts = {
        "X-Api-App-Key": app_id,
        "X-Api-Access-Key": token,
        "X-Api-Resource-Id": _TTS_RESOURCE,
    }
    legacy_asr = {
        "X-Api-App-Key": app_id,
        "X-Api-Access-Key": token,
        "X-Api-Resource-Id": _ASR_RESOURCE,
    }
    # Same secret, current-console form. Only probed to tell "the credential is wrong"
    # apart from "the credential is right but in a form RTC cannot carry" - a distinction
    # that decides whether the user needs a new credential or a different console page.
    modern_tts = {"X-Api-Key": token, "X-Api-Resource-Id": _TTS_RESOURCE}

    try:
        tts, asr, modern = await asyncio.wait_for(
            asyncio.gather(
                _handshake(_TTS_PATH, legacy_tts),
                _handshake(_ASR_PATH, legacy_asr),
                _handshake(_TTS_PATH, modern_tts),
            ),
            timeout=_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "reachable": False,
            "summary": f"Could not reach the Seed Speech gateway to verify credentials: {exc!r}",
        }

    tts_ok = tts[0] == 101
    asr_ok = asr[0] == 101
    ok = tts_ok and asr_ok

    if ok:
        summary = "Seed Speech ASR and TTS credentials accepted."
    elif modern[0] == 101:
        summary = (
            "SEED_SPEECH_ACCESS_TOKEN holds a current-console (New Console) BytePlus "
            "Speech API key (it is accepted as X-Api-Key), but StartVoiceChat can only "
            "carry the legacy App ID + Access Token pair - so the RTC agent authenticates "
            "as nobody and the interview is silent. Open the Old Console "
            "(console.byteplus.com/voice/app) -> Create Application -> Trial/Official Use "
            "and copy that application's App ID and Access Token into SEED_SPEECH_APP_ID "
            "and SEED_SPEECH_ACCESS_TOKEN."
        )
    elif app_id == token:
        summary = (
            "SEED_SPEECH_APP_ID and SEED_SPEECH_ACCESS_TOKEN are byte-identical - the same "
            "value was pasted into both. They are two different credentials from the "
            "BytePlus Old Console (console.byteplus.com/voice/app)."
        )
    else:
        summary = "Seed Speech rejected these credentials."

    return {
        "ok": ok,
        "reachable": True,
        "summary": summary,
        "tts": {"status": tts[0], "detail": tts[1]},
        "asr": {"status": asr[0], "detail": asr[1]},
        "accepted_as_modern_api_key": modern[0] == 101,
    }


async def _probe_modern() -> dict:
    """Check the modern single API key against both gateways - the local-mode path.

    Only SEED_SPEECH_API_KEY matters here; the App ID is not sent at all, so an empty
    or duplicated one is irrelevant rather than fatal.
    """
    key = settings.seed_speech_api_key
    if not key:
        return {
            "ok": False,
            "reachable": None,
            "summary": "SEED_SPEECH_API_KEY is not set - local voice mode needs it.",
        }

    tts_headers = {"X-Api-Key": key, "X-Api-Resource-Id": settings.seed_tts_resource_id}
    asr_headers = {"X-Api-Key": key, "X-Api-Resource-Id": settings.seed_asr_resource_id}

    try:
        tts, asr = await asyncio.wait_for(
            asyncio.gather(
                _handshake(settings.seed_tts_path, tts_headers),
                _handshake(settings.seed_asr_path, asr_headers),
            ),
            timeout=_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "reachable": False,
            "summary": f"Could not reach the Seed Speech gateway to verify credentials: {exc!r}",
        }

    ok = tts[0] == 101 and asr[0] == 101
    if ok:
        summary = "Seed Speech ASR and TTS accepted the API key (local voice mode)."
    elif tts[0] == 101:
        summary = (
            f"Seed TTS accepted the API key but ASR rejected it ({asr[0]}: {asr[1]}). "
            f"Check that {settings.seed_asr_resource_id} is enabled on this account."
        )
    elif asr[0] == 101:
        summary = (
            f"Seed ASR accepted the API key but TTS rejected it ({tts[0]}: {tts[1]}). "
            f"Check that {settings.seed_tts_resource_id} is enabled on this account."
        )
    else:
        summary = (
            f"Seed Speech rejected SEED_SPEECH_API_KEY as X-Api-Key "
            f"(TTS {tts[0]}, ASR {asr[0]}). Copy the API Key from the BytePlus Speech "
            "console again."
        )

    return {
        "ok": ok,
        "reachable": True,
        "summary": summary,
        "tts": {"status": tts[0], "detail": tts[1]},
        "asr": {"status": asr[0], "detail": asr[1]},
    }
