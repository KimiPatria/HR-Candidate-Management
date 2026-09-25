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
  - Request signing is the Volc Engine V4 algorithm, ported from the official
    `volcengine` PyPI package's `SignerV4.sign`. It now lives in `byteplus_sign.py`,
    shared with the other BytePlus OpenAPIs this app calls; see that module for why it
    is ported rather than imported, and for why it must not be mixed with the
    `byteplus_sdk` signer used by the Visual/CV scripts.
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
  - RESOLVED: SEED_SPEECH_APP_ID/SEED_SPEECH_ACCESS_TOKEN (the legacy pair this module
    sends to ASRConfig/TTSConfig) were, until now, both set to a copy of the modern
    single API key - not a real legacy pair. `speech_check.probe()` confirmed this live:
    the value was accepted as a modern X-Api-Key but rejected as the legacy
    X-Api-App-Key/X-Api-Access-Key pair, exactly the silent-failure mode described above
    (StartVoiceChat returns Result: ok, the agent joins and greets, ASR/TTS then
    authenticate as nobody). Fixed by splitting SEED_SPEECH_API_KEY (modern, local-mode
    only) from SEED_SPEECH_APP_ID/SEED_SPEECH_ACCESS_TOKEN (legacy, rtc-mode only, from
    the Old Console at console.byteplus.com/voice/app) so the two vintages can no longer
    collide. Still VERIFY once a real legacy pair is in place: that it actually produces
    working speech recognition/synthesis at runtime, not just a passing handshake.

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

import httpx

from app.core.config import INSTANCE_ID, settings
from app.models import Interview, InterviewSession
from app.services import byteplus_sign

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


async def _call(action: str, payload: dict) -> dict:
    """One signed RTC OpenAPI call.

    The signature algorithm itself lives in `byteplus_sign`, shared with the other
    BytePlus OpenAPIs this app calls. What stays here is only what is specific to RTC:
    the service name, its host, its region, and its API version.
    """
    return await byteplus_sign.call(
        service=_SERVICE,
        base_url=settings.rtc_openapi_base,
        region=settings.byteplus_region,
        action=action,
        version=_OPENAPI_VERSION,
        payload=payload,
    )


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
    # Session identity rides on THREE independent carriers, because losing it 400s every
    # turn and the candidate simply hears nothing. The query parameter is the mechanism
    # BytePlus explicitly recommends for attaching per-task business data to a CustomLLM
    # Url ("append it directly to LLMConfig.Url as a query parameter", with the worked
    # example .../v1/chat-stream?session_id=12345); ExtraHeader is the documented header
    # channel; SystemMessages is the original guess and stays as a third fallback.
    # api/llm.py:_resolve_session_id reads all three, so any one surviving is enough.
    llm_url = f"{settings.public_base_url}/v1/chat/completions?session_id={session.id}"

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
                    "AccessToken": settings.seed_speech_access_token,
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
                        "token": settings.seed_speech_access_token,
                    },
                    "audio": {
                        "voice_type": interview.voice_id or settings.tts_voice_id,
                    },
                    "resourceId": "seed-tts-2.0",
                },
            },
            # Flat CustomLLM shape - the "ThirdParty LLM" sub-shape of LLMConfig takes no
            # nested ProviderParams, unlike ASRConfig/TTSConfig above.
            #
            # CORRECTION to an earlier note here: the reference client's TypeScript types
            # do not show it, but the StartVoiceChat API reference does document
            # `ExtraHeader` (a JSON map forwarded as extra HTTP headers on each LLM
            # request) alongside `Custom`, `MaxTokens`, `MaxCompletionTokens`,
            # `ReasoningEffort`, `Tools`, `MCP` and `VisionConfig`. The field is called
            # ExtraHeader, not CustomHeaders - which is probably why it was missed.
            "LLMConfig": {
                "Mode": "CustomLLM",
                "Url": llm_url,
                "ModelName": "ai-interviewer",
                "APIKey": settings.rtc_app_key,
                "ExtraHeader": {"X-Session-Id": session.id},
                "SystemMessages": [f"SESSION_ID:{session.id}"],
            },
            # SubtitleMode 1 matches what the reference client sends whenever avatar
            # rendering is enabled (0 otherwise) - set below alongside AvatarConfig.
            "SubtitleConfig": {"SubtitleMode": 1 if settings.avatar_enabled else 0},
            "InterruptConfig": {"Enable": True},
            **_avatar_config_block(interview, session),
        },
        "AgentConfig": {
            "UserId": f"agent-{session.id[:8]}",
            "TargetUserId": [session.rtc_user_id],
            "WelcomeMessage": welcome_message,
        },
    }


def _avatar_config_block(interview: Interview, session: InterviewSession) -> dict:
    """AvatarConfig is entirely omitted when no Akool key is set, rather than sent with
    an empty ApiKey - confirmed live that StartVoiceChat accepts audio-only sessions
    (ASR/TTS/LLM, no AvatarConfig at all) with a plain {"Result": "ok"}, so a missing
    avatar credential degrades to voice-only instead of failing the whole session.

    This account's RTC app is provisioned for the Akool third-party avatar integration
    rather than native BytePlus/Volcano avatar - confirmed live: a real StartVoiceChat
    call with the native shape (AvatarAppID/AvatarToken/AvatarRole, no Provider field)
    was rejected with "akool avatar: ProviderParams is required", while an Akool-shaped
    payload with a placeholder ApiKey was accepted. AvatarId "dvp_Tristan_cloth2_1080P"
    ("Tristan") is a confirmed real Akool preset from the official demo - no
    training/recording needed. akool_api_key comes from Akool, not BytePlus.
    """
    if not settings.avatar_enabled:
        return {}
    return {
        "AvatarConfig": {
            "Enabled": True,
            "Provider": "Akool",
            "AvatarUserID": f"agent-{session.id[:8]}",
            "ProviderParams": {
                "ApiKey": settings.akool_api_key,
                "AvatarId": interview.avatar_id or settings.avatar_id,
            },
        },
    }


# How long to wait for our own tunnel to answer. The round trip leaves this machine, goes
# out to the tunnel provider and comes back, so it is not instant - but it happens once
# per interview, and a tunnel this slow to answer is one RTC will time out on anyway.
_BASE_URL_PROBE_SECONDS = 8.0


async def check_public_base_url() -> None:
    """Prove PUBLIC_BASE_URL actually reaches THIS backend. Raises if it does not.

    This is the check that `public_base_url_plausible` only pretends to be. RTC fetches
    every single candidate reply over this URL from BytePlus's cloud, and it reports
    nothing back to us when that fetch fails - so an unreachable URL is not an error
    anywhere, it is an interview where the candidate talks and is never heard. That is
    exactly how it presented: the mic visualiser moved, the greeting played (we generate
    that locally and hand it over as WelcomeMessage, so it needs no tunnel at all), and
    the transcript stayed empty for the whole session.

    Three failures are worth separating, because they need different fixes:
      - the host does not resolve / refuses      -> the tunnel is dead or its hostname
                                                    went stale (quick tunnels get a new
                                                    random one on every restart)
      - it answers, but not with our instance id -> the tunnel points at a different
                                                    backend than this process
      - it answers 404                           -> something is in front of the tunnel
                                                    rewriting or eating paths
    """
    if not settings.public_base_url_plausible:
        raise RuntimeError(
            f"PUBLIC_BASE_URL is {settings.public_base_url!r}, which BytePlus cannot "
            "call back. RTC fetches every reply from "
            f"{settings.public_base_url}/v1/chat/completions over the public internet, "
            "so this must be a public HTTPS tunnel to this backend (e.g. `cloudflared "
            "tunnel --url http://localhost:8000` or `ngrok http 8000`), not localhost, "
            "a LAN address, or the .env placeholder."
        )

    base = settings.public_base_url.rstrip("/")
    advice = (
        "Start a tunnel to this backend and put the hostname it prints into "
        "PUBLIC_BASE_URL, then restart the backend so it is re-read:\n"
        "    cloudflared tunnel --url http://localhost:8000\n"
        "A `cloudflared tunnel --url` quick tunnel gets a NEW random hostname every "
        "time it starts, so the old one in .env stops resolving as soon as you restart "
        "it. Use a named tunnel if you want a hostname that survives."
    )

    try:
        async with httpx.AsyncClient(timeout=_BASE_URL_PROBE_SECONDS) as client:
            resp = await client.get(f"{base}/health")
    except Exception as exc:  # noqa: BLE001 - DNS, TLS, timeout and refusal are all fatal
        raise RuntimeError(
            f"PUBLIC_BASE_URL {base} did not answer ({exc!r}). RTC would fetch every "
            f"candidate reply from {base}/v1/chat/completions, so the interview would be "
            f"deaf: the candidate hears the greeting and is then never heard.\n{advice}"
        ) from exc

    if resp.status_code != 200:
        raise RuntimeError(
            f"PUBLIC_BASE_URL {base} answered /health with HTTP {resp.status_code}, not "
            f"200. Something is in front of the tunnel, or it points somewhere that is "
            f"not this backend.\n{advice}"
        )

    try:
        reached = resp.json().get("instance")
    except Exception:  # noqa: BLE001 - a non-JSON body means it is not us either
        reached = None
    if reached != INSTANCE_ID:
        raise RuntimeError(
            f"PUBLIC_BASE_URL {base} answered, but it is not this backend: /health "
            f"reported instance {reached!r}, this process is {INSTANCE_ID!r}. The "
            f"tunnel is pointing at a different server (or a stale hostname that now "
            f"belongs to someone else).\n{advice}"
        )

    log.info("PUBLIC_BASE_URL %s verified - it reaches this backend", base)


async def start_voice_chat(
    session: InterviewSession, interview: Interview, welcome_message: str = ""
) -> str:
    """Start the avatar agent in the session's room. Returns the RTC task id."""
    if settings.mock_ai:
        log.info("MOCK_AI: skipping StartVoiceChat for session %s", session.id)
        return f"mock-task-{session.id[:8]}"

    # Refuse rather than start an agent that can greet the candidate and nothing else.
    # StartVoiceChat happily accepts an LLM Url it cannot reach, so without this the
    # session looks healthy - room joined, welcome message spoken - and then every
    # candidate turn silently dies inside RTC with no callback to tell us.
    await check_public_base_url()

    payload = _voice_chat_config(session, interview, welcome_message)
    data = await _call("StartVoiceChat", payload)
    result = data.get("Result")
    task_id = result.get("TaskId") if isinstance(result, dict) else None
    return str(task_id or payload["TaskId"])


async def stop_voice_chat(session: InterviewSession) -> None:
    if settings.mock_ai:
        log.info("MOCK_AI: skipping StopVoiceChat for session %s", session.id)
        return
    if settings.local_voice:
        # No agent task was ever started - the local pipeline tears itself down when the
        # candidate's WebSocket closes.
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
