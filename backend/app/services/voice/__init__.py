"""Self-hosted voice pipeline (VOICE_MODE=local).

We drive Seed ASR and Seed TTS ourselves with the modern single API key, instead of
handing them to BytePlus RTC's managed agent. See `app/core/config.py::voice_mode` for
why, and `pipeline.py` for the turn loop that joins the two legs to the interview brain.
"""
