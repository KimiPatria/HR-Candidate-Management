/**
 * Microphone capture worklet: native-rate float audio in, 16 kHz PCM16 packets out.
 *
 * Runs on the audio thread rather than the main thread because capture must not miss a
 * render quantum while React is busy - a dropped quantum is a clipped word, and clipped
 * words become mis-recognitions the candidate has to repeat.
 *
 * Resampling and Int16 conversion happen here, not in the page, so what crosses to the
 * main thread is already the bytes that go on the wire: about 3 KB every 100 ms instead
 * of a Float32Array every 2.7 ms.
 *
 * Two kinds of message cross that port: ArrayBuffers of PCM, and `{type: "speech"}` on
 * every change in whether the gate below is passing audio. The second exists because an
 * interview in a language the streaming recogniser cannot hear runs on a model that
 * reports nothing until it is told the utterance ended - and this gate is the only part
 * of the system that already knows, having computed it to decide what to send.
 *
 * Seed ASR accepts 16 kHz mono signed-16-bit little-endian PCM only, and the reference
 * asks for 100-200 ms per packet - hence PACKET_SAMPLES below.
 */

const TARGET_RATE = 16000;
const PACKET_SAMPLES = 1600; // 100 ms at 16 kHz

/*
 * Noise gate.
 *
 * Streaming room tone to the recogniser is not free: a large speech model asked to
 * transcribe near-silence invents words rather than returning nothing, and those
 * inventions arrive flagged `definite`, so the interview treats them as the candidate
 * speaking and answers them. Observed in a real session as Mandarin fragments appearing
 * while nobody was talking.
 *
 * The gate is adaptive rather than a fixed threshold, because `autoGainControl` on the
 * capture track raises quiet rooms until any absolute cut-off is either deaf in one room
 * or wide open in another.
 *
 * The floor is the QUIETEST packet in a recent window rather than a running average. An
 * average - or any estimator that only learns while it believes the room is quiet -
 * deadlocks in a loud room: room tone reads as speech, so the estimator never updates,
 * so room tone keeps reading as speech and the gate never closes. A windowed minimum
 * cannot deadlock, because speech has gaps and the quietest moment in ten seconds is
 * room tone in any room.
 *
 * The margins are deliberately permissive. The two ways this can be wrong are not
 * equally bad: letting some room tone through costs a stray recognition, which the
 * pipeline already defends against (de-duplication, a minimum utterance length, and a
 * barge-in threshold), whereas gating out a softly-spoken candidate makes the interview
 * deaf with no recovery and no error message. Tuned against simulated quiet and
 * AGC-boosted rooms crossed with normal, soft and very soft speech: 1.8 was the loosest
 * setting that still fully rejected silence in both room types.
 *
 * HANGOVER_PACKETS must exceed the recogniser's `end_window_size` (800 ms): the server
 * decides an utterance has ended by hearing silence, so cutting the audio off the moment
 * speech stops means the last sentence is never finalised and the interviewer never
 * replies. Trailing silence is not waste - it is the end-of-turn signal.
 */
const SPEECH_OVER_FLOOR = 1.8; // multiplicative margin over the floor
const SPEECH_OVER_FLOOR_ABS = 0.004; // additive margin, for very quiet rooms
const ABSOLUTE_FLOOR = 0.004; // never treat anything below this as speech
const HANGOVER_PACKETS = 14; // 1.4 s, comfortably past end_window_size
const FLOOR_WINDOW_PACKETS = 100; // 10 s - long enough to contain a real pause

class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.pending = new Float32Array(0);
    // Fractional read position into the incoming stream, carried across render quanta so
    // resampling does not restart (and click) every 128 samples.
    this.cursor = 0;
    this.muted = false;
    this.recent = []; // rolling RMS window; its minimum is the noise floor
    this.hangover = 0;
    // Whether the gate is currently passing audio. Reported on every change, because
    // some recognisers cannot work out where an utterance ends by themselves and this
    // is the only place that already knows - see the gate note below.
    this.gateOpen = false;
    this.port.onmessage = (event) => {
      if (event.data?.type === "mute") {
        this.muted = Boolean(event.data.value);
        // Muting stops audio without the gate ever closing, so say so explicitly.
        // Otherwise a candidate who mutes mid-sentence leaves an utterance open and
        // waits on the server's silence timer to notice.
        if (this.muted) this.setGate(false);
      }
    };
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;

    // Muting has to drop samples here rather than stopping capture: tearing the track
    // down and rebuilding it renegotiates the mic and takes long enough to clip the
    // start of whatever the candidate says next.
    if (this.muted) return true;

    const merged = new Float32Array(this.pending.length + channel.length);
    merged.set(this.pending);
    merged.set(channel, this.pending.length);

    const ratio = sampleRate / TARGET_RATE;
    const out = [];
    let cursor = this.cursor;
    while (cursor + 1 < merged.length) {
      const index = Math.floor(cursor);
      const frac = cursor - index;
      // Linear interpolation. Cheap, and at these rates the artefacts sit well above
      // the speech band the recogniser cares about.
      out.push(merged[index] * (1 - frac) + merged[index + 1] * frac);
      cursor += ratio;
    }

    const consumed = Math.floor(cursor);
    this.pending = merged.slice(consumed);
    this.cursor = cursor - consumed;

    if (out.length) this.emit(out);
    return true;
  }

  /** Report a change in whether audio is flowing. Edges only - this runs per packet. */
  setGate(open) {
    if (open === this.gateOpen) return;
    this.gateOpen = open;
    if (!open) this.hangover = 0;
    this.port.postMessage({ type: "speech", value: open });
  }

  emit(samples) {
    this.buffer = this.buffer ? this.buffer.concat(samples) : samples;
    while (this.buffer.length >= PACKET_SAMPLES) {
      const packet = this.buffer.slice(0, PACKET_SAMPLES);
      this.buffer = this.buffer.slice(PACKET_SAMPLES);

      let sum = 0;
      for (let i = 0; i < packet.length; i += 1) sum += packet[i] * packet[i];
      const rms = Math.sqrt(sum / packet.length);

      this.recent.push(rms);
      if (this.recent.length > FLOOR_WINDOW_PACKETS) this.recent.shift();
      let floor = Infinity;
      for (let i = 0; i < this.recent.length; i += 1) {
        if (this.recent[i] < floor) floor = this.recent[i];
      }

      const threshold = Math.max(
        floor * SPEECH_OVER_FLOOR,
        floor + SPEECH_OVER_FLOOR_ABS,
        ABSOLUTE_FLOOR,
      );
      const speaking = rms > threshold;
      if (speaking) this.hangover = HANGOVER_PACKETS;
      else if (this.hangover > 0) this.hangover -= 1;

      this.setGate(speaking || this.hangover > 0);
      if (!this.gateOpen) continue;

      const pcm = new Int16Array(packet.length);
      for (let i = 0; i < packet.length; i += 1) {
        const clamped = Math.max(-1, Math.min(1, packet[i]));
        pcm[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
      }
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }
  }
}

registerProcessor("mic-processor", MicProcessor);
