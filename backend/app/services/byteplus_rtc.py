"""BytePlus RTC wrapper: room tokens plus StartVoiceChat / StopVoiceChat.

Verified against the official reference implementation in byteplus-sdk/RTC_AIGC_Demo -
both the Node server (Server/token.js, Server/util.js, Server/app.js, Server/
sensitive.js) and the React client's config layer (src/config/voiceChat/{asr,tts,
avatar,llm}.ts, which type every field of the request body the client sends) - not
guessed:

  - `generate_token` is a line-for-line port of Server/token.js's AccessToken.serialize,
    and cross-checked byte-for-byte identical against the real JS with fixed inputs.
  - The OpenAPI host, region, and Version below come from Server/app.js's live signing
    call (`rtc.ap-southeast-1.byteplusapi.com`, region `ap-southeast-1`,
    `Version=2025-05-01`).
  - `_signed_headers` implements the same Volc Engine V4 signature algorithm as the
    official `volcengine` PyPI package's `SignerV4.sign` (ported rather than imported,
    since that package is synchronous and reads `~/.volc/credentials` on import - not a
    fit for this async codebase).
  - Every `Config` field name in `_voice_chat_config` is taken directly from the typed
    param maps in asr.ts / tts.ts / avatar.ts / llm.ts - see the comment on each block.
  - The whole thing is confirmed against the LIVE StartVoiceChat API, not just against
    reference source: a real signed call with this ASR/TTS/LLM shape and no AvatarConfig
    returned `{"Result": "ok"}`; adding an Akool-shaped AvatarConfig (even with a
    placeholder ApiKey) was also accepted, while the native BytePlus AvatarConfig shape
    was rejected with "akool avatar: ProviderParams is required" - proving this
    account's RTC app is provisioned for the Akool avatar integration, not native
    BytePlus/Volcano avatar.

Still unverified (no source seen for these, flagged inline):
  - VERIFY: SubtitleConfig - the demo docs say subtitle callbacks are a **binary**
    message format, delivered via the client SDK or a server callback configured at the
    RTC console app level, not a per-request CallbackUrl. `rtc_webhook.py` currently
    assumes a JSON POST body and will need revisiting once this is confirmed.
  - VERIFY: whether Bahasa Indonesia is supported by ASRConfig.ProviderParams.Language -
    the real type only lists 'zh-CN' | 'en-US' as options.
  - VERIFY: whether SEED_SPEECH_APP_ID/API_KEY (used for real, unfaked, in production)
    are actually valid - only tested with the account's real values structurally
    accepted by StartVoiceChat's parameter validation, not confirmed to produce working
    speech recognition/synthesis at runtime.

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


# ASR (SeedASR) only documents these two locales; VERIFY whether "id" (Bahasa
# Indonesia) is actually supported - falling back to en-US if not "zh".
def _asr_language(language: str) -> str:
    return "zh-CN" if language == "zh" else "en-US"


def _voice_chat_config(
    session: InterviewSession, interview: Interview, welcome_message: str
) -> dict:
    """Build the StartVoiceChat agent config.

    Field names below are verified against the real config managers in
    byteplus-sdk/RTC_AIGC_Demo (src/config/voiceChat/{asr,tts,avatar,llm}.ts), not
    guessed - each block's source is noted inline.

    The LLMConfig block is the important one: pointing it at our own endpoint is what
    lets guardrails + RAG + the interview plan sit inside the turn loop, instead of RTC
    calling ModelArk directly. Session identification rides on SystemMessages (a
    confirmed real field) rather than a CustomHeaders mechanism, which does not appear
    anywhere in the real LLMManager - our /v1/chat/completions handler already looks for
    a "SESSION_ID:" prefixed system message as one of its fallbacks (see
    api/llm.py:_resolve_session_id) and discards any other system content, since we
    build our own system prompt server-side regardless of what RTC sends us.
    """
    llm_url = f"{settings.public_base_url}/v1/chat/completions"

    return {
        "AppId": settings.rtc_app_id,
        "RoomId": session.rtc_room_id,
        "TaskId": session.id,
        "Config": {
            # Verified against ASRManager (asr.ts): Seed ASR 2.0 shape.
            "ASRConfig": {
                "Provider": "BytePlus",
                "ProviderParams": {
                    "Mode": "SeedASR",
                    "Language": _asr_language(interview.language),
                    "AppId": settings.seed_speech_app_id,
                    "AccessToken": settings.seed_speech_api_key,
                    "ApiResourceId": "volc.seedasr.sauc.duration",
                    "StreamMode": 2,
                    "enable_nonstream": True,
                },
            },
            # Verified against TTSManager (tts.ts): Seed TTS 2.0 shape. voice_type must
            # be one of the *_uranus_bigtts values to match resourceId "seed-tts-2.0".
            "TTSConfig": {
                "Provider": "byteplus_Bidirectional_streaming",
                "ProviderParams": {
                    "app": {
                        "appid": settings.seed_speech_app_id,
                        "token": settings.seed_speech_api_key,
                    },
                    "audio": {
                        "voice_type": interview.voice_id or settings.tts_voice_id,
                    },
                    "resourceId": "seed-tts-2.0",
                },
            },
            # Verified against LLMManager (llm.ts): flat CustomLLM shape - no Stream,
            # MaxTokens, Temperature, or CustomHeaders fields exist on the real type.
            "LLMConfig": {
                "Mode": "CustomLLM",
                "Url": llm_url,
                "ModelName": "ai-interviewer",
                "APIKey": settings.rtc_app_key,
                "SystemMessages": [f"SESSION_ID:{session.id}"],
            },
            # This account's RTC app is provisioned for the Akool third-party avatar
            # integration rather than native BytePlus/Volcano avatar - confirmed live: a
            # real StartVoiceChat call with the native shape (AvatarAppID/AvatarToken/
            # AvatarRole, no Provider field) was rejected with "akool avatar:
            # ProviderParams is required", while an Akool-shaped payload with a
            # placeholder ApiKey was accepted. AvatarId "dvp_Tristan_cloth2_1080P"
            # ("Tristan") is a confirmed real Akool preset from the official demo - no
            # training/recording needed. akool_api_key comes from Akool, not BytePlus.
            "AvatarConfig": {
                "Enabled": True,
                "Provider": "Akool",
                "AvatarUserID": f"agent-{session.id[:8]}",
                "ProviderParams": {
                    "ApiKey": settings.akool_api_key,
                    "AvatarId": interview.avatar_id or settings.avatar_id,
                },
            },
            # SubtitleMode 1 matches what the reference client sends whenever avatar
            # rendering is enabled (0 otherwise).
            "SubtitleConfig": {"SubtitleMode": 1},
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
