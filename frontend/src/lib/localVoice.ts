/**
 * Local voice engine: the browser half of VOICE_MODE=local.
 *
 * Replaces the BytePlus RTC room with one WebSocket to our own backend. Implements the
 * same `InterviewEngine` interface the RTC engine does, so `InterviewPage` does not know
 * or care which one it is driving - that is the seam that makes switching back to RTC a
 * config change rather than a rewrite.
 *
 *   up    binary   16 kHz mono PCM16 from the mic worklet
 *   up    text     the capture gate opening and closing, plus hang-up
 *   down  binary   24 kHz mono PCM16 from Seed TTS, scheduled back to back for playback
 *   down  text     small JSON control messages
 *
 * Two things here are doing more work than they look like they are:
 *
 *   `echoCancellation` on capture. Without it the mic hears the interviewer through the
 *   candidate's speakers, Seed ASR transcribes it, and the interview starts answering
 *   itself in a loop. It presents as a recogniser bug and is an audio routing one.
 *
 *   The playback scheduler. Audio arrives in ~300-500 ms chunks that must be joined
 *   seamlessly; scheduling each one at `currentTime` would overlap them into garble, so
 *   each is scheduled at the end of the previous one and the cursor is only reset when
 *   playback has actually drained.
 */

import {
  analyserLevel,
  createSpeechAnalyser,
  type AudioTap,
  type VoiceSide,
} from "./audioTap";
import type { DeviceChoice, EngineCallbacks, InterviewEngine } from "./rtc";
import workletUrl from "./micWorklet.js?url";

// Seed TTS is asked for this rate in `settings.seed_tts_sample_rate`; the server
// restates it in its `config` message and that value wins over this fallback.
const DEFAULT_OUTPUT_RATE = 24000;

// How far ahead of `currentTime` a freshly scheduled chunk is placed when the queue has
// drained. Absorbs jitter between network arrival and the audio clock without being long
// enough to hear as lag.
const SCHEDULE_LEAD_SECONDS = 0.08;

export function createLocalEngine(
  token: string,
  callbacks: EngineCallbacks = {},
  devices: DeviceChoice = {},
): InterviewEngine {
  let socket: WebSocket | null = null;
  let audioCtx: AudioContext | null = null;
  let micStream: MediaStream | null = null;
  let worklet: AudioWorkletNode | null = null;
  let outputRate = DEFAULT_OUTPUT_RATE;

  // Visualiser taps. Owning the whole audio graph is the advantage this pipeline has
  // over RTC: the microphone source fans out to one analyser, and every TTS buffer is
  // scheduled through `speechBus` rather than straight at the destination, so the second
  // analyser sees exactly the samples the candidate is hearing.
  //
  // Both are nulled out until join() builds the graph; the visualiser reads zero until
  // then, which is the truth - nothing is flowing yet.
  let micAnalyser: AnalyserNode | null = null;
  let speechAnalyser: AnalyserNode | null = null;
  let speechBus: GainNode | null = null;
  // One scratch buffer per side, allocated once. analyserLevel() is called every frame
  // and allocating a Uint8Array in that loop is how you get audible GC stutter.
  let micScratch = new Uint8Array(0);
  let speechScratch = new Uint8Array(0);

  const taps: AudioTap = {
    analyser: (side: VoiceSide) =>
      side === "candidate" ? micAnalyser : speechAnalyser,
    level: (side: VoiceSide) => {
      if (side === "candidate") {
        return micAnalyser ? analyserLevel(micAnalyser, micScratch) : 0;
      }
      return speechAnalyser ? analyserLevel(speechAnalyser, speechScratch) : 0;
    },
  };

  // Playback scheduling state.
  let playCursor = 0;
  let scheduled: AudioBufferSourceNode[] = [];

  const diagnose = (note: string) => {
    console.info("[voice]", note);
    callbacks.onDiagnostic?.(note);
  };

  function flushPlayback() {
    // Barge-in: drop everything queued but not yet heard. Sources already started are
    // stopped explicitly; stop() on a finished source is a no-op we can ignore.
    for (const source of scheduled) {
      try {
        source.stop();
      } catch {
        // Already ended.
      }
    }
    scheduled = [];
    playCursor = 0;
  }

  function play(pcm: ArrayBuffer) {
    if (!audioCtx) return;
    const samples = new Int16Array(pcm);
    if (!samples.length) return;

    const buffer = audioCtx.createBuffer(1, samples.length, outputRate);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < samples.length; i += 1) channel[i] = samples[i] / 0x8000;

    const source = audioCtx.createBufferSource();
    source.buffer = buffer;
    source.connect(speechBus ?? audioCtx.destination);

    const now = audioCtx.currentTime;
    if (playCursor < now) playCursor = now + SCHEDULE_LEAD_SECONDS;
    source.start(playCursor);
    playCursor += buffer.duration;

    scheduled.push(source);
    source.onended = () => {
      scheduled = scheduled.filter((s) => s !== source);
    };
  }

  return {
    mode: "local",
    taps,

    async join() {
      audioCtx = new AudioContext();
      // Autoplay policy: the context starts suspended unless resumed inside the user
      // gesture that led here. Without this the greeting is scheduled and never heard.
      if (audioCtx.state === "suspended") await audioCtx.resume();

      // Route playback to the speaker the candidate picked in the pre-flight check.
      // AudioContext.setSinkId is Chrome/Edge 110+; elsewhere the call is simply absent
      // and playback stays on the system default, which is what the check told them.
      if (devices.speakerDeviceId) {
        const ctx = audioCtx as AudioContext & { setSinkId?: (id: string) => Promise<void> };
        try {
          await ctx.setSinkId?.(devices.speakerDeviceId);
        } catch (err) {
          console.warn("[voice] Could not set the playback device", err);
        }
      }

      micStream = await openMicrophone(devices.micDeviceId, diagnose);

      await audioCtx.audioWorklet.addModule(workletUrl);
      worklet = new AudioWorkletNode(audioCtx, "mic-processor");
      const micSource = audioCtx.createMediaStreamSource(micStream);
      micSource.connect(worklet);

      // The visualiser taps the microphone *before* the worklet's noise gate, so the
      // bars respond to the candidate the instant they speak rather than after the gate
      // decides it was speech. A fan-out from the same source node costs nothing.
      micAnalyser = createSpeechAnalyser(audioCtx);
      micScratch = new Uint8Array(micAnalyser.fftSize);
      micSource.connect(micAnalyser);

      // Everything the interviewer says goes through here on its way to the speakers.
      speechBus = audioCtx.createGain();
      speechAnalyser = createSpeechAnalyser(audioCtx);
      speechScratch = new Uint8Array(speechAnalyser.fftSize);
      speechBus.connect(speechAnalyser);
      speechBus.connect(audioCtx.destination);
      // The worklet emits through its port, not its output, but Chrome will garbage
      // collect a node with no downstream connection - so it is parked on a muted gain
      // node rather than wired to the speakers, which would echo the candidate to
      // themselves.
      const sink = audioCtx.createGain();
      sink.gain.value = 0;
      worklet.connect(sink).connect(audioCtx.destination);

      const scheme = window.location.protocol === "https:" ? "wss" : "ws";
      const url = `${scheme}://${window.location.host}/api/join/${encodeURIComponent(
        token,
      )}/voice`;
      socket = new WebSocket(url);
      socket.binaryType = "arraybuffer";

      // The worklet sends PCM as ArrayBuffers and gate changes as objects. Both go up
      // the same socket, as binary and text respectively, which is the split the server
      // already reads them by.
      worklet.port.onmessage = (
        event: MessageEvent<ArrayBuffer | { type: string; value: boolean }>,
      ) => {
        if (socket?.readyState !== WebSocket.OPEN) return;
        if (event.data instanceof ArrayBuffer) {
          socket.send(event.data);
        } else {
          socket.send(JSON.stringify(event.data));
        }
      };

      await new Promise<void>((resolve, reject) => {
        if (!socket) return reject(new Error("socket gone"));

        socket.onopen = () => {
          diagnose(`connected to the interview server (mic ${audioCtx?.sampleRate} Hz)`);
          resolve();
        };

        socket.onerror = () => {
          reject(new Error("Could not reach the interview server."));
        };

        socket.onclose = (event) => {
          if (event.code === 4401) {
            callbacks.onError?.("This interview link is no longer valid.");
          } else if (event.code === 4409) {
            callbacks.onError?.(
              "The server is configured for the RTC pipeline; reload to use it.",
            );
          } else if (event.code === 4500) {
            callbacks.onError?.("The interviewer's voice service failed to start.");
          }
          flushPlayback();
        };

        socket.onmessage = (event) => {
          if (event.data instanceof ArrayBuffer) {
            play(event.data);
            return;
          }
          let message: Record<string, unknown>;
          try {
            message = JSON.parse(String(event.data));
          } catch {
            return;
          }
          switch (message.type) {
            case "config":
              outputRate = Number(message.output_sample_rate) || DEFAULT_OUTPUT_RATE;
              diagnose(`speech: in 16000 Hz, out ${outputRate} Hz`);
              break;
            case "ready":
              callbacks.onConnected?.();
              break;
            case "flush_audio":
              // The candidate interrupted; stop talking over them.
              flushPlayback();
              break;
            case "error":
              callbacks.onError?.(String(message.message ?? "The interview hit a problem."));
              break;
            default:
              break;
          }
        };
      });
    },

    async leave() {
      flushPlayback();
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "end" }));
      }
      socket?.close();
      socket = null;
      worklet?.port.close();
      worklet?.disconnect();
      worklet = null;
      micStream?.getTracks().forEach((track) => track.stop());
      micStream = null;
      micAnalyser = null;
      speechAnalyser = null;
      speechBus = null;
      await audioCtx?.close();
      audioCtx = null;
    },

    async setMicMuted(muted: boolean) {
      // Handled in the worklet rather than by stopping the track: re-acquiring the mic
      // takes long enough to clip the first word after unmuting.
      worklet?.port.postMessage({ type: "mute", value: muted });
    },
  };
}

/**
 * Open the candidate's microphone, preferring the device they chose.
 *
 * `deviceId: {exact}` is used rather than a plain preference so a stale id fails loudly
 * instead of silently opening a different microphone than the one they just tested. The
 * fallback then reopens on the system default - being heard on the wrong microphone
 * beats not being heard at all, as long as the diagnostic says which happened.
 *
 * The processing flags matter more than they look. `echoCancellation` in particular is
 * what stops the microphone hearing the interviewer through the speakers, which the
 * recogniser would otherwise transcribe and the interview would answer - see the module
 * comment above.
 */
async function openMicrophone(
  deviceId: string | undefined,
  diagnose: (note: string) => void,
): Promise<MediaStream> {
  const processing: MediaTrackConstraints = {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
    channelCount: 1,
  };

  if (deviceId) {
    try {
      return await navigator.mediaDevices.getUserMedia({
        audio: { ...processing, deviceId: { exact: deviceId } },
      });
    } catch (err) {
      console.warn("[voice] Chosen microphone unavailable", err);
      diagnose("selected microphone unavailable - falling back to the system default");
    }
  }
  return navigator.mediaDevices.getUserMedia({ audio: processing });
}
