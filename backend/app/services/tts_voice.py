"""Voice replication and avatar assets.

These are one-time, per-interview setup steps, not per-session ones. Cloning a voice
takes minutes and costs money, so it must happen during setup - never inside the join
path where a candidate is waiting.

Consent note: Voice Replication clones a real person. Record and store that person
written consent before running `train_voice` against a live account.

VERIFY: endpoint paths below are placeholders.
"""

import logging

import httpx

from app.core.config import settings
from app.models import Interview

log = logging.getLogger(__name__)


def assign_defaults(interview: Interview) -> None:
    """Attach the account-level cloned voice and avatar unless the interview overrides."""
    if not interview.voice_id:
        interview.voice_id = settings.tts_voice_id or None
    if not interview.avatar_id:
        interview.avatar_id = settings.avatar_id or None


def readiness(interview: Interview) -> dict:
    """What the setup page shows before HR can create sessions.

    Avatar is optional: without AKOOL_API_KEY the interview still runs, just
    voice-only (confirmed live - StartVoiceChat accepts a session with no AvatarConfig
    at all). Missing voice is a hard blocker since there is no interview without audio;
    missing avatar is a soft note, not a blocker.
    """
    voice = interview.voice_id or settings.tts_voice_id
    avatar = interview.avatar_id or settings.avatar_id
    missing = []
    if not voice:
        missing.append("TTS_VOICE_ID")
    # Mock mode synthesises everything, so nothing is a blocker until MOCK_AI is off.
    if settings.mock_ai:
        missing = []
    return {
        "voice_id": voice or None,
        "avatar_id": avatar or None,
        "avatar_enabled": settings.avatar_enabled or settings.mock_ai,
        "ready": not missing,
        "missing": missing,
        "mock_ai": settings.mock_ai,
    }


async def train_voice(sample_audio_path: str, speaker_name: str) -> str:
    """Kick off Voice Replication 2.0 training. Returns the voice id to store on the
    interview. One-time; do not call per session."""
    if settings.mock_ai:
        return f"mock-voice-{speaker_name.lower().replace(' ', '-')}"

    with open(sample_audio_path, "rb") as fh:
        audio = fh.read()

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            "https://openspeech.byteplusapi.com/api/v1/mega_tts/audio/upload",
            headers={"Authorization": f"Bearer;{settings.seed_speech_api_key}"},
            json={
                "appid": settings.seed_speech_app_id,
                "speaker_id": speaker_name,
                "audios": [{"audio_bytes": audio.hex(), "audio_format": "wav"}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
    return str(data.get("speaker_id") or speaker_name)
