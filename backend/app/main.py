import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, candidates, documents, interviews, llm, rtc_webhook, sessions
from app.core.config import settings
from app.core.db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    if settings.mock_ai:
        log.warning("MOCK_AI is on - no BytePlus or ModelArk calls will be made")
    else:
        missing = settings.require_live_credentials()
        if missing:
            log.error("MOCK_AI is off but these are unset: %s", ", ".join(missing))
    yield


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
app.include_router(sessions.router, prefix=API)
app.include_router(candidates.router, prefix=API)

# These two are called by BytePlus, not the browser, so they keep stable root paths.
app.include_router(rtc_webhook.router)
app.include_router(llm.router)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "mock_ai": settings.mock_ai,
        "missing_credentials": settings.require_live_credentials(),
        "public_base_url": settings.public_base_url,
    }
