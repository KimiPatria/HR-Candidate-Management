"""Seed ASR clients - candidate audio in, transcript out.

Talks straight to the Seed ASR gateway over WebSocket with the modern single API key
(`X-Api-Key`), which is the credential the upgraded BytePlus Speech console issues. This
is the leg that RTC's StartVoiceChat cannot do for us, because its ASRConfig can only
carry the legacy App ID + Access Token pair.

There are two clients here because BytePlus exposes two different models behind one
account, and they do not understand the same languages.

`SeedASRStream` is the streaming client (`bigmodel_async`). It runs server-side VAD and
dual-pass recognition, emitting a refined `definite: true` utterance when the speaker
stops - the end-of-turn signal a conversation needs, for free. Its language coverage is
Mandarin, English, Cantonese and Chinese regional dialects.

`UtteranceASRStream` is the non-streaming client (`bigmodel_nostream`). It hears Bahasa
Indonesia, which the streaming model does not, at the cost of returning nothing until an
utterance is finished - so the caller has to say when that is.

Fed identical Indonesian audio, confirmed live rather than assumed:

    bigmodel_async     "Syndicate a bug a Manager, Project de Bruijn Technology.
                        Selama Lima Tuan and Siam Mimin Tim Yang Tuan Di Sapul Orang."
    bigmodel           "Cybergeeks. Baggy Manager. Cyborg Person. Technology and Cyborg."
    bigmodel_nostream  "Saya bergabung sebagai manager proyek di perusahaan technology
                        Salama Lima Town, dan saya merupakan tim yang terdekat..."

The obvious-looking fix is not the fix. A `language` field IS accepted on every one of
these endpoints and changes NOTHING on any of them: `id-ID`, `en-US` and omitting it
entirely returned byte-identical transcripts from each. What decides whether Indonesian
is understood is which model the path selects, so that is what `for_language()` picks.

`show_utterances` is required, not optional: the `definite` flag we key the whole turn
loop off only appears inside `utterances`, so without it the interview never detects
that the candidate stopped speaking.
"""

import asyncio
import contextlib
import json
import logging
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import websockets
from websockets.asyncio.client import ClientConnection

from app.core.config import settings

from . import protocol

log = logging.getLogger(__name__)

# The candidate's mic is downsampled to this in the browser before it is sent up. Seed
# ASR currently supports 16 kHz only.
SAMPLE_RATE = 16000

# Languages the streaming model actually recognises. Everything else has to go through
# the slower utterance-at-a-time model, so this set is the routing decision for the whole
# voice pipeline - see the module docstring for the measurements behind it.
#
# Written as an allow-list rather than `if language == "id"` deliberately: a fourth
# interview language added to the picker then has to be classified here on purpose,
# instead of silently defaulting into a recogniser that cannot hear it. That silent
# default is exactly how Indonesian interviews shipped deaf.
STREAMING_LANGUAGES = frozenset({"en", "zh"})

# The gateway ends a recognition session after 8 seconds with no audio packet
# (error 45000081, "waiting next packet timeout"), and it does not come back - the
# interview goes permanently deaf while every other signal still says healthy. Confirmed
# live, not assumed. Real gaps that long are easy to hit: the candidate listening to a
# long question, thinking before answering, or holding the mute button.
#
# So whenever the browser has not sent anything recently we send silence ourselves. The
# recogniser transcribes nothing from it, and the session stays open.
_KEEPALIVE_AFTER_SECONDS = 2.5
_KEEPALIVE_FRAME = b"\x00" * (SAMPLE_RATE // 10 * 2)  # 100 ms of 16-bit silence

# Documented ASR error codes worth naming in a log line rather than printing bare.
_ERROR_MEANINGS = {
    45000001: "invalid request parameters",
    45000002: "empty audio",
    45000081: "packet waiting timeout",
    45000151: "incorrect audio format",
    55000031: "server busy",
}


@dataclass(slots=True)
class Transcript:
    """One recognition result.

    `definite` marks an utterance the recogniser considers finished - the signal the
    turn loop waits for before answering. Non-definite results are interim captions.
    """

    text: str
    definite: bool


class SeedASRStream:
    """One recognition session, living as long as one interview.

    Audio goes in with `send_audio()`; results come out of `results()`. The two run
    concurrently - a reader task drains the socket into a queue so that a slow consumer
    never stalls audio upload, which the gateway would otherwise time out (45000081).
    """

    def __init__(self) -> None:
        self._ws: ClientConnection | None = None
        self._reader: asyncio.Task | None = None
        self._keepalive: asyncio.Task | None = None
        self._queue: asyncio.Queue[Transcript | None] = asyncio.Queue()
        self._closed = False
        # Guards the socket: the keepalive task and the audio path both write to it, and
        # interleaving two frames corrupts the stream for everything after them.
        self._send_lock = asyncio.Lock()
        self._last_sent = 0.0
        # Seed ASR returns the full transcript each time by default, so we only surface
        # what actually changed rather than re-emitting the same caption every packet.
        self._last_emitted = ""
        # Identities of definite utterances already handed downstream. Bounded by how
        # much the candidate actually says in one interview, so it does not need eviction.
        self._seen: set[tuple] = set()

    # ------------------------------------------------------------------ lifecycle

    async def open(self) -> None:
        url = f"wss://{settings.seed_speech_host}{settings.seed_asr_path}"
        headers = {
            "X-Api-Key": settings.seed_speech_api_key,
            "X-Api-Resource-Id": settings.seed_asr_resource_id,
            "X-Api-Connect-Id": _connect_id(),
        }
        self._ws = await websockets.connect(
            url,
            additional_headers=headers,
            # Audio flows continuously; a stalled ping response means the leg is dead and
            # we want to know rather than keep buffering into a socket nobody reads.
            ping_interval=20,
            ping_timeout=20,
            max_size=8 * 1024 * 1024,
        )
        log_id = self._ws.response.headers.get("X-Tt-Logid", "") if self._ws.response else ""
        log.info("Seed ASR connected (logid=%s)", log_id or "unknown")

        await self._ws.send(
            protocol.encode(
                protocol.CLIENT_FULL_REQUEST,
                json.dumps(_request_config()).encode("utf-8"),
                flags=protocol.FLAG_NONE,
                serialization=protocol.SERIAL_JSON,
            )
        )
        self._last_sent = time.monotonic()
        self._reader = asyncio.create_task(self._read_loop())
        self._keepalive = asyncio.create_task(self._keepalive_loop())

    async def close(self) -> None:
        """Send the final packet and tear the socket down."""
        if self._closed:
            return
        self._closed = True
        if self._keepalive is not None:
            self._keepalive.cancel()
        if self._ws is not None:
            try:
                # Under the lock: a keepalive frame may still be mid-send, and two frames
                # interleaved on the socket corrupt everything after them.
                async with self._send_lock:
                    # An empty last packet is what tells the recogniser to flush.
                    await self._ws.send(
                        protocol.encode(
                            protocol.CLIENT_AUDIO_ONLY,
                            b"",
                            flags=protocol.FLAG_LAST_PACKET,
                            serialization=protocol.SERIAL_RAW,
                        )
                    )
            except Exception:  # noqa: BLE001 - the socket may already be gone
                pass
            await self._ws.close()
        if self._reader is not None:
            self._reader.cancel()
        await self._queue.put(None)

    # -------------------------------------------------------------------- audio in

    async def send_audio(self, pcm: bytes) -> None:
        """Send one chunk of 16 kHz mono signed-16-bit PCM.

        Best practice per the API reference is 100-200 ms per packet; the browser side
        is what actually sets that cadence.
        """
        await self._send_audio_frame(pcm)

    async def end_utterance(self) -> None:
        """No-op: this model runs its own VAD and decides end-of-utterance itself.

        Present so the pipeline can pass the browser's end-of-speech cue to whichever
        recogniser it happens to be driving, without asking which one it has.
        """

    async def _send_audio_frame(self, pcm: bytes) -> None:
        if self._ws is None or self._closed:
            return
        async with self._send_lock:
            await self._ws.send(
                protocol.encode(
                    protocol.CLIENT_AUDIO_ONLY,
                    pcm,
                    flags=protocol.FLAG_NONE,
                    serialization=protocol.SERIAL_RAW,
                )
            )
            self._last_sent = time.monotonic()

    async def _keepalive_loop(self) -> None:
        """Keep the recognition session alive across gaps in the candidate's audio."""
        try:
            while not self._closed:
                await asyncio.sleep(_KEEPALIVE_AFTER_SECONDS / 2)
                idle = time.monotonic() - self._last_sent
                if idle >= _KEEPALIVE_AFTER_SECONDS:
                    await self._send_audio_frame(_KEEPALIVE_FRAME)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Seed ASR keepalive stopped")

    # ------------------------------------------------------------------ results out

    async def results(self) -> AsyncIterator[Transcript]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if not isinstance(raw, bytes):
                    continue
                try:
                    frame = protocol.decode(raw)
                except ValueError:
                    log.exception("Undecodable Seed ASR frame")
                    continue

                if frame.is_error:
                    meaning = _ERROR_MEANINGS.get(frame.error_code or 0, "unknown error")
                    log.error(
                        "Seed ASR error %s (%s): %s",
                        frame.error_code,
                        meaning,
                        frame.text()[:300],
                    )
                    continue

                for transcript in self._extract(frame):
                    await self._queue.put(transcript)
        except asyncio.CancelledError:
            raise
        except websockets.exceptions.ConnectionClosed:
            log.info("Seed ASR connection closed")
        except Exception:  # noqa: BLE001
            log.exception("Seed ASR reader failed")
        finally:
            await self._queue.put(None)

    def _extract(self, frame: protocol.Frame) -> list[Transcript]:
        """Pull newly-finished transcripts out of one server frame.

        The word "newly" is the whole job here. Seed ASR reports the recognition state
        of the session, not a queue of events: every result frame carries the utterances
        recognised SO FAR, so a definite utterance reappears in each subsequent frame for
        the rest of the interview. Emitting whatever the frame contains therefore replays
        the same finished sentence forever - which downstream reads as the candidate
        saying it again, and again, recording a turn and cancelling the interviewer's
        reply each time. That is exactly the failure this guards against, and it is why
        every definite utterance is checked against `_seen` before it is emitted.

        `start_time`/`end_time` are what make an utterance identifiable: the same words
        said twice are two utterances at different offsets and must both count, while the
        same utterance redelivered keeps its offsets and must not.
        """
        try:
            body = frame.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return []

        result = body.get("result") or {}
        if isinstance(result, list):  # some builds wrap it in a list
            result = result[0] if result else {}

        out: list[Transcript] = []
        for utterance in result.get("utterances") or []:
            if not utterance.get("definite"):
                continue
            text = (utterance.get("text") or "").strip()
            if not text:
                continue
            key = (utterance.get("start_time"), utterance.get("end_time"), text)
            if key in self._seen:
                continue
            self._seen.add(key)
            out.append(Transcript(text=text, definite=True))

        if out:
            self._last_emitted = ""
            return out

        interim = (result.get("text") or "").strip()
        if interim and interim != self._last_emitted:
            self._last_emitted = interim
            return [Transcript(text=interim, definite=False)]
        return []


def _request_config() -> dict:
    """The full client request that opens a recognition session."""
    return {
        "user": {"uid": "ai-interviewer"},
        "audio": {
            "format": "pcm",
            "codec": "raw",
            "rate": SAMPLE_RATE,
            "bits": 16,
            "channel": 1,
        },
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
            "enable_ddc": True,
            # Required: `definite` - our end-of-turn signal - only appears inside
            # `utterances`, which this switch is what produces.
            "show_utterances": True,
            # Dual-pass: stream interim text for captions, then re-recognise each
            # finished utterance with the non-streaming model for the text we actually
            # answer. Only supported on bigmodel_async.
            "enable_nonstream": True,
            # "single" = incremental: the server stops re-sending sentences it has
            # already finalised. `_extract` de-duplicates regardless (the two mechanisms
            # are belt and braces, because replaying a finished sentence corrupts the
            # transcript AND stops the interviewer from ever replying), but this keeps
            # the frames small and the interim caption scoped to the current sentence
            # instead of growing to the length of the whole interview.
            "result_type": "single",
            # Silence (ms) that ends an utterance. 800 is the documented default; it is
            # the main knob for how long the interviewer waits before replying.
            "end_window_size": 800,
        },
    }


def _connect_id() -> str:
    import uuid

    return str(uuid.uuid4())


# ---------------------------------------------------------------- utterance mode

# How long a stretch of silence ends an utterance when the browser's own end-of-speech
# message never arrives. The gate in `micWorklet.js` is the primary signal and it holds
# audio open for 1.4 s after speech stops, so this only fires when that message was lost
# - a dropped control frame must not leave the candidate permanently unheard.
_IDLE_FINALISE_SECONDS = 2.0

# A single recognition pass covers the whole utterance, so its cost grows with the
# utterance: measured tail latency after the last packet was ~0.3 s at 4 s of audio,
# ~1.5 s at 8 s, ~2.0 s at 24 s and ~2.9 s at 48 s. Past about a minute that wait stops
# reading as a pause and starts reading as a hang, so a monologue is cut here and
# recognised in pieces. The pipeline rejoins them before answering, so the candidate
# loses nothing but the seam.
_MAX_UTTERANCE_SECONDS = 60.0

# Ceiling on how long to wait for the final result after the last packet, scaled from the
# measurements above with room to spare. Bounded so a gateway that simply stops replying
# fails in seconds rather than hanging the turn loop forever.
_FINALISE_TIMEOUT_FLOOR = 6.0
_FINALISE_TIMEOUT_CEILING = 25.0


class _UtteranceSession:
    """One `bigmodel_nostream` connection, covering exactly one stretch of speech.

    The connection is the unit of recognition here: this model reports nothing until it
    is told the audio has ended, so an utterance and a socket have the same lifetime.
    """

    def __init__(self) -> None:
        self._ws: ClientConnection | None = None
        self._reader: asyncio.Task | None = None
        self._latest = ""
        self._sent_seconds = 0.0
        self._interim: Callable[[str], None] | None = None

    @property
    def sent_seconds(self) -> float:
        return self._sent_seconds

    async def open(self, on_interim: Callable[[str], None]) -> None:
        self._interim = on_interim
        url = f"wss://{settings.seed_speech_host}{settings.seed_asr_utterance_path}"
        headers = {
            "X-Api-Key": settings.seed_speech_api_key,
            "X-Api-Resource-Id": settings.seed_asr_resource_id,
            "X-Api-Connect-Id": _connect_id(),
        }
        self._ws = await websockets.connect(
            url,
            additional_headers=headers,
            ping_interval=20,
            ping_timeout=20,
            max_size=8 * 1024 * 1024,
        )
        await self._ws.send(
            protocol.encode(
                protocol.CLIENT_FULL_REQUEST,
                json.dumps(_utterance_request_config()).encode("utf-8"),
                flags=protocol.FLAG_NONE,
                serialization=protocol.SERIAL_JSON,
            )
        )
        self._reader = asyncio.create_task(self._read_loop())

    async def send(self, pcm: bytes) -> None:
        if self._ws is None:
            return
        await self._ws.send(
            protocol.encode(
                protocol.CLIENT_AUDIO_ONLY,
                pcm,
                flags=protocol.FLAG_NONE,
                serialization=protocol.SERIAL_RAW,
            )
        )
        self._sent_seconds += len(pcm) / (SAMPLE_RATE * 2)

    async def finish(self) -> str:
        """Close the utterance and return everything the model heard in it.

        The empty final packet is what triggers recognition at all - without it this
        model waits forever and the candidate is never answered.
        """
        if self._ws is None:
            return ""
        try:
            await self._ws.send(
                protocol.encode(
                    protocol.CLIENT_AUDIO_ONLY,
                    b"",
                    flags=protocol.FLAG_LAST_PACKET,
                    serialization=protocol.SERIAL_RAW,
                )
            )
        except Exception:  # noqa: BLE001 - the socket may already be gone
            log.debug("Utterance socket closed before its last packet", exc_info=True)

        # The reader ends when the gateway closes the connection, which it does right
        # after delivering the final result.
        budget = min(
            _FINALISE_TIMEOUT_CEILING,
            max(_FINALISE_TIMEOUT_FLOOR, self._sent_seconds * 0.3),
        )
        if self._reader is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._reader), timeout=budget)
            except TimeoutError:
                log.warning(
                    "Seed ASR did not finalise %.1fs of audio within %.1fs; using %d chars",
                    self._sent_seconds,
                    budget,
                    len(self._latest),
                )
            except Exception:  # noqa: BLE001
                log.exception("Utterance reader failed")
            self._reader.cancel()
        await self._close_socket()
        return self._latest.strip()

    async def abandon(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
        await self._close_socket()

    async def _close_socket(self) -> None:
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None

    async def _read_loop(self) -> None:
        """Track the best transcript this session has produced.

        Results are cumulative rather than incremental on this model - each one restates
        the whole utterance so far - so the last non-empty result is the complete one and
        there is nothing to stitch together.
        """
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if not isinstance(raw, bytes):
                    continue
                try:
                    frame = protocol.decode(raw)
                except ValueError:
                    log.exception("Undecodable Seed ASR frame")
                    continue
                if frame.is_error:
                    meaning = _ERROR_MEANINGS.get(frame.error_code or 0, "unknown error")
                    log.error(
                        "Seed ASR error %s (%s): %s",
                        frame.error_code,
                        meaning,
                        frame.text()[:300],
                    )
                    return
                text = _result_text(frame)
                if text and text != self._latest:
                    self._latest = text
                    # This model only volunteers a progress result about every 23 s of
                    # audio, so these are far too sparse to read as live captions - but
                    # on a long answer they are the difference between a transcript that
                    # looks slow and one that looks broken.
                    if self._interim is not None:
                        self._interim(text)
        except asyncio.CancelledError:
            raise
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception:  # noqa: BLE001
            log.exception("Seed ASR utterance reader failed")


class UtteranceASRStream:
    """Recognition for languages the streaming model cannot hear.

    Presents the same surface as `SeedASRStream` - `open`, `send_audio`, `results`,
    `close` - so the pipeline drives either one without knowing which it has. The one
    addition is `end_utterance()`, because this model cannot work out for itself when the
    candidate stopped talking; that signal comes from the gate in the browser's capture
    worklet, which already computes it in order to decide what to send.

    Utterances are finalised concurrently but published in the order they were spoken. A
    long answer followed by a short one would otherwise land backwards in the transcript,
    since the wait for a result scales with how much was said.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Transcript | None] = asyncio.Queue()
        self._closed = False
        self._session: _UtteranceSession | None = None
        self._inflight: deque[asyncio.Task[str]] = deque()
        self._drainer: asyncio.Task | None = None
        self._idle_watch: asyncio.Task | None = None
        self._last_audio = 0.0
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ lifecycle

    async def open(self) -> None:
        """Prove the credentials before the interview starts.

        Sessions are per utterance, so there is nothing to hold open - but discovering a
        bad key at the candidate's first word means discovering it as silence, which is
        the failure mode this codebase keeps paying for. One throwaway connection now
        turns that into a startup error.
        """
        probe = _UtteranceSession()
        await probe.open(lambda _text: None)
        await probe.abandon()
        log.info("Seed ASR ready (utterance mode, %s)", settings.seed_asr_utterance_path)
        self._drainer = asyncio.create_task(self._drain_loop())
        self._idle_watch = asyncio.create_task(self._idle_loop())

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._idle_watch is not None:
            self._idle_watch.cancel()
        async with self._lock:
            if self._session is not None:
                await self._session.abandon()
                self._session = None
        for task in self._inflight:
            task.cancel()
        self._inflight.clear()
        if self._drainer is not None:
            self._drainer.cancel()
        await self._queue.put(None)

    # -------------------------------------------------------------------- audio in

    async def send_audio(self, pcm: bytes) -> None:
        """Feed one chunk of candidate audio, opening a session if none is running.

        Audio arriving IS the start of an utterance: the browser's gate only forwards
        packets it judged to be speech, so there is no separate begin signal to wait for.
        """
        if self._closed:
            return
        async with self._lock:
            if self._session is None:
                self._session = _UtteranceSession()
                try:
                    await self._session.open(self._publish_interim)
                except Exception:  # noqa: BLE001
                    self._session = None
                    log.exception("Could not open a Seed ASR utterance session")
                    return
            self._last_audio = time.monotonic()
            try:
                await self._session.send(pcm)
            except Exception:  # noqa: BLE001
                log.exception("Dropping a Seed ASR utterance whose socket failed")
                await self._session.abandon()
                self._session = None
                return
            if self._session.sent_seconds >= _MAX_UTTERANCE_SECONDS:
                log.info("Cutting a long answer at %.0fs", _MAX_UTTERANCE_SECONDS)
                self._finalise_locked()

    async def end_utterance(self) -> None:
        """The candidate stopped speaking; recognise what they said."""
        if self._closed:
            return
        async with self._lock:
            self._finalise_locked()

    def _finalise_locked(self) -> None:
        """Hand the running session off to be recognised. Caller holds `_lock`.

        Deliberately does not await the result: the next utterance must be able to open
        its own socket while this one is still resolving, or a quick follow-up sentence
        is lost during the wait.
        """
        session, self._session = self._session, None
        if session is None:
            return
        self._inflight.append(asyncio.create_task(session.finish()))

    async def _idle_loop(self) -> None:
        """Finalise on silence, in case the browser's end-of-speech message went missing."""
        try:
            while not self._closed:
                await asyncio.sleep(_IDLE_FINALISE_SECONDS / 2)
                async with self._lock:
                    if self._session is None:
                        continue
                    if time.monotonic() - self._last_audio >= _IDLE_FINALISE_SECONDS:
                        log.debug("Finalising an utterance on silence, not on a cue")
                        self._finalise_locked()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Seed ASR idle watch stopped")

    # ------------------------------------------------------------------ results out

    def _publish_interim(self, text: str) -> None:
        self._queue.put_nowait(Transcript(text=text, definite=False))

    async def _drain_loop(self) -> None:
        """Publish finished utterances in the order they were spoken."""
        try:
            while not self._closed:
                if not self._inflight:
                    await asyncio.sleep(0.05)
                    continue
                task = self._inflight[0]
                try:
                    text = await task
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    log.exception("An utterance failed to recognise")
                    text = ""
                finally:
                    if self._inflight and self._inflight[0] is task:
                        self._inflight.popleft()
                if text:
                    await self._queue.put(Transcript(text=text, definite=True))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Seed ASR drain loop stopped")
        finally:
            await self._queue.put(None)

    async def results(self) -> AsyncIterator[Transcript]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item


def _result_text(frame: protocol.Frame) -> str:
    try:
        body = frame.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ""
    result = body.get("result") or {}
    if isinstance(result, list):
        result = result[0] if result else {}
    return (result.get("text") or "").strip()


def _utterance_request_config() -> dict:
    """The request that opens one non-streaming recognition.

    No `language` key on purpose. It is accepted here and ignored - `id-ID`, `en-US` and
    omitting it were measured to give byte-identical transcripts - so setting it would
    only imply a control we do not have.
    """
    return {
        "user": {"uid": "ai-interviewer"},
        "audio": {
            "format": "pcm",
            "codec": "raw",
            "rate": SAMPLE_RATE,
            "bits": 16,
            "channel": 1,
        },
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
            "show_utterances": True,
        },
    }


def for_language(language: str) -> "SeedASRStream | UtteranceASRStream":
    """Pick the recogniser that can actually hear this interview.

    The whole reason this function exists: `interview.language` used to reach the LLM,
    the planner and the prompts but never the recogniser, so an Indonesian interview
    listened in Mandarin and English and transcribed the candidate as nonsense.
    """
    if language in STREAMING_LANGUAGES:
        return SeedASRStream()
    log.info("Interview language %r needs utterance-mode recognition", language)
    return UtteranceASRStream()
