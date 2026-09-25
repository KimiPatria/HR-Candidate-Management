import uuid
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Identifies THIS uvicorn process, and nothing else. `/health` returns it so the RTC
# preflight can prove the public tunnel reaches this backend rather than merely reaching
# *a* backend - a recycled quick-tunnel hostname now belonging to someone else answers
# /health perfectly happily. Regenerated on every start, deliberately: a reused id would
# make a stale tunnel look live across restarts, which is the exact bug being guarded.
INSTANCE_ID = uuid.uuid4().hex


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    app_env: str = "dev"
    hr_password: str = "change-me"
    session_secret: str = "dev-secret-not-for-production"
    # Where RTC reaches this backend (the tunnel URL in dev).
    public_base_url: str = "http://localhost:8000"
    # Where the candidate opens their interview link (the frontend origin).
    candidate_app_url: str = "http://localhost:5173"
    cors_origins: str = "http://localhost:5173"

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/app.db"

    # Mock switch: when true no provider makes a network call.
    mock_ai: bool = True

    # Which voice pipeline drives the interview. This is the single switch between two
    # complete implementations of "candidate speaks, interviewer answers":
    #
    #   "rtc"   - BytePlus RTC's managed conversational-AI agent (StartVoiceChat) owns
    #             transport, ASR, TTS and the turn loop; it calls our /v1/chat/completions
    #             once per candidate utterance. Needs the LEGACY Seed Speech App ID +
    #             Access Token pair, which the upgraded BytePlus Speech console no longer
    #             issues - see the comment on seed_speech_app_id.
    #   "local" - we own the pipeline: the browser streams mic PCM to our own WebSocket,
    #             we drive Seed ASR and Seed TTS directly with the modern single API key
    #             (X-Api-Key), and run the turn loop in-process.
    #
    # Both paths share the same interview brain (services/interview_turn.py), so this
    # switch changes transport only - never behaviour. Flip back to "rtc" the day
    # BytePlus issues a legacy credential pair and nothing else needs to change.
    voice_mode: str = "local"

    # BytePlus common
    byteplus_access_key: str = ""
    byteplus_secret_key: str = ""
    # Confirmed against the official RTC_AIGC_Demo server reference.
    byteplus_region: str = "ap-southeast-1"

    # RTC
    rtc_app_id: str = ""
    rtc_app_key: str = ""
    rtc_openapi_base: str = "https://rtc.ap-southeast-1.byteplusapi.com"
    # Avatar rendering for this account's RTC app is provisioned via Akool (a
    # third-party avatar vendor), confirmed by a live StartVoiceChat call: the native
    # BytePlus/Volcano AvatarConfig shape was rejected ("akool avatar: ProviderParams
    # is required"), while an Akool-shaped payload was accepted. akool_api_key comes
    # from Akool, not the BytePlus console. avatar_id holds the Akool avatar id.
    avatar_id: str = "dvp_Tristan_cloth2_1080P"
    akool_api_key: str = ""
    # Kept for accounts that DO have native BytePlus avatar entitlement instead.
    avatar_app_id: str = ""
    avatar_token: str = ""

    # Dreamina OmniHuman - the OTHER way to put a face on screen, and a completely
    # different shape from the two above. Akool/Flash Avatar renders a live stream inside
    # the RTC session; OmniHuman is an async batch job (submit photo + audio, poll, get an
    # mp4) billed per second of generated video. That pricing rules it out per-turn - a
    # 30-minute interview is roughly 720s of AI speech, which is two orders of magnitude
    # more than the same interview costs today - so it is used for exactly one thing here:
    # a single opening-greeting clip, generated once per INTERVIEW at setup time and
    # replayed to every candidate. Off unless a req_key is set.
    #
    # VERIFY: omnihuman_req_key is not published in the docs that are reachable without a
    # console login, and this account's entitlement (Vision AI CV vs ModelArk video tasks)
    # is unconfirmed. `scripts/probe_omnihuman.py` is what settles both - same as the
    # Akool-vs-native avatar question was settled, by one live call.
    omnihuman_req_key: str = ""
    omnihuman_poll_timeout_seconds: int = 900

    # ModelArk
    modelark_api_key: str = ""
    modelark_endpoint_id: str = ""
    modelark_base_url: str = "https://ark.ap-southeast.bytepluses.com/api/v3"

    # Speech (Seed Speech: ASR + TTS + Voice Clone).
    #
    # BytePlus has two credential vintages, issued from two different console pages, and
    # each voice pipeline reads only its own - they are independent, not synced:
    #   - modern (console.byteplus.com/voice/new/overview - "New Console"): ONE api key,
    #     sent as the `X-Api-Key` header. This is all VOICE_MODE=local reads.
    #   - legacy (console.byteplus.com/voice/app - "Old Console" -> Create Application ->
    #     Trial/Official Use): an App ID + Access Token + Secret Key TRIPLE. RTC's
    #     StartVoiceChat can only carry the App ID + Access Token part of this vintage
    #     (ASRConfig wants AppId/AccessToken, TTSConfig wants app.appid/app.token -
    #     confirmed against the current master of the official byteplus-sdk/RTC_AIGC_Demo
    #     reference, which still defines no ApiKey field for the BytePlus provider, and no
    #     use of a Secret Key). This is all VOICE_MODE=rtc reads. The Secret Key is
    #     captured below in case some other legacy call ends up needing it, but nothing
    #     reads it yet.
    tts_voice_id: str = ""
    seed_speech_api_key: str = ""
    seed_speech_app_id: str = ""
    seed_speech_access_token: str = ""
    seed_speech_secret_key: str = ""

    # Seed Speech gateway. Single source of truth for the speech probe, the ASR client
    # and the TTS client, so a region change is one edit rather than four.
    seed_speech_host: str = "voice.ap-southeast-1.bytepluses.com"
    seed_asr_path: str = "/api/v3/sauc/bigmodel_async"
    # The utterance-at-a-time recogniser, used for languages the streaming model above
    # cannot hear. Confirmed live: fed the same Bahasa Indonesia audio, the streaming
    # endpoint returned "Syndicate a bug a Manager, Project de Bruijn Technology" while
    # this one returned "Saya bergabung sebagai manager proyek di perusahaan technology".
    # It is the same account, key and resource id - only the model differs. See
    # `services/voice/asr.py` for what it costs us in exchange.
    seed_asr_utterance_path: str = "/api/v3/sauc/bigmodel_nostream"
    seed_asr_resource_id: str = "volc.seedasr.sauc.duration"
    seed_tts_path: str = "/api/v3/tts/bidirection"
    seed_tts_resource_id: str = "seed-tts-2.0"
    # What we ask Seed TTS to hand back. PCM keeps the browser side trivial - no decoder,
    # just schedule the samples - at the cost of bandwidth we have plenty of locally.
    seed_tts_sample_rate: int = 24000

    # BytePlus Translate.
    #
    # Reuses the account-wide BYTEPLUS_ACCESS_KEY/SECRET_KEY above - no separate
    # credential - but NOT byteplus_region: Translate is documented at ap-singapore-1
    # while RTC is at ap-southeast-1, and signing with the wrong one fails the signature
    # rather than degrading, so it gets its own setting.
    translate_openapi_base: str = "https://open.byteplusapi.com"
    translate_region: str = "ap-singapore-1"
    translate_api_version: str = "2020-06-01"
    # Language to normalise reference documents into before embedding them, or "" to
    # index every document exactly as uploaded (the behaviour before this existed).
    #
    # Worth turning on for a bilingual deployment: embeddings.py runs
    # BAAI/bge-small-en-v1.5, an English-tuned model, so an Indonesian document embeds
    # into roughly the wrong part of the space and retrieves badly. Translating at ingest
    # is a retrieval-quality fix, not a convenience. The retrieved snippet then reaches
    # the LLM in English while the interview runs in Indonesian, which is fine - retrieved
    # context is model input, never spoken verbatim, and prompts.py already pins the
    # spoken language.
    translate_index_language: str = ""

    # Retrieval + memory
    vdb_kb_api_key: str = ""
    vdb_kb_base_url: str = ""
    vikingdb_memory_api_key: str = ""
    vikingdb_memory_base_url: str = ""

    # ---- Drive pickers (Google Drive, OneDrive) ----
    #
    # All four are PUBLIC client identifiers, the kind that ship inside any single-page
    # app that talks to Google or Microsoft. They are served to the HR browser by
    # /api/integrations, which is why none of them is a secret and why no OAuth client
    # secret appears here: the picker runs entirely in the browser, the user consents
    # with their own account, and the access token that results never reaches this
    # process. We only ever receive the bytes of the file they chose.
    #
    # Leave a client id blank to hide that drive in the UI - the paste and upload paths
    # are unaffected, so the feature is additive.
    google_client_id: str = ""
    # Browser API key restricted to the Picker API, from the same Google Cloud project.
    google_api_key: str = ""
    # The project NUMBER (not the id) - what Picker calls the app id.
    google_app_id: str = ""
    # Application (client) id of an Entra ID app registration carrying a redirect URI of
    # platform type Web for this app's origin. Not SPA: the v7.2 OneDrive picker predates
    # MSAL and does its own popup sign-in.
    microsoft_client_id: str = ""

    # Storage
    upload_dir: str = "./data/uploads"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def upload_path(self) -> Path:
        p = Path(self.upload_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def public_base_url_plausible(self) -> bool:
        """Whether PUBLIC_BASE_URL *looks* like an address BytePlus could call.

        RTC calls `{PUBLIC_BASE_URL}/v1/chat/completions` for every candidate utterance,
        from their cloud - not from this machine. A loopback address, a LAN address, or
        the unedited `.env` placeholder all mean the agent delivers its welcome message
        (which we generate locally and hand to StartVoiceChat) and is then deaf for the
        rest of the interview. That failure presents as a dead microphone rather than as
        a config error, so it is worth catching up front instead of live.

        This is a STRING check and nothing more - the name says `plausible`, not
        `reachable`, because it was called the latter and that cost a real interview. A
        Cloudflare quick tunnel (`cloudflared tunnel --url ...`) mints a new random
        `*.trycloudflare.com` hostname on every start and stops resolving the moment the
        process exits, so a stale one is perfectly well-formed and completely dead. It
        passed every test below while returning NXDOMAIN, the agent was started against
        it, and every candidate turn died in DNS inside BytePlus's cloud with no callback
        to tell us. Only an actual request can catch that - see
        `byteplus_rtc.check_public_base_url`, which is the gate that matters.
        """
        url = self.public_base_url.strip()
        if not url:
            return False
        host = url.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
        if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or host.endswith(".local"):
            return False
        if host.endswith(".example.com") or host.endswith(".example"):
            return False
        # RFC1918 - a tunnel is required precisely because these are not routable from
        # BytePlus's side, even though they resolve fine from this laptop.
        octets = host.split(".")
        if len(octets) == 4 and all(o.isdigit() for o in octets):
            first, second = int(octets[0]), int(octets[1])
            if first == 10 or (first == 192 and second == 168):
                return False
            if first == 172 and 16 <= second <= 31:
                return False
        return True

    @property
    def local_voice(self) -> bool:
        """True when we drive ASR/TTS ourselves instead of handing them to RTC."""
        return self.voice_mode.strip().lower() == "local"

    @property
    def avatar_enabled(self) -> bool:
        """Whether to include AvatarConfig in StartVoiceChat at all.

        Confirmed live that BytePlus accepts an audio-only session (ASR/TTS/LLM, no
        AvatarConfig) with a plain {"Result": "ok"} - so a missing Akool key degrades to
        voice-only instead of blocking the whole interview.
        """
        return bool(self.akool_api_key)

    @property
    def google_drive_enabled(self) -> bool:
        """Picker needs all three: the OAuth client to get a token, the API key to load
        the picker itself, and the app id to scope it. Two out of three is a picker that
        opens and then fails, so treat it as off."""
        return bool(self.google_client_id and self.google_api_key and self.google_app_id)

    @property
    def onedrive_enabled(self) -> bool:
        return bool(self.microsoft_client_id)

    @property
    def translate_enabled(self) -> bool:
        """Whether transcript/document translation can be offered at all.

        Soft, like `avatar_enabled`: without it the HR translate toggle is hidden and
        documents index as uploaded. Nothing that already worked stops working.
        """
        return bool(self.byteplus_access_key and self.byteplus_secret_key)

    @property
    def omnihuman_enabled(self) -> bool:
        """Whether a greeting clip can be generated. Soft - interviews run voice-only."""
        return bool(
            self.omnihuman_req_key and self.byteplus_access_key and self.byteplus_secret_key
        )

    def require_live_credentials(self) -> list[str]:
        """Return the names of settings that must be filled before MOCK_AI can be false.

        The list depends on voice_mode, because the two pipelines genuinely need
        different things - reporting RTC's requirements while running local mode would
        send someone hunting for credentials the running code never reads.

        AKOOL_API_KEY is deliberately not required in either mode - see `avatar_enabled`.
        Its absence is surfaced separately (readiness checks, /health) as a soft warning,
        since the interview runs fine voice-only without it.
        """
        # The interview brain is the same either way, so these are always required.
        required = {
            "MODELARK_API_KEY": self.modelark_api_key,
            "MODELARK_ENDPOINT_ID": self.modelark_endpoint_id,
            "TTS_VOICE_ID": self.tts_voice_id,
        }

        if self.local_voice:
            # Local mode talks to Seed Speech and ModelArk outbound only. Nothing calls
            # back in, so no tunnel is needed - and no RTC credentials at all. Only the
            # modern single key is read on this path.
            required["SEED_SPEECH_API_KEY"] = self.seed_speech_api_key
            return [name for name, value in required.items() if not value]

        # PUBLIC_BASE_URL is listed even though it is a URL rather than a credential: RTC
        # fetches every reply over it, and an unreachable one breaks the interview just
        # as completely as a missing key, and far less visibly.
        required.update(
            {
                "PUBLIC_BASE_URL": (
                    self.public_base_url if self.public_base_url_plausible else ""
                ),
                "RTC_APP_ID": self.rtc_app_id,
                "RTC_APP_KEY": self.rtc_app_key,
                "BYTEPLUS_ACCESS_KEY": self.byteplus_access_key,
                "BYTEPLUS_SECRET_KEY": self.byteplus_secret_key,
                # RTC reads only the legacy pair - never the modern key above, which this
                # path leaves unrequired.
                "SEED_SPEECH_APP_ID": self.seed_speech_app_id,
                "SEED_SPEECH_ACCESS_TOKEN": self.seed_speech_access_token,
            }
        )
        return [name for name, value in required.items() if not value]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
