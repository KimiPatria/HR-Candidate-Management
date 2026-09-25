/**
 * Pre-flight device check - the green room before the interview starts.
 *
 * A candidate only gets one attempt at this interview, and the two ways hardware ruins
 * it are silent until it is too late: the browser picks a microphone that is not the one
 * they are talking into (a webcam array across the room, a disconnected headset that
 * still holds the default), or their output is routed somewhere they cannot hear. Both
 * present during the interview as "the AI is ignoring me", and by then the transcript is
 * already the record a human will read.
 *
 * So this follows the pattern people already know from Meet and Zoom: ask for permission
 * first, then show what we are hearing *live* while they talk, and let them switch
 * devices and hear the result before committing. The live meter is the important half -
 * a dropdown alone only proves a device exists, not that it is picking up their voice.
 *
 * The chosen ids are handed upward and threaded into whichever voice engine runs (see
 * lib/engine.ts), and remembered in localStorage so a candidate who reloads is not asked
 * to set it all up twice.
 */

import {
  Button,
  Callout,
  FormGroup,
  HTMLSelect,
  Icon,
  Intent,
  Spinner,
  SpinnerSize,
} from "@blueprintjs/core";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";

import { analyserLevel, createSpeechAnalyser } from "../lib/audioTap";

export interface DeviceSelection {
  micDeviceId?: string;
  speakerDeviceId?: string;
}

export interface DeviceCheckHandle {
  /**
   * Close the preview microphone.
   *
   * Called by the page immediately before the interview opens its own capture. React
   * unmount cleanup would also do it, but not reliably *before* the engine's
   * getUserMedia runs, and on some Windows audio stacks two exclusive opens of the same
   * device race - the interview loses, and the candidate starts inaudible.
   */
  release(): void;
}

const STORAGE_MIC = "interview:micDeviceId";
const STORAGE_SPEAKER = "interview:speakerDeviceId";

/** Segments in the level meter. Enough to show a voice moving, few enough to read. */
const METER_SEGMENTS = 18;

// If nothing crosses this in the first few seconds of talking, the device is almost
// certainly not the one in front of them.
const SILENCE_THRESHOLD = 0.04;
const SILENCE_GRACE_MS = 5000;

/** localStorage throws in some privacy modes; a remembered device is never worth an error. */
function remember(key: string, value: string | undefined) {
  try {
    if (value) window.localStorage.setItem(key, value);
    else window.localStorage.removeItem(key);
  } catch {
    // Not fatal - the candidate just picks again next time.
  }
}

function recall(key: string): string | undefined {
  try {
    return window.localStorage.getItem(key) ?? undefined;
  } catch {
    return undefined;
  }
}

type Permission = "prompting" | "granted" | "denied" | "unsupported";

const DeviceCheck = forwardRef<DeviceCheckHandle, { onChange: (s: DeviceSelection) => void }>(
  function DeviceCheck({ onChange }, ref) {
    const [permission, setPermission] = useState<Permission>("prompting");
    const [mics, setMics] = useState<MediaDeviceInfo[]>([]);
    const [speakers, setSpeakers] = useState<MediaDeviceInfo[]>([]);
    const [micId, setMicId] = useState<string | undefined>(() => recall(STORAGE_MIC));
    const [speakerId, setSpeakerId] = useState<string | undefined>(() => recall(STORAGE_SPEAKER));
    const [error, setError] = useState<string | null>(null);
    const [silent, setSilent] = useState(false);
    const [testing, setTesting] = useState(false);
    // Goes false once the page takes the microphone back to start the interview. The
    // meter must stop with it: a frozen bar next to "Connecting…" is honest, whereas a
    // live-looking meter reading zero would announce a dead microphone that is fine.
    const [live, setLive] = useState(true);

    const streamRef = useRef<MediaStream | null>(null);
    const ctxRef = useRef<AudioContext | null>(null);
    const analyserRef = useRef<AnalyserNode | null>(null);
    const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
    const meterRef = useRef<HTMLDivElement>(null);

    // Output routing is Chrome/Edge 110+. Where it is missing, offering a dropdown that
    // silently does nothing is worse than not offering one - the test tone still plays
    // on the system default, which is what they would get in the interview anyway.
    const canPickSpeaker =
      typeof AudioContext !== "undefined" && "setSinkId" in AudioContext.prototype;

    const stopStream = useCallback(() => {
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
      sourceRef.current?.disconnect();
      sourceRef.current = null;
    }, []);

    useImperativeHandle(
      ref,
      () => ({
        release: () => {
          stopStream();
          setSilent(false);
          setLive(false);
        },
      }),
      [stopStream],
    );

    /** Open one microphone and wire it to the meter, replacing whatever was open. */
    const openMic = useCallback(
      async (deviceId: string | undefined) => {
        stopStream();
        // No processing flags here on purpose: this shows the candidate what the raw
        // device picks up. Noise suppression on the preview would hide exactly the
        // problem they came here to find.
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: deviceId ? { deviceId: { exact: deviceId } } : true,
        });
        streamRef.current = stream;

        const ctx = (ctxRef.current ??= new AudioContext());
        // Autoplay policy: a context created without a preceding user gesture starts
        // suspended, and resume() rejects. That must not fail the whole check - the
        // gesture effect below picks it up on the candidate's first click, and until
        // then the dropdowns still work. Awaiting this would turn a dead meter into a
        // dead page.
        void ctx.resume().catch(() => undefined);
        const analyser = (analyserRef.current ??= createSpeechAnalyser(ctx));
        sourceRef.current = ctx.createMediaStreamSource(stream);
        // Meter only - never connected to the destination, which would put the
        // candidate's own voice in their ears at full volume.
        sourceRef.current.connect(analyser);

        // Report the id the browser actually gave us, not the one we asked for: with no
        // stored preference this is how we learn which device is the system default.
        return stream.getAudioTracks()[0]?.getSettings().deviceId ?? deviceId;
      },
      [stopStream],
    );

    const refreshDevices = useCallback(async () => {
      const devices = await navigator.mediaDevices.enumerateDevices();
      setMics(devices.filter((d) => d.kind === "audioinput"));
      setSpeakers(devices.filter((d) => d.kind === "audiooutput"));
    }, []);

    // Permission, then devices, then the preview stream. In that order because device
    // labels are empty strings until a capture permission has been granted - asking for
    // a choice between "Microphone 1" and "Microphone 2" is no choice at all.
    useEffect(() => {
      let cancelled = false;

      (async () => {
        if (!navigator.mediaDevices?.getUserMedia) {
          setPermission("unsupported");
          return;
        }
        try {
          const actual = await openMic(recall(STORAGE_MIC));
          if (cancelled) return;
          await refreshDevices();
          if (cancelled) return;
          setMicId(actual);
          setPermission("granted");
        } catch (err) {
          if (cancelled) return;
          setPermission(isPermissionError(err) ? "denied" : "granted");
          setError(describeMicError(err));
        }
      })();

      const onDeviceChange = () => void refreshDevices();
      navigator.mediaDevices?.addEventListener?.("devicechange", onDeviceChange);

      return () => {
        cancelled = true;
        navigator.mediaDevices?.removeEventListener?.("devicechange", onDeviceChange);
      };
      // Mount only: re-running this would re-prompt and reopen the device.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    /*
     * Resume the audio context on the candidate's first interaction.
     *
     * Landing straight on this page with no prior click - which is what happens when
     * consent is not required - leaves the context suspended and the meter flat. Every
     * browser lifts that on the first real gesture, so one listener is all it takes;
     * `once` means it costs nothing after that.
     */
    useEffect(() => {
      const resume = () => void ctxRef.current?.resume().catch(() => undefined);
      window.addEventListener("pointerdown", resume, { once: true });
      window.addEventListener("keydown", resume, { once: true });
      return () => {
        window.removeEventListener("pointerdown", resume);
        window.removeEventListener("keydown", resume);
      };
    }, []);

    // Everything the preview holds open must be released, whether the candidate joined
    // the interview or navigated away.
    useEffect(
      () => () => {
        stopStream();
        void ctxRef.current?.close();
        ctxRef.current = null;
        analyserRef.current = null;
      },
      [stopStream],
    );

    // Push the selection upward whenever it settles.
    useEffect(() => {
      onChange({ micDeviceId: micId, speakerDeviceId: speakerId });
    }, [micId, speakerId, onChange]);

    /*
     * The meter runs on requestAnimationFrame and writes classes straight onto the
     * segment nodes. Driving it through React state instead would re-render this whole
     * card sixty times a second - including the two selects - to move a few pixels.
     */
    useEffect(() => {
      if (permission !== "granted" || !live) return;
      const scratch = new Uint8Array(analyserRef.current?.fftSize ?? 2048);
      const startedAt = performance.now();
      let peak = 0;
      let lit = -1;
      let frame = 0;

      const tick = (now: number) => {
        frame = requestAnimationFrame(tick);
        const analyser = analyserRef.current;
        const level = analyser ? analyserLevel(analyser, scratch) : 0;
        if (level > peak) peak = level;

        const next = Math.round(level * METER_SEGMENTS);
        if (next !== lit) {
          lit = next;
          const segments = meterRef.current?.children;
          if (segments) {
            for (let i = 0; i < segments.length; i += 1) {
              segments[i].classList.toggle("on", i < next);
            }
          }
        }

        // One-shot: once we have seen a voice, stop second-guessing the device.
        if (now - startedAt > SILENCE_GRACE_MS) {
          setSilent(peak < SILENCE_THRESHOLD);
          peak = 0;
        }
      };
      frame = requestAnimationFrame(tick);
      return () => cancelAnimationFrame(frame);
    }, [permission, micId, live]);

    async function chooseMic(deviceId: string) {
      setError(null);
      setSilent(false);
      try {
        const actual = await openMic(deviceId);
        setMicId(actual);
        remember(STORAGE_MIC, actual);
      } catch (err) {
        setError(describeMicError(err));
      }
    }

    function chooseSpeaker(deviceId: string) {
      setSpeakerId(deviceId);
      remember(STORAGE_SPEAKER, deviceId);
    }

    /**
     * A short two-note chime on the selected output.
     *
     * Synthesised rather than shipped as an asset so there is no file to fail to load,
     * and two notes rather than one because a single beep is easy to mistake for a
     * system sound from another app.
     */
    async function playTestTone() {
      setTesting(true);
      const ctx = new AudioContext();
      try {
        const routable = ctx as AudioContext & { setSinkId?: (id: string) => Promise<void> };
        if (speakerId && routable.setSinkId) await routable.setSinkId(speakerId);
        if (ctx.state === "suspended") await ctx.resume();

        const start = ctx.currentTime + 0.05;
        [660, 880].forEach((frequency, index) => {
          const at = start + index * 0.18;
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = "sine";
          osc.frequency.value = frequency;
          // A hard start and stop on a sine is an audible click; ramp both ends.
          gain.gain.setValueAtTime(0.0001, at);
          gain.gain.exponentialRampToValueAtTime(0.25, at + 0.02);
          gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.17);
          osc.connect(gain).connect(ctx.destination);
          osc.start(at);
          osc.stop(at + 0.18);
        });
        await new Promise((resolve) => setTimeout(resolve, 600));
      } catch (err) {
        console.warn("[devices] Test tone failed", err);
        setError("Could not play a test sound on that output device.");
      } finally {
        void ctx.close();
        setTesting(false);
      }
    }

    if (permission === "unsupported") {
      return (
        <Callout intent={Intent.DANGER} icon="error" title="Microphone not available">
          This browser will not give a page microphone access. Open the interview link in
          Chrome, Edge, or Safari over a secure (https) connection.
        </Callout>
      );
    }

    if (permission === "denied") {
      return (
        <Callout intent={Intent.DANGER} icon="disable" title="Microphone blocked">
          <p>
            The interview is spoken, so it cannot start without a microphone. Allow access
            from the padlock icon in the address bar, then reload this page.
          </p>
          {error && <p className="bp6-text-muted">{error}</p>}
        </Callout>
      );
    }

    if (permission === "prompting") {
      return (
        <div className="row" style={{ gap: 10 }}>
          <Spinner size={SpinnerSize.SMALL} />
          <span className="bp6-text-muted">Waiting for microphone access…</span>
        </div>
      );
    }

    return (
      <div className="device-check">
        <FormGroup label="Microphone" labelFor="mic-select" style={{ marginBottom: 8 }}>
          <HTMLSelect
            id="mic-select"
            fill
            value={micId ?? ""}
            onChange={(event) => void chooseMic(event.currentTarget.value)}
            options={mics.map((device, index) => ({
              value: device.deviceId,
              label: device.label || `Microphone ${index + 1}`,
            }))}
          />
        </FormGroup>

        <div className="mic-meter" ref={meterRef} aria-hidden="true">
          {Array.from({ length: METER_SEGMENTS }, (_, i) => (
            <span key={i} className="mic-meter-segment" />
          ))}
        </div>
        <p className="bp6-text-muted bp6-text-small" style={{ margin: "6px 0 14px" }}>
          {!live
            ? "Handing your microphone over to the interview…"
            : silent
              ? "We are not picking anything up — say something, or try another microphone."
              : "Say something — the bar should move while you talk."}
        </p>

        <FormGroup label="Speaker" labelFor="speaker-select" style={{ marginBottom: 8 }}>
          {canPickSpeaker && speakers.length > 0 ? (
            <HTMLSelect
              id="speaker-select"
              fill
              value={speakerId ?? ""}
              onChange={(event) => chooseSpeaker(event.currentTarget.value)}
              options={speakers.map((device, index) => ({
                value: device.deviceId,
                label: device.label || `Speaker ${index + 1}`,
              }))}
            />
          ) : (
            <p className="bp6-text-muted bp6-text-small" style={{ margin: 0 }}>
              This browser always uses your system default output.
            </p>
          )}
        </FormGroup>

        <Button
          icon="volume-up"
          text="Play a test sound"
          onClick={() => void playTestTone()}
          loading={testing}
        />

        {error && (
          <Callout intent={Intent.WARNING} icon="warning-sign" compact style={{ marginTop: 12 }}>
            {error}
          </Callout>
        )}

        <p className="bp6-text-muted bp6-text-small" style={{ margin: "14px 0 0" }}>
          <Icon icon="headset" size={12} /> Headphones are worth using — they stop the
          interviewer&rsquo;s voice leaking back into your microphone.
        </p>
      </div>
    );
  },
);

export default DeviceCheck;

function isPermissionError(err: unknown): boolean {
  return err instanceof DOMException && (err.name === "NotAllowedError" || err.name === "SecurityError");
}

/** getUserMedia's error names are precise but unreadable; each one has a real cause. */
function describeMicError(err: unknown): string {
  if (!(err instanceof DOMException)) {
    return err instanceof Error ? err.message : "Could not open that microphone.";
  }
  switch (err.name) {
    case "NotAllowedError":
    case "SecurityError":
      return "Microphone access was refused by the browser.";
    case "NotFoundError":
      return "No microphone was found. Plug one in, then reload this page.";
    case "NotReadableError":
      return "That microphone is in use by another app. Close it and try again.";
    case "OverconstrainedError":
      return "That microphone is no longer available. Pick another one.";
    default:
      return `Could not open that microphone (${err.name}).`;
  }
}
