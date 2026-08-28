"""BytePlus RTC wrapper: room tokens plus StartVoiceChat / StopVoiceChat.

This is the module with the most unverified surface area. Three things must be checked
against the BytePlus console and SDK samples before MOCK_AI is switched off:

  VERIFY 1 - the AccessToken binary layout in `generate_token`. It follows the published
             v001 scheme, but a byte-level mismatch produces tokens the SDK silently
             rejects at join time. Cross-check against the official AccessToken sample.
  VERIFY 2 - the Volc V4 request signature in `_signed_headers`.
  VERIFY 3 - the StartVoiceChat payload shape in `_voice_chat_config`, especially the
             custom-LLM block and the Flash Avatar block.

MOCK_AI=true bypasses all three and returns synthetic values so the rest of the stack is
testable today.
"""

import base64
import hashlib
import hmac
import logging
import struct
import time
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.models import Interview, InterviewSession

log = logging.getLogger(__name__)

TOKEN_TTL_SECONDS = 3 * 60 * 60
_SERVICE = "rtc"


# ------------------------------------------------------------------ room access token


def _pack_string(value: bytes) -> bytes:
    return struct.pack("<H", len(value)) + value


def generate_token(room_id: str, user_id: str, ttl: int = TOKEN_TTL_SECONDS) -> str:
    """Build an RTC AccessToken granting publish+subscribe in one room.

    VERIFY 1 (see module docstring) before using in live mode.
    """
    if settings.mock_ai:
        return f"mock-token.{room_id}.{user_id}"

    now = int(time.time())
    expire_at = now + ttl

    # Privileges: 0 = publish stream, 1 = subscribe stream.
    privileges = {0: expire_at, 1: expire_at}
    priv_bytes = struct.pack("<H", len(privileges))
    for key, value in sorted(privileges.items()):
        priv_bytes += struct.pack("<H", key) + struct.pack("<I", value)

    body = (
        struct.pack("<I", now)  # nonce / issued-at
        + _pack_string(settings.rtc_app_id.encode())
        + _pack_string(room_id.encode())
        + _pack_string(user_id.encode())
        + struct.pack("<I", now)
        + struct.pack("<I", expire_at)
        + priv_bytes
    )
    signature = hmac.new(settings.rtc_app_key.encode(), body, hashlib.sha256).digest()
    packed = _pack_string(signature) + _pack_string(body)
    return "001" + settings.rtc_app_id + base64.b64encode(packed).decode()


# ------------------------------------------------------------------- request signing


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def _signed_headers(action: str, version: str, body: bytes) -> dict[str, str]:
    """Volc Engine V4 signature. VERIFY 2 before using in live mode."""
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


async def _call(action: str, version: str, payload: dict) -> dict:
    import json

    body = json.dumps(payload).encode()
    url = f"{settings.rtc_openapi_base}/?Action={action}&Version={version}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(url, headers=_signed_headers(action, version, body), content=body)
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------- voice chat


def _voice_chat_config(
    session: InterviewSession, interview: Interview, welcome_message: str
) -> dict:
    """Build the StartVoiceChat agent config. VERIFY 3 before using in live mode.

    The LLMConfig block is the important one: pointing it at our own endpoint is what
    lets guardrails + RAG + the interview plan sit inside the turn loop, instead of RTC
    calling ModelArk directly.
    """
    callback_url = f"{settings.public_base_url}/rtc/callback"
    llm_url = f"{settings.public_base_url}/v1/chat/completions"

    return {
        "AppId": settings.rtc_app_id,
        "RoomId": session.rtc_room_id,
        "TaskId": session.id,
        "Config": {
            "ASRConfig": {
                "Provider": "byteplus",
                "ProviderParams": {
                    "Mode": "streaming",
                    "AppId": settings.asr_app_id,
                    "Language": interview.language,
                },
                # Let the candidate finish a thought before the agent takes the turn.
                "VADConfig": {"SilenceTime": 800},
            },
            "TTSConfig": {
                "Provider": "byteplus",
                "ProviderParams": {
                    "AppId": settings.tts_app_id,
                    "VoiceType": interview.voice_id or settings.tts_voice_id,
                    "Language": interview.language,
                },
                # Speak the first sentence as soon as it arrives rather than waiting for
                # the full completion.
                "IgnoreBracketText": True,
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
                "Provider": "byteplus",
                "AvatarId": interview.avatar_id or settings.avatar_id,
                "Mode": "flash",
            },
            "SubtitleConfig": {"Enable": True, "CallbackUrl": callback_url},
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
    data = await _call("StartVoiceChat", "2024-12-01", payload)
    result = data.get("Result") or {}
    return str(result.get("TaskId") or payload["TaskId"])


async def stop_voice_chat(session: InterviewSession) -> None:
    if settings.mock_ai:
        log.info("MOCK_AI: skipping StopVoiceChat for session %s", session.id)
        return
    try:
        await _call(
            "StopVoiceChat",
            "2024-12-01",
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
