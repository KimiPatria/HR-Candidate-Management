"""Seed TTS streaming client - interviewer text in, speech audio out.

Bidirectional streaming, so text can be pushed in sentence by sentence as the LLM
produces it while audio for the earlier sentences is already coming back. That overlap
is what keeps the interviewer's reply from starting several seconds after it was
decided.

Connection lifecycle (event-driven, on top of the shared binary framing):

    connect  ->  StartConnection(1)   ->  ConnectionStarted(50)
    per reply:   StartSession(100)    ->  SessionStarted(150)
                 TaskRequest(200) xN  ->  TTSSentenceStart(350)
                                          TTSResponse(352) xN   <- audio frames
                                          TTSSentenceEnd(351)
                 FinishSession(102)   ->  SessionFinished(152)
    teardown ->  FinishConnection(2)  ->  ConnectionFinished(52)

The connection is held open for the whole interview and a fresh session is started per
reply, which is what the protocol is shaped for - reconnecting per utterance would add a
TLS handshake to every turn.

CONFIRMED LIVE against voice.ap-southeast-1.bytepluses.com, not inferred: a real
`scripts/probe_speech.py` run walked 1 -> 50, 100 -> 150, 200 -> 350, nine 352 audio
frames (message type 0b1011, 153,810 bytes of 24 kHz PCM), 351 carrying the full
sentence text, 102 -> 152. Feeding that audio straight back into Seed ASR returned the
sentence with `definite: true`, so the event numbers, the `req_params` shape and the
framing in `protocol.py` are all verified end to end. Re-run that probe after any change
here - it is the cheapest way to catch a framing regression, which otherwise presents as
an interviewer that has simply gone quiet.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator

import websockets
from websockets.asyncio.client import ClientConnection

from app.core.config import settings

from . import protocol

log = logging.getLogger(__name__)

NAMESPACE = "BidirectionalTTS"

# Client events.
EVENT_START_CONNECTION = 1
EVENT_FINISH_CONNECTION = 2
EVENT_START_SESSION = 100
EVENT_FINISH_SESSION = 102
EVENT_TASK_REQUEST = 200

# Server events.
EVENT_CONNECTION_STARTED = 50
EVENT_CONNECTION_FAILED = 51
EVENT_CONNECTION_FINISHED = 52
EVENT_SESSION_STARTED = 150
EVENT_SESSION_FINISHED = 152
EVENT_SESSION_FAILED = 153
EVENT_SENTENCE_START = 350
EVENT_SENTENCE_END = 351
EVENT_TTS_RESPONSE = 352
EVENT_TTS_ENDED = 359

_TERMINAL_EVENTS = frozenset(
    {EVENT_SESSION_FINISHED, EVENT_SESSION_FAILED, EVENT_TTS_ENDED}
)

# How long to wait for an abandoned session to wind itself up. Bounded because a stuck
# session must not hold up the next reply indefinitely - better one refused reply than an
# interview frozen mid-turn.
_DRAIN_TIMEOUT_SECONDS = 5.0


class SeedTTSStream:
    """A held-open TTS connection. One `speak()` call per interviewer reply.

    The connection is multiplexed: it outlives every reply, and several sessions can have
    frames in flight at once - most obviously when the candidate interrupts, because the
    abandoned reply keeps emitting audio, a terminal event, and sometimes an error long
    after the next reply has started.

    So exactly ONE task reads the socket and routes each frame to the session it belongs
    to. Letting each `speak()` call read the socket directly (the obvious design, and the
    one this started as) means concurrent readers racing for frames: whoever happens to
    read first consumes a frame meant for the other. In practice that made an interrupted
    reply poison the next one - its terminal event ended the new reply before any audio
    was heard, or its error frame aborted the new session's handshake outright with
    "rpc error: the stream is done". Both present to the candidate as the interviewer
    going mute at random while the transcript shows a reply they never heard.
    """

    def __init__(self, voice_id: str) -> None:
        self._voice_id = voice_id
        self._ws: ClientConnection | None = None
        self._closed = False
        self._reader: asyncio.Task | None = None
        # Per-session inboxes, plus one for connection-scoped frames (and anything that
        # arrives for a session nobody is listening to any more).
        self._sessions: dict[str, asyncio.Queue] = {}
        self._connection_frames: asyncio.Queue = asyncio.Queue()
        # Sessions already told to finish. Both the text pump (normal end of reply) and
        # `speak`'s teardown (barge-in) need to send FinishSession, and whichever gets
        # there second would otherwise draw an "the stream is done" error frame.
        self._finished: set[str] = set()

    async def open(self) -> None:
        url = f"wss://{settings.seed_speech_host}{settings.seed_tts_path}"
        headers = {
            "X-Api-Key": settings.seed_speech_api_key,
            "X-Api-Resource-Id": settings.seed_tts_resource_id,
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }
        self._ws = await websockets.connect(
            url,
            additional_headers=headers,
            ping_interval=20,
            ping_timeout=20,
            max_size=8 * 1024 * 1024,
        )
        log_id = self._ws.response.headers.get("X-Tt-Logid", "") if self._ws.response else ""
        log.info("Seed TTS connected (logid=%s)", log_id or "unknown")

        self._reader = asyncio.create_task(self._read_loop())
        await self._send(EVENT_START_CONNECTION, {})
        frame = await self._expect({EVENT_CONNECTION_STARTED, EVENT_CONNECTION_FAILED})
        if frame.event == EVENT_CONNECTION_FAILED:
            raise RuntimeError(f"Seed TTS refused the connection: {frame.text()[:300]}")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._reader is not None:
            self._reader.cancel()
        if self._ws is None:
            return
        try:
            await self._send(EVENT_FINISH_CONNECTION, {})
        except Exception:  # noqa: BLE001 - already gone is fine
            pass
        await self._ws.close()

    async def _read_loop(self) -> None:
        """Sole reader of the socket. Routes each frame to the session that owns it."""
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if not isinstance(raw, bytes):
                    continue
                try:
                    frame = protocol.decode(raw)
                except ValueError:
                    log.exception("Undecodable Seed TTS frame")
                    continue
                inbox = self._sessions.get(frame.session_id or "")
                if inbox is not None:
                    inbox.put_nowait(frame)
                elif frame.is_error and not frame.session_id:
                    # A connection-scoped error belongs to whoever is mid-request. Left
                    # in the connection queue - which only the opening handshake ever
                    # reads - it would strand every waiting reply in silence forever, so
                    # it goes to all of them and fails them fast instead.
                    log.error(
                        "Seed TTS connection error %s: %s",
                        frame.error_code,
                        frame.text()[:300],
                    )
                    for waiting in list(self._sessions.values()):
                        waiting.put_nowait(frame)
                    self._connection_frames.put_nowait(frame)
                elif frame.session_id:
                    # A session that has been abandoned (barge-in) or already finished.
                    # Dropping these is the point: they must not reach anyone else.
                    log.debug(
                        "Discarding Seed TTS frame for finished session %s (event=%s)",
                        frame.session_id[:8],
                        frame.event,
                    )
                else:
                    self._connection_frames.put_nowait(frame)
        except asyncio.CancelledError:
            raise
        except websockets.exceptions.ConnectionClosed:
            log.info("Seed TTS connection closed")
        except Exception:  # noqa: BLE001
            log.exception("Seed TTS reader failed")
        finally:
            # Unblock anyone waiting, so a dropped connection surfaces as a failed reply
            # rather than an interview that hangs forever mid-sentence.
            for inbox in list(self._sessions.values()):
                inbox.put_nowait(None)
            self._connection_frames.put_nowait(None)

    async def speak(self, text_chunks: AsyncIterator[str]) -> AsyncIterator[bytes]:
        """Synthesise a reply, streaming text in and audio out at the same time.

        `text_chunks` is the LLM's token stream. It is buffered into sentences before
        being sent, because handing the synthesiser two-word fragments produces audibly
        choppy prosody - it needs a clause to decide intonation over.
        """
        if self._ws is None or self._closed:
            return

        session_id = str(uuid.uuid4())
        # Register the inbox BEFORE announcing the session, or its first frames arrive
        # with nowhere to go and get discarded as belonging to nobody.
        inbox: asyncio.Queue = asyncio.Queue()
        self._sessions[session_id] = inbox

        pump: asyncio.Task | None = None
        ran_to_completion = False
        try:
            # Wait for something worth saying BEFORE opening the session.
            #
            # A TTS session that is started and then left idle is abandoned by the
            # server, and it says so only by going quiet: no audio, no error, nothing in
            # the logs. Measured against the live service, a session still synthesises
            # after 7s of idle and produces silence after 10s.
            #
            # That window sits right on top of the model's time-to-first-token, measured
            # at 4-9s and varying turn to turn. So opening the session first and waiting
            # for the model second loses the race whenever the model happens to be slow:
            # the reply is written, the transcript records it in full - the text was
            # consumed on its way to a synthesiser that had already hung up - and the
            # candidate hears nothing. Intermittent, and never the same turn twice.
            #
            # Buffering here moves the entire wait to before the session exists, so a
            # session is only ever opened when there is already a fragment to send.
            first, remainder = await self._first_fragment(text_chunks)
            if first is None:
                log.warning("Seed TTS asked to speak an empty reply; nothing to say")
                return  # the model produced nothing at all
            log.debug("Seed TTS session %s opening for %r", session_id[:8], first[:60])

            await self._send(
                EVENT_START_SESSION, self._session_params(), session_id=session_id
            )
            frame = await self._await_session(inbox, {EVENT_SESSION_STARTED, EVENT_SESSION_FAILED})
            if frame is None or frame.event == EVENT_SESSION_FAILED:
                detail = frame.text()[:300] if frame else "connection closed"
                raise RuntimeError(f"Seed TTS refused the session: {detail}")

            # Push the rest in the background so audio flows back while the model is
            # still writing - the whole point of the bidirectional leg.
            pump = asyncio.create_task(
                self._pump_text(text_chunks, session_id, first=first, buffered=remainder)
            )
            async for audio in self._audio_until_done(inbox):
                yield audio
            ran_to_completion = True
        finally:
            if pump is not None:
                pump.cancel()
            # A session abandoned mid-stream - which is exactly what a barge-in does -
            # leaves the connection part-way through a reply, and the server then refuses
            # the NEXT StartSession outright ("rpc error: the stream is done"). Since the
            # connection is shared for the whole interview, that one interruption makes
            # every later reply silent. So an abandoned session is finished and read out
            # to its terminal event before anyone starts another, rather than just
            # dropped. This runs while the candidate is still speaking, so the wait costs
            # nothing the candidate can perceive.
            await self._finish_session(session_id)
            if not ran_to_completion:
                await self._drain(session_id, inbox)
            self._sessions.pop(session_id, None)
            self._finished.discard(session_id)

    async def _drain(self, session_id: str, inbox: asyncio.Queue) -> None:
        """Read an abandoned session out to its terminal event."""
        try:
            async with asyncio.timeout(_DRAIN_TIMEOUT_SECONDS):
                while True:
                    frame = await inbox.get()
                    if frame is None or frame.is_error or frame.event in _TERMINAL_EVENTS:
                        return
        except TimeoutError:
            log.warning(
                "Seed TTS session %s did not finish within %.1fs of being abandoned; "
                "the next reply may be refused",
                session_id[:8],
                _DRAIN_TIMEOUT_SECONDS,
            )

    # ------------------------------------------------------------------- internals

    async def _first_fragment(self, chunks: AsyncIterator[str]) -> tuple[str | None, str]:
        """Consume the model's stream until there is a first fragment worth speaking.

        Returns that fragment and whatever was buffered past it. `(None, "")` means the
        model produced nothing.
        """
        buffer = ""
        async for piece in chunks:
            buffer += piece
            cut = _sentence_break(buffer, first=True)
            if cut is not None:
                return buffer[:cut].strip(), buffer[cut:]
        return (buffer.strip() or None), ""

    async def _pump_text(
        self,
        chunks: AsyncIterator[str],
        session_id: str,
        *,
        first: str,
        buffered: str,
    ) -> None:
        buffer = buffered
        try:
            await self._send(
                EVENT_TASK_REQUEST, self._task_params(first), session_id=session_id
            )
            async for piece in chunks:
                buffer += piece
                while (cut := _sentence_break(buffer)) is not None:
                    sentence, buffer = buffer[:cut].strip(), buffer[cut:]
                    if sentence:
                        await self._send(
                            EVENT_TASK_REQUEST,
                            self._task_params(sentence),
                            session_id=session_id,
                        )
            if buffer.strip():
                await self._send(
                    EVENT_TASK_REQUEST,
                    self._task_params(buffer.strip()),
                    session_id=session_id,
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Seed TTS text pump failed")
        finally:
            # FinishSession is what makes the synthesiser flush and emit its terminal
            # event; without it `_audio_until_done` waits forever on a finished reply.
            # It must happen here rather than in `speak`'s teardown, which does not run
            # until the audio loop has already returned.
            await self._finish_session(session_id)

    async def _finish_session(self, session_id: str) -> None:
        """Tell the synthesiser this reply is complete. Safe to call more than once."""
        if session_id in self._finished or self._ws is None or self._closed:
            return
        self._finished.add(session_id)
        try:
            await self._send(EVENT_FINISH_SESSION, {}, session_id=session_id)
        except Exception:  # noqa: BLE001 - best effort on a teardown path
            pass

    async def _audio_until_done(self, inbox: asyncio.Queue) -> AsyncIterator[bytes]:
        """Yield this session's audio until it reports itself finished."""
        while True:
            frame = await inbox.get()
            if frame is None:  # connection went away
                return
            if frame.is_error:
                log.error("Seed TTS error %s: %s", frame.error_code, frame.text()[:300])
                return
            if frame.event == EVENT_TTS_RESPONSE or frame.is_audio:
                if frame.payload:
                    yield frame.payload
            elif frame.event in _TERMINAL_EVENTS:
                if frame.event == EVENT_SESSION_FAILED:
                    log.error("Seed TTS session failed: %s", frame.text()[:300])
                return

    async def _await_session(
        self, inbox: asyncio.Queue, events: set[int]
    ) -> protocol.Frame | None:
        """Wait for one of this session's handshake events. None if the socket died."""
        while True:
            frame = await inbox.get()
            if frame is None:
                return None
            if frame.is_error:
                raise RuntimeError(
                    f"Seed TTS error {frame.error_code}: {frame.text()[:300]}"
                )
            if frame.event in events:
                return frame

    async def _send(self, event: int, params: dict, *, session_id: str | None = None) -> None:
        assert self._ws is not None
        payload = {"event": event, "namespace": NAMESPACE, **params}
        await self._ws.send(
            protocol.encode(
                protocol.CLIENT_FULL_REQUEST,
                json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                flags=protocol.FLAG_WITH_EVENT,
                serialization=protocol.SERIAL_JSON,
                event=event,
                session_id=session_id,
            )
        )

    async def _expect(self, events: set[int]) -> protocol.Frame:
        """Wait for a connection-scoped event (not tied to any one reply)."""
        while True:
            frame = await self._connection_frames.get()
            if frame is None:
                raise RuntimeError("Seed TTS closed before answering the handshake")
            if frame.is_error:
                raise RuntimeError(
                    f"Seed TTS error {frame.error_code}: {frame.text()[:300]}"
                )
            if frame.event in events:
                return frame

    def _session_params(self) -> dict:
        return {
            "req_params": {
                "speaker": self._voice_id,
                "audio_params": {
                    # PCM keeps the browser side decoder-free.
                    "format": "pcm",
                    "sample_rate": settings.seed_tts_sample_rate,
                },
            }
        }

    def _task_params(self, text: str) -> dict:
        params = self._session_params()
        params["req_params"]["text"] = text
        return params


# Sentence-ish boundaries. Kept deliberately simple: the cost of an occasional early cut
# is slightly odd phrasing, while the cost of never cutting is the candidate waiting for
# the whole reply to be written before hearing any of it.
_BREAKS = ".!?。！？\n"

# Clause boundaries, used ONLY for the first fragment of a reply. Everything the
# candidate experiences as "the interviewer is slow" accumulates before the first sound:
# the end-of-turn pause, then several seconds of model latency, and then - if we insist
# on a whole sentence - however long the model takes to reach a full stop. Letting the
# opening clause go as soon as it is long enough to carry its own intonation buys back
# that last stretch. Later fragments keep waiting for real sentence ends, where prosody
# matters more and nobody is counting the seconds.
_CLAUSE_BREAKS = ",;:，；："
_FIRST_CLAUSE_MIN_CHARS = 45


def _sentence_break(buffer: str, *, first: bool = False) -> int | None:
    """Index just past the first usable break in `buffer`, or None if there isn't one."""
    for index, char in enumerate(buffer):
        if char in _BREAKS:
            return index + 1
    if first:
        for index, char in enumerate(buffer):
            if char in _CLAUSE_BREAKS and index + 1 >= _FIRST_CLAUSE_MIN_CHARS:
                return index + 1
    return None
