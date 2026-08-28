/**
 * BytePlus RTC Web SDK wrapper.
 *
 * The SDK is loaded with a dynamic import and is OPTIONAL: if `@byteplus/rtc` is not
 * installed the page falls back to a mock engine so the whole interview flow - join,
 * transcript, end - is still clickable end to end. Install it when you are ready to go
 * live:
 *
 *     npm install @byteplus/rtc
 *
 * VERIFY: the SDK method names below (createEngine / joinRoom / setRemoteVideoPlayer /
 * startAudioCapture) against the BytePlus RTC Web SDK docs for your SDK version. The
 * avatar arrives as a normal remote video stream published by the agent user, which is
 * why there is no avatar-specific call here.
 */

import type { RTCCredentials } from "./api";

const SDK_PACKAGE = "@byteplus/rtc";

export type EngineMode = "live" | "mock";

export interface InterviewEngine {
  mode: EngineMode;
  join(container: HTMLElement): Promise<void>;
  leave(): Promise<void>;
  setMicMuted(muted: boolean): Promise<void>;
}

export interface EngineCallbacks {
  onRemoteVideo?: () => void;
  onError?: (message: string) => void;
}

export async function createEngine(
  creds: RTCCredentials,
  callbacks: EngineCallbacks = {},
): Promise<InterviewEngine> {
  try {
    // Indirect specifier on purpose: it keeps TypeScript and the bundler from resolving
    // the SDK at build time, so the project compiles and runs before it is installed.
    const specifier = SDK_PACKAGE;
    const module = await import(/* @vite-ignore */ specifier);
    return createLiveEngine(module, creds, callbacks);
  } catch {
    console.warn(
      "[rtc] @byteplus/rtc is not installed - running the interview in mock mode. " +
        "Run `npm install @byteplus/rtc` to connect to a real room.",
    );
    return createMockEngine(callbacks);
  }
}

/* eslint-disable @typescript-eslint/no-explicit-any */
function createLiveEngine(
  module: any,
  creds: RTCCredentials,
  callbacks: EngineCallbacks,
): InterviewEngine {
  const VERTC = module.default ?? module;
  let engine: any = null;

  return {
    mode: "live",

    async join(container: HTMLElement) {
      engine = VERTC.createEngine(creds.app_id);

      // The avatar publishes video into the room like any other participant; render
      // whatever remote stream shows up.
      engine.on(VERTC.events.onUserPublishStream, async (event: any) => {
        await engine.setRemoteVideoPlayer(0, {
          userId: event.userId,
          renderDom: container,
        });
        callbacks.onRemoteVideo?.();
      });

      engine.on(VERTC.events.onError, (event: any) => {
        callbacks.onError?.(String(event?.errorCode ?? event));
      });

      await engine.joinRoom(
        creds.token,
        creds.room_id,
        { userId: creds.user_id },
        {
          isAutoPublish: true,
          isAutoSubscribeAudio: true,
          isAutoSubscribeVideo: true,
        },
      );

      // Microphone only - the candidate is heard, not seen.
      await engine.startAudioCapture();
    },

    async leave() {
      if (!engine) return;
      try {
        await engine.stopAudioCapture();
        await engine.leaveRoom();
      } finally {
        VERTC.destroyEngine(engine);
        engine = null;
      }
    },

    async setMicMuted(muted: boolean) {
      if (!engine) return;
      if (muted) await engine.stopAudioCapture();
      else await engine.startAudioCapture();
    },
  };
}
/* eslint-enable @typescript-eslint/no-explicit-any */

function createMockEngine(callbacks: EngineCallbacks): InterviewEngine {
  return {
    mode: "mock",
    async join() {
      // Let the page render its placeholder stage immediately.
      callbacks.onRemoteVideo?.();
    },
    async leave() {},
    async setMicMuted() {},
  };
}
