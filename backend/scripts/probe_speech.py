"""Live end-to-end probe of the local voice pipeline's two speech legs.

`app/services/speech_check.py` only proves the credentials are accepted at handshake.
This goes further and actually runs a synthesis and a recognition, printing every frame
the gateway sends back - which is the only way to confirm the parts of the Seed TTS
bidirectional protocol (event numbers, `req_params` shape) that we had to infer, since
the published reference we worked from was truncated before its event table.

Run it whenever the TTS leg misbehaves, and after any change to `voice/tts.py`:

    cd backend && .venv/Scripts/python.exe -m scripts.probe_speech

It synthesises one short sentence and feeds the resulting audio straight back into ASR,
so a full pass proves both legs and the framing between them. Costs a couple of seconds
of billed speech.
"""

import asyncio
import json
import sys

import websockets

from app.core.config import settings
from app.services.voice import protocol
from app.services.voice.tts import (
    EVENT_FINISH_CONNECTION,
    EVENT_FINISH_SESSION,
    EVENT_START_CONNECTION,
    EVENT_START_SESSION,
    EVENT_TASK_REQUEST,
    NAMESPACE,
)

SENTENCE = "Thanks for joining. Tell me about your most recent role."


def _describe(frame: protocol.Frame) -> str:
    bits = [f"type=0b{frame.message_type:04b}"]
    if frame.event is not None:
        bits.append(f"event={frame.event}")
    if frame.session_id:
        bits.append(f"session={frame.session_id[:8]}...")
    if frame.error_code is not None:
        bits.append(f"error={frame.error_code}")
    if frame.is_audio or (frame.payload and frame.serialization == protocol.SERIAL_RAW):
        bits.append(f"audio={len(frame.payload)}B")
    elif frame.payload:
        bits.append(f"payload={frame.text()[:160]}")
    return "  ".join(bits)


async def probe_tts() -> bytes:
    """Synthesise one sentence, printing the whole event exchange. Returns raw PCM."""
    url = f"wss://{settings.seed_speech_host}{settings.seed_tts_path}"
    headers = {
        "X-Api-Key": settings.seed_speech_api_key,
        "X-Api-Resource-Id": settings.seed_tts_resource_id,
        "X-Api-Connect-Id": "probe-tts",
    }
    print(f"\n=== TTS  {url}")
    print(f"    voice={settings.tts_voice_id}  resource={settings.seed_tts_resource_id}")

    audio = bytearray()
    async with websockets.connect(
        url, additional_headers=headers, max_size=8 * 1024 * 1024
    ) as ws:
        print(f"    handshake OK  logid={ws.response.headers.get('X-Tt-Logid', '?')}")

        async def send(event: int, params: dict, session: str | None = None) -> None:
            body = {"event": event, "namespace": NAMESPACE, **params}
            print(f"--> event={event} {json.dumps(params)[:140]}")
            await ws.send(
                protocol.encode(
                    protocol.CLIENT_FULL_REQUEST,
                    json.dumps(body).encode(),
                    flags=protocol.FLAG_WITH_EVENT,
                    event=event,
                    session_id=session,
                )
            )

        audio_params = {
            "speaker": settings.tts_voice_id,
            "audio_params": {"format": "pcm", "sample_rate": settings.seed_tts_sample_rate},
        }
        session_id = "probe-session-0001"

        await send(EVENT_START_CONNECTION, {})
        await send(EVENT_START_SESSION, {"req_params": audio_params}, session_id)
        await send(
            EVENT_TASK_REQUEST, {"req_params": {**audio_params, "text": SENTENCE}}, session_id
        )
        await send(EVENT_FINISH_SESSION, {}, session_id)

        try:
            async for raw in ws:
                if not isinstance(raw, bytes):
                    continue
                try:
                    frame = protocol.decode(raw)
                except ValueError as exc:
                    print(f"<-- UNDECODABLE: {exc}  first16={raw[:16].hex()}")
                    continue
                print(f"<-- {_describe(frame)}")
                if frame.is_audio or (frame.event == 352 and frame.payload):
                    audio += frame.payload
                if frame.event in (152, 153, 359) or frame.is_error:
                    break
            await send(EVENT_FINISH_CONNECTION, {})
        except websockets.exceptions.ConnectionClosed as exc:
            print(f"    closed: {exc}")

    print(f"    total audio: {len(audio)} bytes")
    return bytes(audio)


async def probe_asr(pcm: bytes) -> None:
    """Feed PCM into Seed ASR and print the transcripts it returns.

    The TTS output is 24 kHz and ASR wants 16 kHz, so this decimates crudely - good
    enough to prove the leg works, not a resampler worth reusing.
    """
    url = f"wss://{settings.seed_speech_host}{settings.seed_asr_path}"
    headers = {
        "X-Api-Key": settings.seed_speech_api_key,
        "X-Api-Resource-Id": settings.seed_asr_resource_id,
        "X-Api-Connect-Id": "probe-asr",
    }
    print(f"\n=== ASR  {url}")

    if pcm:
        import array

        samples = array.array("h")
        samples.frombytes(pcm[: len(pcm) // 2 * 2])
        ratio = settings.seed_tts_sample_rate / 16000
        downsampled = array.array(
            "h", [samples[int(i * ratio)] for i in range(int(len(samples) / ratio))]
        )
        pcm = downsampled.tobytes()
        print(f"    feeding {len(pcm)} bytes of 16 kHz PCM ({len(pcm) / 32000:.1f}s)")
    else:
        print("    no audio from TTS - sending silence just to test the handshake")
        pcm = b"\x00" * 32000

    from app.services.voice.asr import _request_config

    async with websockets.connect(
        url, additional_headers=headers, max_size=8 * 1024 * 1024
    ) as ws:
        print(f"    handshake OK  logid={ws.response.headers.get('X-Tt-Logid', '?')}")
        await ws.send(
            protocol.encode(
                protocol.CLIENT_FULL_REQUEST, json.dumps(_request_config()).encode()
            )
        )

        async def feed() -> None:
            # 200 ms packets, per the API reference's pacing guidance.
            step = 16000 * 2 // 5
            for offset in range(0, len(pcm), step):
                chunk = pcm[offset : offset + step]
                last = offset + step >= len(pcm)
                await ws.send(
                    protocol.encode(
                        protocol.CLIENT_AUDIO_ONLY,
                        chunk,
                        flags=protocol.FLAG_LAST_PACKET if last else protocol.FLAG_NONE,
                        serialization=protocol.SERIAL_RAW,
                    )
                )
                await asyncio.sleep(0.2)

        sender = asyncio.create_task(feed())
        try:
            async with asyncio.timeout(45):
                async for raw in ws:
                    if not isinstance(raw, bytes):
                        continue
                    frame = protocol.decode(raw)
                    if frame.is_error:
                        print(f"<-- ERROR {frame.error_code}: {frame.text()[:200]}")
                        break
                    body = frame.json()
                    result = body.get("result") or {}
                    if isinstance(result, list):
                        result = result[0] if result else {}
                    text = (result.get("text") or "").strip()
                    definites = [
                        u.get("text")
                        for u in (result.get("utterances") or [])
                        if u.get("definite")
                    ]
                    if definites:
                        print(f"<-- DEFINITE: {definites}")
                    elif text:
                        print(f"<-- interim:  {text}")
        except TimeoutError:
            print("    timed out waiting for transcripts")
        finally:
            sender.cancel()


async def main() -> None:
    if not settings.seed_speech_api_key:
        sys.exit("SEED_SPEECH_API_KEY is not set.")
    print(f"host={settings.seed_speech_host}  voice_mode={settings.voice_mode}")
    audio = await probe_tts()
    await probe_asr(audio)


if __name__ == "__main__":
    asyncio.run(main())
