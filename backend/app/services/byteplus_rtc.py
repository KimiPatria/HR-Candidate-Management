"""BytePlus RTC wrapper: room tokens plus StartVoiceChat / StopVoiceChat.

Verified against the official reference implementation in
byteplus-sdk/RTC_AIGC_Demo (Server/token.js, Server/util.js, Server/app.js,
Server/sensitive.js), not guessed:

  - `generate_token` is a line-for-line port of Server/token.js's AccessToken.serialize().
  - The OpenAPI host, region, and Version below come from Server/app.js's live signing
    call (`rtc.ap-southeast-1.byteplusapi.com`, region `ap-southeast-1`,
    `Version=2025-05-01`).
  - `_signed_headers` implements the same Volc Engine V4 signature algorithm as the
    official `volcengine` PyPI package's `SignerV4.sign` (ported rather than imported,
    since that package is synchronous and reads `~/.volc/credentials` on import - not a
    fit for this async codebase).
  - The `Config` field names in `_voice_chat_config` (ASRConfig.Provider="BytePlus",
    TTSConfig.Provider="byteplus_Bidirectional_streaming", AvatarConfig.Provider=
    "Volcano" with AvatarAppID/AvatarToken) come from Server/sensitive.js's
    VOICE_CHAT_MODE template, which has to name every field it injects secrets into.

Still unverified (no source seen for these, flagged inline):
  - VERIFY: exactly which field inside AvatarConfig.ProviderParams selects *which*
    avatar character to render - sensitive.js only shows the AvatarAppID/AvatarToken
    credential pair, not the character-selection field.
  - VERIFY: SubtitleConfig - the demo docs say subtitle callbacks are a **binary**
    message format, delivered via the client SDK or a server callback configured at the
    RTC console app level, not a per-request CallbackUrl. `rtc_webhook.py` currently
    assumes a JSON POST body and will need revisiting once this is confirmed.

MOCK_AI=true bypasses all of this and returns synthetic values so the rest of the stack
is testable without credentials.
"""

import base64
import hashlib
import hmac
import logging
import random
import struct
import time
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.models import Interview, InterviewSession

log = logging.getLogger(__name__)

TOKEN_TTL_SECONDS = 24 * 60 * 60  # matches the demo's generateRtcAccessToken
_SERVICE = "rtc"
_OPENAPI_VERSION = "2025-05-01"

# Privilege keys, from token.js. PrivPublishStream also implies the three sub-privileges
# below it - the JS AddPrivilege() call sets all four together.
_PRIV_PUBLISH_STREAM = 0
_PRIV_PUBLISH_AUDIO_STREAM = 1
_PRIV_PUBLISH_VIDEO_STREAM = 2
_PRIV_PUBLISH_DATA_STREAM = 3
_PRIV_SUBSCRIBE_STREAM = 4


# ------------------------------------------------------------------ room access token


def _put_bytes(buf: bytearray, data: bytes) -> None:
    buf += struct.pack("<H", len(data))
    buf += data


def _put_string(buf: bytearray, value: str) -> None:
    _put_bytes(buf, value.encode("utf-8"))


def _put_privileges(buf: bytearray, privileges: dict[int, int]) -> None:
    buf += struct.pack("<H", len(privileges))
    for key in sorted(privileges):
        buf += struct.pack("<H", key)
        buf += struct.pack("<I", privileges[key] & 0xFFFFFFFF)


def generate_token(room_id: str, user_id: str, ttl: int = TOKEN_TTL_SECONDS) -> str:
    """Build an RTC AccessToken granting publish+subscribe in one room.

    Port of AccessToken.serialize() in the official Server/token.js reference. Note the
    AppID is NOT part of the signed payload - it's appended to the token string raw,
    after the version prefix, and only the nonce/issuedAt/expireAt/roomID/userID/
    privileges are HMAC-signed with the App Key.
    """
    if settings.mock_ai:
        return f"mock-token.{room_id}.{user_id}"

    now = int(time.time())
    nonce = random.getrandbits(32)
    expire_at = now + ttl

    # "0 means forever" for the privilege's own expiry, per the demo; the *token's*
    # overall expiry (expire_at) is what actually bounds how long it is valid.
    privileges = {
        _PRIV_PUBLISH_STREAM: 0,
        _PRIV_PUBLISH_AUDIO_STREAM: 0,
        _PRIV_PUBLISH_VIDEO_STREAM: 0,
        _PRIV_PUBLISH_DATA_STREAM: 0,
        _PRIV_SUBSCRIBE_STREAM: 0,
    }

    msg = bytearray()
    msg += struct.pack("<I", nonce)
    msg += struct.pack("<I", now)
    msg += struct.pack("<I", expire_at)
    _put_string(msg, room_id)
    _put_string(msg, user_id)
    _put_privileges(msg, privileges)

    signature = hmac.new(settings.rtc_app_key.encode(), bytes(msg), hashlib.sha256).digest()

    content = bytearray()
    _put_bytes(content, bytes(msg))
    _put_bytes(content, signature)

    return "001" + settings.rtc_app_id + base64.b64encode(bytes(content)).decode()


# ------------------------------------------------------------------- request signing


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def _signed_headers(action: str, version: str, body: bytes) -> dict[str, str]:
    """Volc Engine V4 signature, ported from the official `volcengine` package's
    SignerV4.sign (auth/SignerV4.py: canonical request over method/path/query/
    signed-headers-block/signed-header-names/body-hash, HMAC-SHA256 signing key chain
    over secret-key -> date -> region -> service -> "request")."""
    now = datetime.now(timezone.utc)
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = x_date[:8]
    host = settings.rtc_openapi_base.split("://", 1)[-1]
    payload_hash = hashlib.sha256(body).hexdigest()

    query = f"Action={quote(action)}&Version={quote(version)}"
    signed_headers = "content-type;host;x-content-sha256;x-date"
    canonical_request = "\n".join(
        [
            "POST",
            "/",
            query,
            "content-type:application/json",
            f"host:{host}",
            f"x-content-sha256:{payload_hash}",
            f"x-date:{x_date}",
            "",
            signed_headers,
            payload_hash,
        ]
    )
    credential_scope = f"{short_date}/{settings.byteplus_region}/{_SERVICE}/request"
    string_to_sign = "\n".join(
        [
            "HMAC-SHA256",
            x_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )

    k_date = _sign(settings.byteplus_secret_key.encode(), short_date)
    k_region = _sign(k_date, settings.byteplus_region)
    k_service = _sign(k_region, _SERVICE)
    k_signing = _sign(k_service, "request")
    signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

    return {
        "Content-Type": "application/json",
        "Host": host,
        "X-Date": x_date,
        "X-Content-Sha256": payload_hash,
        "Authorization": (
            f"HMAC-SHA256 Credential={settings.byteplus_access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
    }


async def _call(action: str, payload: dict) -> dict:
    import json

    body = json.dumps(payload).encode()
    url = f"{settings.rtc_openapi_base}/?Action={action}&Version={_OPENAPI_VERSION}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            url, headers=_signed_headers(action, _OPENAPI_VERSION, body), content=body
        )
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------- voice chat


def _voice_chat_config(
    session: InterviewSession, interview: Interview, welcome_message: str
) -> dict:
    """Build the StartVoiceChat agent config.

    The LLMConfig block is the important one: pointing it at our own endpoint is what
    lets guardrails + RAG + the interview plan sit inside the turn loop, instead of RTC
    calling ModelArk directly. Its shape (flat Mode/Url/APIKey, OpenAI-compatible) is
    confirmed by Server/sensitive.js's injectSensitiveInfo, which merges APIKey directly
    into body.Config.LLMConfig when Mode is "CustomLLM".
    """
    llm_url = f"{settings.public_base_url}/v1/chat/completions"

    return {
        "AppId": settings.rtc_app_id,
        "RoomId": session.rtc_room_id,
        "TaskId": session.id,
        "Config": {
            "ASRConfig": {
                # Provider string and nested ProviderParams.BytePlus.{AppId,AccessToken}
                # confirmed by sensitive.js's VOICE_CHAT_MODE.ASRConfig template.
                "Provider": "BytePlus",
                "ProviderParams": {
                    "BytePlus": {
                        "AppId": settings.seed_speech_app_id,
                        "AccessToken": settings.seed_speech_api_key,
                    },
                },
                # Let the candidate finish a thought before the agent takes the turn.
                "VADConfig": {"SilenceTime": 800},
            },
            "TTSConfig": {
                # Confirmed by sensitive.js: this exact provider string, with the
                # App ID / token nested one level deeper under "app" than ASR's shape.
                "Provider": "byteplus_Bidirectional_streaming",
                "ProviderParams": {
                    "byteplus_Bidirectional_streaming": {
                        "app": {
                            "appid": settings.seed_speech_app_id,
                            "token": settings.seed_speech_api_key,
                        },
                    },
                },
                "VoiceType": interview.voice_id or settings.tts_voice_id,
            },
            "LLMConfig": {
                "Mode": "CustomLLM",
                "Url": llm_url,
                "APIKey": settings.rtc_app_key,
                "ModelName": "ai-interviewer",
                "Stream": True,
                # Echoed back to us on every turn so we can resolve the session.
                "CustomHeaders": {"X-Session-Id": session.id},
                "MaxTokens": 400,
                "Temperature": 0.6,
            },
            "AvatarConfig": {
                # Provider "Volcano" plus its own AvatarAppID/AvatarToken pair (from the
                # Flash Avatar console, distinct from the RTC App ID/Key) confirmed by
                # sensitive.js. VERIFY: which field selects the avatar character itself -
                # guessing ProviderParams.AvatarId until seen in real docs/responses.
                "Provider": "Volcano",
                "AvatarAppID": settings.avatar_app_id,
                "AvatarToken": settings.avatar_token,
                "ProviderParams": {
                    "AvatarId": interview.avatar_id or settings.avatar_id,
                },
            },
            # VERIFY: sensitive.js shows only `SubtitleMode`, and BytePlus's docs note
            # subtitle callbacks are a binary format delivered via the client SDK or a
            # server callback URL configured at the RTC console app level - not a
            # per-request CallbackUrl. rtc_webhook.py assumes JSON until this is checked.
            "SubtitleConfig": {"SubtitleMode": 0},
            "InterruptConfig": {"Enable": True},
        },
        "AgentConfig": {
            "UserId": f"agent-{session.id[:8]}",
            "TargetUserId": [session.rtc_user_id],
            "WelcomeMessage": welcome_message,
        },
    }


async def start_voice_chat(
    session: InterviewSession, interview: Interview, welcome_message: str = ""
) -> str:
    """Start the avatar agent in the session's room. Returns the RTC task id."""
    if settings.mock_ai:
        log.info("MOCK_AI: skipping StartVoiceChat for session %s", session.id)
        return f"mock-task-{session.id[:8]}"

    payload = _voice_chat_config(session, interview, welcome_message)
    data = await _call("StartVoiceChat", payload)
    result = data.get("Result") or {}
    return str(result.get("TaskId") or payload["TaskId"])


async def stop_voice_chat(session: InterviewSession) -> None:
    if settings.mock_ai:
        log.info("MOCK_AI: skipping StopVoiceChat for session %s", session.id)
        return
    try:
        await _call(
            "StopVoiceChat",
            {
                "AppId": settings.rtc_app_id,
                "RoomId": session.rtc_room_id,
                "TaskId": session.rtc_task_id or session.id,
            },
        )
    except Exception:  # noqa: BLE001
        # Ending an interview must succeed locally even if RTC teardown fails; the task
        # times out on its own server-side.
        log.exception("StopVoiceChat failed for session %s", session.id)
