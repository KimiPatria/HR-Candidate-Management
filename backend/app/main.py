import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    auth,
    candidates,
    documents,
    integrations,
    interviews,
    knowledge,
    llm,
    rtc_webhook,
    rubrics,
    sessions,
    translate,
    voice_ws,
)
from app.core.config import INSTANCE_ID, settings
from app.core.db import init_db
from app.services import byteplus_rtc, embeddings, modelark, speech_check
from app.services import translate as translate_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


async def _warn_if_unreachable() -> None:
    """Log, once, whether BytePlus can actually call us back. See the caller."""
    last: Exception | None = None
    for attempt in range(3):
        await asyncio.sleep(1.0 * (attempt + 1))
        try:
            await byteplus_rtc.check_public_base_url()
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
    log.error("PUBLIC_BASE_URL UNUSABLE - interviews will be deaf. %s", last)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    try:
        await asyncio.to_thread(embeddings.warm_up)
        log.info("Local embedding model ready (%s)", embeddings.MODEL_NAME)
    except Exception:  # noqa: BLE001 - document indexing degrades to lexical search, not a crash
        log.exception(
            "Local embedding model failed to load - document indexing will fall back to "
            "keyword search until this is resolved (likely no network for its one-time download)"
        )
    if settings.mock_ai:
        log.warning("MOCK_AI is on - no BytePlus or ModelArk calls will be made")
    else:
        log.info(
            "Voice pipeline: %s",
            "local (we drive Seed ASR/TTS with the modern API key; RTC unused)"
            if settings.local_voice
            else "rtc (BytePlus StartVoiceChat owns ASR/TTS/turn loop)",
        )
        missing = settings.require_live_credentials()
        if missing:
            log.error("MOCK_AI is off but these are unset: %s", ", ".join(missing))
        # Presence-checking the speech credentials is not enough: StartVoiceChat accepts
        # any ProviderParams and returns Result: ok regardless, so bad speech credentials
        # surface only as an interview where the AI never speaks and never hears. Try
        # them for real, once, at startup.
        result = await speech_check.probe()
        if result["ok"]:
            log.info("Seed Speech credentials verified against the live gateway")
        elif result.get("reachable") is False:
            log.warning("Seed Speech credential check skipped: %s", result["summary"])
        else:
            log.error("SPEECH CREDENTIALS REJECTED - interviews will be silent. %s", result["summary"])

        # Same reasoning as the speech probe above, for the other half of the RTC path:
        # BytePlus fetches every candidate reply over PUBLIC_BASE_URL and tells us
        # nothing when it cannot. Saying so at boot beats discovering it as an interview
        # where the candidate is never heard.
        #
        # Deferred to a task rather than awaited here, because this probe goes out and
        # comes back to THIS process - and nothing is listening until lifespan yields, so
        # checking inline reports every healthy tunnel as broken. Retried a few times for
        # the same reason: the socket opens a moment after we are scheduled.
        #
        # A warning, never a hard failure. The tunnel is routinely started after the
        # backend, and `start_voice_chat` re-checks and refuses per session anyway - that
        # is the gate that protects an actual interview.
        if not settings.local_voice:
            _probe = asyncio.create_task(_warn_if_unreachable())
            app.state.base_url_probe = _probe  # keep a reference so it is not GC'd
    yield
    await modelark.aclose()
    await translate_service.aclose()


app = FastAPI(
    title="AI Candidate Interviewer",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Everything the browser calls lives under /api, so the SPA can own /join/{token} and
# other human-facing paths without colliding with the API.
API = "/api"
app.include_router(auth.router, prefix=API)
app.include_router(interviews.router, prefix=API)
app.include_router(documents.router, prefix=API)
app.include_router(knowledge.router, prefix=API)
app.include_router(integrations.router, prefix=API)
app.include_router(rubrics.router, prefix=API)
app.include_router(sessions.router, prefix=API)
app.include_router(candidates.router, prefix=API)
app.include_router(translate.router, prefix=API)
# Candidate microphone audio (VOICE_MODE=local). Closes immediately in RTC mode.
app.include_router(voice_ws.router, prefix=API)

# These two are called by BytePlus, not the browser, so they keep stable root paths.
app.include_router(rtc_webhook.router)
app.include_router(llm.router)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        # Identifies this process. `byteplus_rtc.check_public_base_url` fetches this over
        # PUBLIC_BASE_URL and compares it, which is what turns "the tunnel answered" into
        # "the tunnel reaches *us*" - a recycled hostname pointing at somebody else's
        # backend answers the first question just fine.
        "instance": INSTANCE_ID,
        "mock_ai": settings.mock_ai,
        "voice_mode": settings.voice_mode,
        "missing_credentials": settings.require_live_credentials(),
        # Soft capabilities: absent means the feature is hidden, not that the app is
        # broken, so they are reported here rather than in missing_credentials.
        "translate_enabled": settings.translate_enabled,
        "omnihuman_enabled": settings.omnihuman_enabled,
        # Only meaningful in RTC mode - local mode makes no inbound callbacks.
        "public_base_url": settings.public_base_url if not settings.local_voice else None,
    }
