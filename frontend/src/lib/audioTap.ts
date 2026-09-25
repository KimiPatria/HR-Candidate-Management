/**
 * How the visualiser gets at the two voices in the room.
 *
 * The two voice pipelines can offer very different quality of signal, and the tap is the
 * seam that hides the difference from the component doing the drawing:
 *
 *   local  we own the whole audio graph, so both sides get a real `AnalyserNode` -
 *          the microphone source fans out to one, and Seed TTS playback is routed
 *          through another on its way to the speakers. Genuine FFT, read at frame rate.
 *
 *   rtc    BytePlus owns the audio graph and exposes no node to attach to. All we get is
 *          `enableAudioPropertiesReport`, a scalar volume every 100 ms. So `analyser()`
 *          returns null for that side and the visualiser falls back to shaping bars from
 *          the envelope - the loudness is real data, the bar *shape* is decoration.
 *
 * Everything here is pull-based on purpose. The visualiser samples inside its own
 * requestAnimationFrame loop rather than being pushed at; pushing would mean either
 * re-rendering React 60 times a second or buffering frames, and both cost the latency
 * this is supposed to avoid.
 */

/** Which voice a reading belongs to. Names match what the candidate sees on screen. */
export type VoiceSide = "interviewer" | "candidate";

export interface AudioTap {
  /** Live analyser for that side, or null when the pipeline only reports a level. */
  analyser(side: VoiceSide): AnalyserNode | null;
  /** 0..1 loudness for that side. Always available, whatever the pipeline. */
  level(side: VoiceSide): number;
}

// 512 bins over ~24 kHz is ~47 Hz per bin: fine enough to see formants move, coarse
// enough that one frame's worth of work stays trivial. Larger windows also mean more
// latency, which is the thing being optimised for here.
const FFT_SIZE = 512;

// The analyser's own smoothing. Kept low because the visualiser does its own asymmetric
// attack/decay - stacking both makes speech look like slow breathing.
const SMOOTHING = 0.35;

/** An analyser tuned for speech, not for a spectrum analyser toy. */
export function createSpeechAnalyser(ctx: AudioContext): AnalyserNode {
  const node = ctx.createAnalyser();
  node.fftSize = FFT_SIZE;
  node.smoothingTimeConstant = SMOOTHING;
  // The default -100..-30 dB window spends most of its range on room tone. Speech at
  // normal levels sits around -50 dB, so this window puts it in the middle of the bar
  // rather than pinned at the bottom.
  node.minDecibels = -80;
  node.maxDecibels = -20;
  return node;
}

/**
 * RMS of the analyser's current time-domain frame, 0..1.
 *
 * Time domain rather than the frequency bins because RMS is what tracks perceived
 * loudness; averaging FFT magnitudes weights a hissy "s" the same as a vowel.
 */
export function analyserLevel(
  node: AnalyserNode,
  // Spelled with the buffer type because TypeScript 5.7 made typed arrays generic over
  // it, and the DOM signature refuses a SharedArrayBuffer-backed view. A bare
  // `Uint8Array` widens to `ArrayBufferLike` and no longer matches.
  scratch: Uint8Array<ArrayBuffer>,
): number {
  node.getByteTimeDomainData(scratch);
  let sum = 0;
  for (let i = 0; i < scratch.length; i += 1) {
    const centred = (scratch[i] - 128) / 128;
    sum += centred * centred;
  }
  // Normal speech RMS lands around 0.05-0.15, so the raw value would barely move the
  // bars. The gain puts a conversational voice in the upper half of the range.
  return Math.min(1, Math.sqrt(sum / scratch.length) * 4.5);
}

/** A tap that always reads silent. Used by the mock engine so nothing has to null-check. */
export const SILENT_TAP: AudioTap = {
  analyser: () => null,
  level: () => 0,
};
