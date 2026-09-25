/**
 * Two-sided call visualiser, in the spirit of the Dynamic Island's call view.
 *
 * The interviewer grows from the left edge and the candidate from the right, meeting in
 * the middle where their two colours blend. Whoever is talking is at full strength;
 * whoever is not collapses toward a resting line, so a glance tells you who has the
 * floor - which is the whole point of drawing this rather than a pulsing dot.
 *
 * Layout detail worth keeping: bar 0 of each side sits at the CENTRE, not the edge, and
 * bars run outward through rising frequency bands. Speech energy is concentrated in the
 * low bands, so the busiest bars are the ones either side of the midpoint - exactly
 * where the gradient blends. Reversing it would leave the blend zone almost still.
 *
 * REAL-TIME
 * ---------
 * Everything is pulled inside one requestAnimationFrame loop that reads the analysers
 * directly and paints. Nothing here goes through React state, because a 60 Hz setState
 * would re-render the page sixty times a second to move some pixels, and the scheduling
 * jitter would show up as stutter. The only latency between a sound and a bar is the
 * analyser window itself (see lib/audioTap.ts).
 *
 * On the RTC pipeline there is no analyser to read - just a volume number every 100 ms -
 * so `barsFromLevel` shapes a plausible envelope instead. The height is real data; the
 * bar-to-bar variation is decoration, and it is kept subtle so it does not read as more
 * precision than we have.
 */

import { useEffect, useRef } from "react";

import type { AudioTap, VoiceSide } from "../lib/audioTap";

export type VisualizerVariant = "stage" | "strip";

/** Bars per side. The strip sits over avatar video and gets a coarser, calmer render. */
const BARS: Record<VisualizerVariant, number> = { stage: 26, strip: 16 };

// Asymmetric smoothing: jump to a louder reading almost immediately, fall away slowly.
// Symmetric smoothing makes speech look like a slow wobble; this is what makes consonant
// attacks visible and what every meter that feels "live" does.
const ATTACK = 0.55;
const DECAY = 0.12;

// How strongly a side fades when it is not the one talking. The idle side never
// disappears - a dead half looks like a bug - it just recedes.
const IDLE_ALPHA = 0.22;
const ACTIVE_ALPHA = 1;

// A side counts as fully active at this level. Low enough to catch a quiet talker, high
// enough that room tone does not light both halves permanently.
const ACTIVE_LEVEL = 0.06;

/**
 * Where the frequency axis is cut off. Above ~5 kHz speech has almost no energy and the
 * bars would just sit flat, wasting half the width.
 */
const SPEECH_CEILING_HZ = 5000;

export default function VoiceVisualizer({
  tap,
  variant = "stage",
  className,
}: {
  tap: AudioTap | null;
  variant?: VisualizerVariant;
  className?: string;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // Held in a ref so the tap arriving (or changing) does not restart the render loop.
  const tapRef = useRef(tap);
  tapRef.current = tap;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const barCount = BARS[variant];
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // Smoothed bar heights and side activity, carried across frames.
    const heights: Record<VoiceSide, Float32Array> = {
      interviewer: new Float32Array(barCount),
      candidate: new Float32Array(barCount),
    };
    const activity: Record<VoiceSide, number> = { interviewer: 0, candidate: 0 };
    const bins = new Float32Array(barCount);
    // Sized on first use, once an analyser exists to say how many bins there are.
    let spectrum = new Uint8Array(0);

    const colours = readColours(canvas);

    // The backing store must track CSS size * devicePixelRatio, or the bars go soft on a
    // retina display and blurry after the window moves between monitors.
    let width = 0;
    let height = 0;
    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      width = rect.width;
      height = rect.height;
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);

    let frame = 0;
    const render = (now: number) => {
      frame = requestAnimationFrame(render);
      const current = tapRef.current;

      for (const side of SIDES) {
        const level = current?.level(side) ?? 0;
        activity[side] +=
          (level - activity[side]) * (level > activity[side] ? ATTACK : DECAY);

        const analyser = current?.analyser(side) ?? null;
        if (analyser) {
          if (spectrum.length !== analyser.frequencyBinCount) {
            spectrum = new Uint8Array(analyser.frequencyBinCount);
          }
          analyser.getByteFrequencyData(spectrum);
          barsFromSpectrum(spectrum, analyser.context.sampleRate, bins);
        } else {
          barsFromLevel(level, now, bins);
        }

        const target = heights[side];
        for (let i = 0; i < barCount; i += 1) {
          const to = bins[i];
          // Reduced motion still tracks the voice, it just does not snap; the asymmetry
          // is what reads as flicker, so drop it and smooth both directions slowly.
          const rate = reduceMotion ? DECAY : to > target[i] ? ATTACK : DECAY;
          target[i] += (to - target[i]) * rate;
        }
      }

      draw(ctx, width, height, heights, activity, colours, variant);
    };
    frame = requestAnimationFrame(render);

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [variant]);

  return (
    <canvas
      ref={canvasRef}
      className={`voice-visualizer voice-visualizer-${variant}${className ? ` ${className}` : ""}`}
      // Decorative: the same information is in the status strip beside the transcript,
      // which is what a screen reader should read instead of a canvas full of bars.
      aria-hidden="true"
    />
  );
}

const SIDES: VoiceSide[] = ["interviewer", "candidate"];

/* ------------------------------------------------------------------------ signal */

/**
 * Fold FFT bins into `out` bars, centre-outward, on a roughly logarithmic axis.
 *
 * Log spacing rather than linear because pitch is logarithmic: linear spacing hands the
 * first two bars everything a voice does and leaves the rest flat.
 */
function barsFromSpectrum(spectrum: Uint8Array, sampleRate: number, out: Float32Array) {
  const nyquist = sampleRate / 2;
  const usable = Math.min(
    spectrum.length,
    Math.round((SPEECH_CEILING_HZ / nyquist) * spectrum.length),
  );
  const bars = out.length;

  for (let i = 0; i < bars; i += 1) {
    // Both edges of the band, so no bin is counted twice and none is skipped.
    const from = Math.floor(bandEdge(i, bars) * usable);
    const to = Math.max(from + 1, Math.floor(bandEdge(i + 1, bars) * usable));
    let peak = 0;
    for (let bin = from; bin < to && bin < spectrum.length; bin += 1) {
      if (spectrum[bin] > peak) peak = spectrum[bin];
    }
    // Peak rather than mean: averaging a wide high band against its quiet neighbours
    // flattens exactly the detail that makes speech legible.
    out[i] = peak / 255;
  }
}

/** Normalised position of band edge `i` on a log-ish axis over [0, 1]. */
function bandEdge(i: number, bars: number): number {
  const t = i / bars;
  return (Math.pow(2, t * 4) - 1) / 15;
}

/**
 * Bar shape for pipelines that only report a scalar level (see the module comment).
 *
 * A fixed envelope - tallest at the centre, tapering outward, the way real speech sits -
 * with a slow per-bar wobble so it does not look frozen between the 100 ms updates.
 */
function barsFromLevel(level: number, now: number, out: Float32Array) {
  const bars = out.length;
  for (let i = 0; i < bars; i += 1) {
    const taper = Math.exp(-(i / bars) * 2.2);
    const wobble = 0.82 + 0.18 * Math.sin(now / 190 + i * 0.9);
    out[i] = Math.min(1, level * taper * wobble * 1.35);
  }
}

/* ------------------------------------------------------------------------- paint */

interface Colours {
  interviewer: string;
  candidate: string;
}

/**
 * Colours come from CSS custom properties so the stylesheet owns them and a theme change
 * stays a stylesheet change. The fallbacks keep the canvas visible if the properties are
 * missing on the first frame.
 */
function readColours(el: HTMLElement): Colours {
  const style = getComputedStyle(el);
  const read = (name: string, fallback: string) =>
    style.getPropertyValue(name).trim() || fallback;
  return {
    interviewer: read("--viz-interviewer", "#4c90f0"),
    candidate: read("--viz-candidate", "#32d296"),
  };
}

function draw(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  heights: Record<VoiceSide, Float32Array>,
  activity: Record<VoiceSide, number>,
  colours: Colours,
  variant: VisualizerVariant,
) {
  ctx.clearRect(0, 0, width, height);
  if (width <= 0 || height <= 0) return;

  const bars = heights.interviewer.length;
  const mid = width / 2;
  const barWidth = variant === "stage" ? 4 : 3;
  const gap = variant === "stage" ? 4 : 3;
  const slot = barWidth + gap;
  const centreY = height / 2;
  // A resting line, so silence still reads as connected rather than broken.
  const minHeight = barWidth;

  // One gradient across the whole pill: pure interviewer on the left, pure candidate on
  // the right, blending through the middle third. Both sides are filled from it, so the
  // bars either side of the centre genuinely share colour instead of butting up against
  // a hard seam.
  const gradient = ctx.createLinearGradient(0, 0, width, 0);
  gradient.addColorStop(0, colours.interviewer);
  gradient.addColorStop(0.34, colours.interviewer);
  gradient.addColorStop(0.5, mixColours(colours.interviewer, colours.candidate));
  gradient.addColorStop(0.66, colours.candidate);
  gradient.addColorStop(1, colours.candidate);
  ctx.fillStyle = gradient;

  for (const side of SIDES) {
    const values = heights[side];
    ctx.globalAlpha =
      IDLE_ALPHA +
      (ACTIVE_ALPHA - IDLE_ALPHA) * Math.min(1, activity[side] / ACTIVE_LEVEL);

    for (let i = 0; i < bars; i += 1) {
      // i = 0 is the centre; the interviewer runs left, the candidate right.
      const offset = gap / 2 + i * slot;
      const x = side === "interviewer" ? mid - offset - barWidth : mid + offset;
      if (x < -barWidth || x > width) continue;

      const h = Math.max(minHeight, values[i] * height);
      const radius = Math.min(barWidth / 2, h / 2);
      ctx.beginPath();
      ctx.roundRect(x, centreY - h / 2, barWidth, h, radius);
      ctx.fill();
    }
  }
  ctx.globalAlpha = 1;
}

/** Midpoint of two CSS colours, for the centre stop of the gradient. */
function mixColours(a: string, b: string): string {
  const from = parseColour(a);
  const to = parseColour(b);
  if (!from || !to) return a;
  const channel = (i: number) => Math.round((from[i] + to[i]) / 2);
  return `rgb(${channel(0)}, ${channel(1)}, ${channel(2)})`;
}

/**
 * Minimal colour parser: hex and rgb() only.
 *
 * That is the whole vocabulary the two custom properties use, and a full CSS colour
 * parser here would be a lot of code guarding a case we control. Anything else falls
 * back to no blend rather than throwing inside a frame.
 */
function parseColour(value: string): [number, number, number] | null {
  const hex = value.trim().match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (hex) {
    const digits = hex[1];
    const full =
      digits.length === 3
        ? digits
            .split("")
            .map((d) => d + d)
            .join("")
        : digits;
    return [
      parseInt(full.slice(0, 2), 16),
      parseInt(full.slice(2, 4), 16),
      parseInt(full.slice(4, 6), 16),
    ];
  }
  const rgb = value.match(/rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/i);
  if (rgb) return [Number(rgb[1]), Number(rgb[2]), Number(rgb[3])];
  return null;
}
