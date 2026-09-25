/**
 * Picks the voice engine for an interview.
 *
 * The backend decides - `/join/{token}/start` returns `voice_mode` - because the choice
 * depends on which BytePlus credentials the deployment actually has, which is server
 * knowledge. The page just builds whatever it is told to and drives it through the
 * shared `InterviewEngine` interface.
 *
 *   "rtc"    BytePlus RTC room; their managed agent runs ASR, TTS and the turn loop.
 *   "local"  one WebSocket to our backend, which drives Seed ASR/TTS itself.
 *
 * The device choice from the pre-flight check is passed straight through: which
 * microphone and speaker to use is a candidate decision, not a pipeline one, so both
 * engines take the same `DeviceChoice` and apply it in whatever way their SDK allows.
 *
 * Adding a third pipeline means adding a case here and a file next to it; nothing in
 * InterviewPage changes.
 */

import type { RTCCredentials } from "./api";
import { createLocalEngine } from "./localVoice";
import {
  createEngine as createRtcEngine,
  type DeviceChoice,
  type EngineCallbacks,
  type InterviewEngine,
} from "./rtc";

export async function createInterviewEngine(
  creds: RTCCredentials,
  token: string,
  callbacks: EngineCallbacks = {},
  devices: DeviceChoice = {},
): Promise<InterviewEngine> {
  if (creds.voice_mode === "local") {
    return createLocalEngine(token, callbacks, devices);
  }
  return createRtcEngine(creds, callbacks, devices);
}

export type { DeviceChoice, EngineCallbacks, InterviewEngine };
