from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # ModelArk
    modelark_api_key: str = ""
    modelark_endpoint_id: str = ""
    modelark_base_url: str = "https://ark.ap-southeast.bytepluses.com/api/v3"

    # Speech (Seed Speech: ASR + TTS + Voice Clone share one App ID / API key at the
    # console level, confirmed against the BytePlus console rather than assumed).
    tts_voice_id: str = ""
    seed_speech_app_id: str = ""
    seed_speech_api_key: str = ""

    # Retrieval + memory
    vdb_kb_api_key: str = ""
    vdb_kb_base_url: str = ""
    vikingdb_memory_api_key: str = ""
    vikingdb_memory_base_url: str = ""

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

    def require_live_credentials(self) -> list[str]:
        """Return the names of settings that must be filled before MOCK_AI can be false."""
        required = {
            "RTC_APP_ID": self.rtc_app_id,
            "RTC_APP_KEY": self.rtc_app_key,
            "BYTEPLUS_ACCESS_KEY": self.byteplus_access_key,
            "BYTEPLUS_SECRET_KEY": self.byteplus_secret_key,
            "MODELARK_API_KEY": self.modelark_api_key,
            "MODELARK_ENDPOINT_ID": self.modelark_endpoint_id,
            "TTS_VOICE_ID": self.tts_voice_id,
            "AVATAR_ID": self.avatar_id,
            "AKOOL_API_KEY": self.akool_api_key,
            "SEED_SPEECH_APP_ID": self.seed_speech_app_id,
            "SEED_SPEECH_API_KEY": self.seed_speech_api_key,
        }
        return [name for name, value in required.items() if not value]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
