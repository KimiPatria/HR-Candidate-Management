"""The local turn loop: ASR -> interview brain -> TTS.

This is the piece RTC's managed agent was doing for us. It owns nothing about *how* the
interview thinks - that is `services/interview_turn.py`, shared byte-for-byte with the
RTC path - only the sequencing of listen, decide, speak.

    candidate audio -> Seed ASR -> definite utterance
                                        |
                                        v
                             interview_turn.compose()
                                        |
                                        v
                       token stream -> Seed TTS -> audio to the browser

Two behaviours here are worth knowing about because they are what make it feel like a
conversation rather than a walkie-talkie:

  Barge-in. If the candidate starts a new utterance while the interviewer is still
  talking, the in-flight reply is cancelled and the browser is told to drop whatever
  audio it has buffered. Without this, interrupting produces two voices talking over
  each other and a reply to a question the candidate already moved past.

  Echo. The mic hears the interviewer through the candidate's speakers, and Seed ASR
  will happily transcribe it - so the interview starts answering itself. The browser
  enables acoustic echo cancellation on capture, which is what actually prevents this;
  it is called out here because the failure mode looks like an ASR bug, not an audio
  routing one.

This loop is also the only place that knows what the interviewer is doing at any moment,
so it narrates itself onto the transcript channel as `agent_status` events. A silence in
a voice interview is ambiguous - thinking, a stalled network, and a dead microphone all
look identical from the candidate's chair - and telling them which one it is costs one
dict per stage.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from app.core.db import SessionLocal
from app.models import Interview, InterviewSession
from app.services import interview_turn, transcript_hub, turns

from .asr import STREAMING_LANGUAGES, Transcript, for_language
from .tts import SeedTTSStream

log = logging.getLogger(__name__)

# Utterances shorter than this are almost always echo fragments, throat-clearing, or a
# stray "mm" - answering them derails the interview more often than ignoring them.
_MIN_UTTERANCE_CHARS = 2

# Interrupting the interviewer takes more than this. While it is speaking, its own voice
# leaks back through the candidate's speakers into the microphone, and short recognitions
# during that window are far more often that echo than a real interruption. Treating them
# as barge-in cancels the reply mid-sentence and hands the model its own words as the
# candidate's answer - an interview that talks over itself and then answers itself. A
# candidate genuinely cutting in says more than a few characters.
_MIN_BARGE_IN_CHARS = 12

# How long to wait after a finished sentence before deciding the candidate has finished
# ANSWERING. The recogniser finalises per sentence, but people answer in several: "I led
# the migration. It took about six months. We came in under budget." Replying to the
# first of those talks over the second, and asks the next question against a third of the
# answer. So sentences are collected and the turn is only taken once the candidate has
# actually stopped. This stacks on top of the recogniser's own 800 ms end-of-sentence
# window, so the real pause before the interviewer speaks is about two seconds - close to
# the beat a human interviewer leaves.
_END_OF_TURN_SECONDS = 1.2

# The same wait, for interviews running on the utterance-at-a-time recogniser. It is much
# shorter because the pause has already been paid for twice over by the time a transcript
# arrives: the browser's capture gate holds the microphone open for 1.4 s after speech
# stops before declaring the utterance over, and recognition of the whole utterance then
# takes up to ~3 s on a long answer. Leaving the full 1.2 s on top of that is dead air the
# candidate reads as a hang. What remains is only there to join an answer the gate split
# across a long mid-sentence pause.
_END_OF_TURN_SECONDS_UTTERANCE = 0.4

# How long the candidate has to keep talking over the interviewer before it counts as an
# interruption rather than as their own voice leaking back through the speakers.
#
# Only used on the utterance path, and only because the usual defence is unavailable
# there. Normally barge-in triggers on a finished utterance, and short ones are ignored as
# echo (see _MIN_BARGE_IN_CHARS) - but a recogniser that says nothing until the speaker
# stops cannot report an interruption while it is still happening, so interrupting would
# do nothing until the candidate had already finished. Sustained speech is the signal
# available in time; echo cancellation kills the leak, so anything still open this long
# after is the candidate.
_BARGE_IN_HOLD_SECONDS = 0.6


class LocalVoicePipeline:
    """One running interview. Constructed per candidate WebSocket."""

    def __init__(
        self,
        session: InterviewSession,
        interview: Interview,
        *,
        send_audio: Callable[[bytes], Awaitable[None]],
        send_event: Callable[[dict], Awaitable[None]],
    ) -> None:
        self._session = session
        self._interview = interview
        self._send_audio = send_audio
        self._send_event = send_event

        # Which recogniser this interview needs is decided by its language, because the
        # streaming model cannot hear all of the languages the interview picker offers.
        self._asr = for_language(interview.language)
        self._streaming_asr = interview.language in STREAMING_LANGUAGES
        self._end_of_turn = (
            _END_OF_TURN_SECONDS if self._streaming_asr else _END_OF_TURN_SECONDS_UTTERANCE
        )
        self._tts = SeedTTSStream(interview.voice_id or _default_voice())
        self._speaking: asyncio.Task | None = None
        self._stopped = False
        # Sentences heard since the candidate started their current answer, and the timer
        # that decides they have finished it. See _END_OF_TURN_SECONDS.
        self._pending: list[str] = []
        self._turn_timer: asyncio.Task | None = None
        # Utterance path only: the candidate's microphone gate is open, and the timer that
        # decides they are genuinely interrupting rather than echoing. See
        # _BARGE_IN_HOLD_SECONDS.
        self._barge_in_timer: asyncio.Task | None = None

    # ------------------------------------------------------------------ lifecycle

    async def start(self, greeting: str) -> None:
        """Open both speech legs and deliver the opening greeting."""
        await self._asr.open()
        await self._tts.open()
        await self._send_event({"type": "ready"})
        if greeting:
            self._speak(_once(greeting), candidate_text="", record=False)
        else:
            self._status("listening")

    async def run(self) -> None:
        """Consume ASR results until the socket or the recogniser closes."""
        async for result in self._asr.results():
            if self._stopped:
                return
            await self._handle(result)
        if self._stopped:
            return
        # The recogniser hung up while the interview was still running. Every other signal
        # still says healthy - the socket is open, audio is being accepted, the avatar is
        # on screen - and the candidate would simply talk into a room that stopped
        # listening. Saying so is the difference between a fixable session and a wasted
        # one.
        log.error("Seed ASR ended mid-interview for session %s", self._session.id)
        self._status("ended")
        await self._send_event(
            {
                "type": "error",
                "message": "The interviewer stopped hearing you. Please reload to continue.",
            }
        )

    def _status(self, stage: str) -> None:
        """Tell the candidate's page what the interviewer is doing.

        Rides the transcript channel rather than the voice socket because that is the one
        the page is already reading turns and captions from, and because it means the HR
        live view gets the same narration for free. `publish` never blocks and never
        raises, so this is safe to call from anywhere in the turn loop.
        """
        transcript_hub.publish(
            self._session.id, {"type": "agent_status", "status": stage}
        )

    async def stop(self) -> None:
        self._stopped = True
        if self._turn_timer is not None:
            self._turn_timer.cancel()
            self._turn_timer = None
        if self._barge_in_timer is not None:
            self._barge_in_timer.cancel()
            self._barge_in_timer = None
        await self._cancel_speech()
        await self._asr.close()
        await self._tts.close()

    async def push_audio(self, pcm: bytes) -> None:
        """Forward one chunk of candidate microphone audio to the recogniser."""
        if not self._stopped:
            await self._asr.send_audio(pcm)

    async def set_speech_gate(self, open_: bool) -> None:
        """The browser's capture gate opened or closed.

        The worklet already decides speech from silence in order to know what to send, so
        this is that decision forwarded rather than a second one made here. It carries the
        two signals the utterance-at-a-time recogniser cannot produce for itself: when an
        utterance has ended, and - via the sustained-speech timer - when the candidate is
        interrupting.
        """
        if self._stopped:
            return
        if open_:
            self._on_speech_started()
            return
        self._cancel_barge_in_timer()
        await self._asr.end_utterance()
        if not self._streaming_asr and (self._speaking is None or self._speaking.done()):
            # Recognition of the finished utterance now takes up to ~3 s before the turn
            # loop even sees the words. The streaming path has interim captions filling
            # that gap; this one has nothing to show, so the strip is the only thing
            # telling the candidate their answer landed.
            self._status("thinking")

    def _on_speech_started(self) -> None:
        if not self._streaming_asr and (self._speaking is None or self._speaking.done()):
            # On the streaming path this arrives from the first interim result instead,
            # which is both prompt and proof the recogniser really heard something. There
            # are no prompt interim results here, so the gate is the earliest honest
            # signal that the microphone is picking the candidate up.
            self._status("hearing")
        if self._streaming_asr:
            # The streaming recogniser reports interruptions itself, in time to act on,
            # and does so from the words rather than from the volume - which is the
            # better signal when it is available.
            return
        if self._speaking is None or self._speaking.done():
            return
        if self._barge_in_timer is not None and not self._barge_in_timer.done():
            return
        self._barge_in_timer = asyncio.create_task(self._barge_in_when_sustained())

    def _cancel_barge_in_timer(self) -> None:
        if self._barge_in_timer is not None:
            self._barge_in_timer.cancel()
            self._barge_in_timer = None

    async def _barge_in_when_sustained(self) -> None:
        """Cut the reply short if the candidate is still talking over it."""
        try:
            await asyncio.sleep(_BARGE_IN_HOLD_SECONDS)
        except asyncio.CancelledError:
            # They stopped inside the window - echo, a cough, or a false start.
            raise
        if self._stopped or self._speaking is None or self._speaking.done():
            return
        log.debug("Sustained speech over the interviewer - treating it as barge-in")
        await self._cancel_speech()
        await self._send_event({"type": "flush_audio"})

    # ---------------------------------------------------------------------- turns

    async def _handle(self, result: Transcript) -> None:
        if not result.definite:
            # Interim caption - rides the existing live-transcript channel, same as the
            # RTC path's subtitle callback, so the panel behaves identically in both.
            transcript_hub.publish(
                self._session.id,
                {"type": "partial", "speaker": "candidate", "text": result.text, "final": False},
            )
            # An interim result is proof the candidate is still mid-answer, so it pushes
            # the end-of-turn decision back. This is the signal that actually tracks
            # "still talking": finalised sentences arrive a second or more apart even in
            # continuous speech (the recogniser's end-of-sentence window plus its
            # second-pass re-recognition), so timing the pause between THOSE cuts people
            # off mid-answer, while interim results keep flowing the whole time.
            if self._pending:
                self._restart_turn_timer()
            # Only while the floor is theirs: during a reply, an interim result is far
            # more often our own voice echoing back than the candidate speaking, and
            # flipping the status on that would make the strip flicker mid-sentence.
            if self._speaking is None or self._speaking.done():
                self._status("hearing")
            return

        text = result.text.strip()
        if len(text) < _MIN_UTTERANCE_CHARS:
            return

        # Barge-in: a finished utterance while we are talking means the candidate spoke
        # over the interviewer, and the reply in flight is answering a stale question.
        # Short ones are ignored rather than obeyed - see _MIN_BARGE_IN_CHARS.
        if self._speaking is not None and not self._speaking.done():
            if len(text) < _MIN_BARGE_IN_CHARS:
                log.debug("Ignoring %r during speech - too short to be a real barge-in", text)
                return
            await self._cancel_speech()
            await self._send_event({"type": "flush_audio"})

        # Collect the sentence and (re)start the clock. Answering happens only once the
        # candidate has gone quiet long enough to have finished their whole answer.
        self._pending.append(text)
        self._restart_turn_timer()

    def _restart_turn_timer(self) -> None:
        if self._turn_timer is not None:
            self._turn_timer.cancel()
        self._turn_timer = asyncio.create_task(self._take_turn_when_finished())

    async def _take_turn_when_finished(self) -> None:
        """Wait out the end-of-turn pause, then answer everything heard since the last."""
        try:
            await asyncio.sleep(self._end_of_turn)
        except asyncio.CancelledError:
            # The candidate kept talking; a later sentence owns the turn now.
            raise

        answer = " ".join(self._pending).strip()
        self._pending.clear()
        if not answer:
            self._status("listening")
            return

        async with SessionLocal() as db:
            await turns.record_turn(db, self._session.id, "candidate", answer)
            composed = await interview_turn.compose(
                db, self._session, self._interview, answer, notify=self._status
            )
        self._speak(composed.text, candidate_text=answer, composed=composed)

    def _speak(
        self,
        text_stream,
        *,
        candidate_text: str,
        record: bool = True,
        composed: interview_turn.ComposedTurn | None = None,
    ) -> None:
        """Start synthesising a reply. Non-blocking: the loop stays free to hear a
        barge-in while the audio is still going out."""
        self._speaking = asyncio.create_task(
            self._speak_task(text_stream, candidate_text, record, composed)
        )

    async def _speak_task(
        self,
        text_stream,
        candidate_text: str,
        record: bool,
        composed: interview_turn.ComposedTurn | None,
    ) -> None:
        collected: list[str] = []

        async def tee():
            """Pass the LLM stream to TTS while keeping a copy for the transcript."""
            async for piece in text_stream:
                collected.append(piece)
                yield piece

        await self._send_event({"type": "speaking", "value": True})
        try:
            first = True
            async for audio in self._tts.speak(tee()):
                if first:
                    # Only now is there a voice to hear. Announcing "speaking" when the
                    # task started would cover the synthesis wait, which is exactly the
                    # silence this narration exists to explain.
                    self._status("speaking")
                    first = False
                await self._send_audio(audio)
        except asyncio.CancelledError:
            # Barge-in, or the socket went away. Both are normal.
            raise
        except Exception:  # noqa: BLE001
            log.exception("Speech synthesis failed for session %s", self._session.id)
            await self._send_event(
                {"type": "error", "message": "The interviewer's voice cut out."}
            )
        finally:
            await self._send_event({"type": "speaking", "value": False})
            # Reached on barge-in too, since the cancellation runs this block: whatever
            # ended the reply, the floor is the candidate's again.
            self._status("listening")

        reply = "".join(collected).strip()
        if reply and record:
            async with SessionLocal() as db:
                await turns.record_turn(
                    db,
                    self._session.id,
                    "ai",
                    reply,
                    plan_item_id=composed.plan_item_id if composed else None,
                    guardrail_action=composed.guardrail_action if composed else None,
                )
            asyncio.create_task(  # noqa: RUF006
                interview_turn.audit(self._session.id, candidate_text, reply)
            )

    async def _cancel_speech(self) -> None:
        if self._speaking is None:
            return
        self._speaking.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._speaking
        self._speaking = None


async def _once(text: str):
    """Adapt a ready-made string to the token-stream interface TTS expects."""
    yield text


def _default_voice() -> str:
    from app.core.config import settings

    return settings.tts_voice_id
