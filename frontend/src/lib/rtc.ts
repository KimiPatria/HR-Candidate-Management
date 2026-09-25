/**
 * BytePlus RTC Web SDK wrapper.
 *
 * `@byteplus/rtc` is a real dependency (see package.json) and is loaded with a dynamic
 * import using a literal specifier so Vite can statically analyze and pre-bundle it. A
 * *variable* specifier with `/* @vite-ignore *\/` would skip that analysis entirely and
 * fall straight through to the browser's native `import()`, which cannot resolve a bare
 * npm package name without an import map - that used to make this silently fall back to
 * the mock engine even with the package installed. If the import ever does fail (package
 * genuinely missing), the catch below still degrades to a mock engine so the interview
 * flow stays clickable.
 *
 * TRANSPORT is the other thing this file owns - see configureTransport(). Out of the box
 * the SDK only ever reaches the media servers over UDP, which is the difference between
 * "works on a phone hotspot" and "works on office wifi".
 *
 * VERIFY: the SDK method names below (createEngine / joinRoom / setRemoteVideoPlayer /
 * startAudioCapture) against the BytePlus RTC Web SDK docs for your SDK version. The
 * avatar arrives as a normal remote video stream published by the agent user, which is
 * why there is no avatar-specific call here.
 */

import type { RTCCredentials } from "./api";
import { SILENT_TAP, type AudioTap, type VoiceSide } from "./audioTap";

// "live" = a real BytePlus RTC room, "local" = our own WebSocket voice pipeline (see
// localVoice.ts), "mock" = neither is reachable and the page stays clickable anyway.
export type EngineMode = "live" | "local" | "mock";

// How long after joinRoom() we wait for the connection to actually complete before
// telling the candidate the network is the problem, instead of leaving them on
// "Connecting..." forever. Has to clear TCP_RACE_DELAY_MS plus a full TCP-only ICE
// handshake, which through a slow corporate proxy is several seconds more.
const CONNECT_TIMEOUT_MS = 20_000;

// How long the SDK waits on the UDP attempt before *also* racing a TCP-only one.
const TCP_RACE_DELAY_MS = 2_000;

// BytePlus cloud proxy: fixed domains (port 443) that stand in for the RTC edge's
// otherwise-dynamic IPs, so a firewall can actually allow them. Requires the AppID to be
// provisioned for cloud proxy on BytePlus's side (via their technical support) - if it
// isn't, startCloudProxy() below just fails to connect to the proxy and the engine falls
// back to whatever configureTransport() above already does. Values are BytePlus's fixed
// Web SDK cloud-proxy endpoints, not account-specific.
// How often BytePlus reports per-user volume once enableAudioPropertiesReport() is on.
// 100 ms is the SDK's floor - anything smaller is silently reset to this - so on the RTC
// path the visualiser is interpolating between ten real readings a second. That is the
// hard limit of this pipeline, and the reason the local one taps the graph directly.
const AUDIO_REPORT_INTERVAL_MS = 100;

// linearVolume arrives 0..255. Speech in a normal room reports roughly 60-150, so
// dividing by the full range would leave the bars barely moving; this scales a
// conversational voice to most of the height and clamps the shouting case.
const RTC_VOLUME_SCALE = 160;

const CLOUD_PROXY_CONFIG = {
  logProxy: "rtc-log-report-src.zijieapi.com",
  accessProxy: ["rtc-access-src.zijieapi.com", "rtc-access-src-hl.zijieapi.com"],
  configProxy: "rtc-src.zijieapi.com",
};

export interface InterviewEngine {
  mode: EngineMode;
  join(container: HTMLElement): Promise<void>;
  leave(): Promise<void>;
  setMicMuted(muted: boolean): Promise<void>;
  /** Live audio readings for the visualiser. See lib/audioTap.ts. */
  taps: AudioTap;
}

/**
 * Devices the candidate picked in the pre-flight check, if any.
 *
 * Both are optional and both may name a device that has since been unplugged, so every
 * use is best-effort: falling back to the system default is always better than failing
 * to open the microphone at all.
 */
export interface DeviceChoice {
  micDeviceId?: string;
  speakerDeviceId?: string;
}

export interface EngineCallbacks {
  onRemoteVideo?: () => void;
  onError?: (message: string) => void;
  // Fires once the underlying RTC connection actually completes - joining the room and
  // starting mic capture can take several seconds (ICE negotiation), during which the
  // UI would otherwise claim "Interviewer is listening" while audio isn't flowing yet.
  onConnected?: () => void;
  // Connection-path narration: which transport won, state transitions, timeouts. This
  // is what turns "it doesn't work on my wifi" into a specific, checkable claim.
  onDiagnostic?: (note: string) => void;
}

export async function createEngine(
  creds: RTCCredentials,
  callbacks: EngineCallbacks = {},
  devices: DeviceChoice = {},
): Promise<InterviewEngine> {
  try {
    const module = await import("@byteplus/rtc");
    return createLiveEngine(module, creds, callbacks, devices);
  } catch (err) {
    console.warn(
      "[rtc] Failed to load @byteplus/rtc - running the interview in mock mode.",
      err,
    );
    return createMockEngine(callbacks);
  }
}

/* eslint-disable @typescript-eslint/no-explicit-any */

/**
 * Turn on the SDK's TCP fallback paths.
 *
 * MUST run before createEngine(): setParameter("JOIN_ROOM_CONFIG", ...) writes
 * JoinRoomConfig's *static* DEFAULT_CONF, and each engine's JoinRoomConfig instance
 * snapshots those values in its constructor. Setting it afterwards changes nothing for
 * the engine already built.
 *
 * The shipped defaults in @byteplus/rtc 4.69 are
 *   { useTcpAfterJoinTimeout: true, joinWithTcpOnly: false, joinWithTcpOnlyDelay: 5000 }
 *
 * so the *signaling* channel already retries over TCP after a join timeout, but the
 * *media* path never attempts anything except UDP. On any network that drops outbound
 * UDP to BytePlus's edge nodes - plenty of office wifi, and consumer ISP routers with
 * aggressive UDP flood protection - ICE never completes, and the candidate sits on
 * "Connecting..." indefinitely. The same build works over a phone hotspot because
 * cellular NAT lets the UDP straight out. Hosted interview products (LiveKit, Agora,
 * Daily) ship TURN-over-TCP/443 fallback on by default, which is why they work on the
 * exact same wifi this app fails on.
 *
 * `joinWithTcpOnly` is badly named: it does NOT force TCP. Per _startIceConnect in the
 * shipped bundle, the SDK fires the normal UDP attempt at every access node, then
 * `joinWithTcpOnlyDelay` ms later fires a second, TCP-only attempt at the same nodes and
 * keeps whichever connects first, destroying the loser. So on a UDP-capable network this
 * costs one extra candidate-gathering pass and nothing else - UDP still wins the race.
 *
 * Manual override for testing: appending `__rtc_tcp_only__` to the page's query string
 * makes the SDK skip the delay and race TCP immediately (it reads location.search
 * itself). Useful for proving a network is UDP-blocked rather than broken elsewhere.
 */
function configureTransport(VERTC: any, notify?: (note: string) => void): void {
  try {
    VERTC.setParameter("JOIN_ROOM_CONFIG", {
      useTcpAfterJoinTimeout: true,
      joinWithTcpOnly: true,
      joinWithTcpOnlyDelay: TCP_RACE_DELAY_MS,
    });
    notify?.(`transport: UDP first, TCP raced after ${TCP_RACE_DELAY_MS}ms`);
  } catch (err) {
    // Never let transport tuning stop the interview - UDP-only is still worth
    // attempting, it just won't have the fallback.
    console.warn("[rtc] Could not set JOIN_ROOM_CONFIG; UDP-only in effect", err);
    notify?.("transport: TCP fallback unavailable (setParameter rejected)");
  }
}

function connectionStateName(module: any, state: unknown): string {
  const states = module.ConnectionState ?? {};
  return Object.keys(states).find((key) => states[key] === state) ?? String(state);
}

function createLiveEngine(
  module: any,
  creds: RTCCredentials,
  callbacks: EngineCallbacks,
  devices: DeviceChoice,
): InterviewEngine {
  const VERTC = module.default ?? module;
  let engine: any = null;
  let connectTimer: ReturnType<typeof setTimeout> | null = null;
  let connected = false;

  // Latest volume reading per side, written by the SDK's report callbacks and read by
  // the visualiser's animation loop. Deliberately plain numbers rather than React state:
  // these change ten times a second and nothing outside the canvas cares.
  const volumes = { interviewer: 0, candidate: 0 };

  const taps: AudioTap = {
    // The SDK never hands out an AudioNode, so there is no spectrum to be had here.
    analyser: () => null,
    level: (side: VoiceSide) => volumes[side],
  };

  const diagnose = (note: string) => {
    console.info("[rtc]", note);
    callbacks.onDiagnostic?.(note);
  };

  const clearConnectTimer = () => {
    if (connectTimer === null) return;
    clearTimeout(connectTimer);
    connectTimer = null;
  };

  const markConnected = () => {
    if (connected) return;
    connected = true;
    clearConnectTimer();
    callbacks.onConnected?.();
  };

  return {
    mode: "live",

    async join(container: HTMLElement) {
      configureTransport(VERTC, diagnose);
      engine = VERTC.createEngine(creds.app_id);

      // Must be called before joinRoom(). If the AppID isn't provisioned for cloud proxy
      // on BytePlus's side, this is a no-op in practice - onCloudProxyConnected simply
      // never fires and the engine proceeds with the UDP/TCP path from above.
      try {
        engine.startCloudProxy(CLOUD_PROXY_CONFIG);
      } catch (err) {
        console.warn("[rtc] startCloudProxy rejected", err);
      }
      engine.on(VERTC.events.onCloudProxyConnected, (event: any) => {
        diagnose(`cloud proxy connected (join took ${event?.interval ?? "?"}ms)`);
      });

      // The avatar publishes video into the room like any other participant; render it
      // when present. Voice-only interviews publish audio-only (mediaType AUDIO), which
      // the SDK auto-plays without any call here - mediaType must be checked so an
      // audio-only publish doesn't flip hasVideo and hide the "listening" status/orb
      // behind an empty video container.
      engine.on(VERTC.events.onUserPublishStream, async (event: any) => {
        const hasVideoTrack =
          event?.mediaType === module.MediaType?.VIDEO ||
          event?.mediaType === module.MediaType?.AUDIO_AND_VIDEO;
        if (!hasVideoTrack) return;
        await engine.setRemoteVideoPlayer(0, {
          userId: event.userId,
          renderDom: container,
        });
        callbacks.onRemoteVideo?.();
      });

      engine.on(VERTC.events.onError, (event: any) => {
        callbacks.onError?.(String(event?.errorCode ?? event));
      });

      // Both of these mean "UDP did not work here" - the only direct evidence we get
      // that the fallback above is load-bearing on this candidate's network.
      engine.on(VERTC.events.onIceConnectWithTcp, () => {
        diagnose("media connected over TCP - UDP is blocked on this network");
      });
      engine.on(VERTC.events.onRejoinWithTcp, () => {
        diagnose("signaling rejoined over TCP after a UDP join timeout");
      });

      // Volume reporting for the visualiser. Local is the candidate's microphone;
      // remote is every other publisher in the room, which for an interview is just the
      // agent - so the loudest remote is the interviewer. Failing to enable this must
      // not stop the interview: a flat visualiser is a cosmetic loss.
      try {
        engine.enableAudioPropertiesReport({ interval: AUDIO_REPORT_INTERVAL_MS });
        engine.on(VERTC.events.onLocalAudioPropertiesReport, (reports: any[]) => {
          const main = reports?.[0]?.audioPropertiesInfo?.linearVolume ?? 0;
          volumes.candidate = Math.min(1, main / RTC_VOLUME_SCALE);
        });
        engine.on(VERTC.events.onRemoteAudioPropertiesReport, (reports: any[]) => {
          let loudest = 0;
          for (const report of reports ?? []) {
            loudest = Math.max(loudest, report?.audioPropertiesInfo?.linearVolume ?? 0);
          }
          volumes.interviewer = Math.min(1, loudest / RTC_VOLUME_SCALE);
        });
      } catch (err) {
        console.warn("[rtc] Audio level reporting unavailable", err);
      }

      const CONNECTED_STATES = new Set([
        module.ConnectionState?.CONNECTION_STATE_CONNECTED,
        module.ConnectionState?.CONNECTION_STATE_RECONNECTED,
      ]);
      engine.on(VERTC.events.onConnectionStateChanged, (event: any) => {
        diagnose(`connection state: ${connectionStateName(module, event?.state)}`);
        if (CONNECTED_STATES.has(event?.state)) markConnected();
      });

      // Without this the candidate stares at "Connecting..." with no idea whether to
      // wait, reload, or switch networks: joinRoom() resolves once signaling is up and
      // does not reject on a media path that never comes together, so this watchdog is
      // the only thing that catches a UDP-blocked-and-TCP-also-blocked network.
      connectTimer = setTimeout(() => {
        connectTimer = null;
        if (connected) return;
        diagnose("connect timeout - neither UDP nor TCP reached the media servers");
        callbacks.onError?.(
          "Could not reach the interview servers on this network. Both the standard " +
            "(UDP) and the fallback (TCP) connection timed out. A VPN, firewall, or " +
            "restrictive wifi is the usual cause - try another network, or ask IT to " +
            "allow rtc.rtcplus.com and rtc-access-sg.rtcplus.com.",
        );
      }, CONNECT_TIMEOUT_MS);

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

      // Microphone only - the candidate is heard, not seen. The device id comes from
      // the pre-flight check; if that device has gone away since, retry on the system
      // default rather than leaving the candidate with no microphone at all.
      try {
        await engine.startAudioCapture(devices.micDeviceId);
      } catch (err) {
        if (!devices.micDeviceId) throw err;
        diagnose("selected microphone unavailable - falling back to the system default");
        await engine.startAudioCapture();
      }

      if (devices.speakerDeviceId) {
        try {
          await engine.setAudioPlaybackDevice(devices.speakerDeviceId);
        } catch (err) {
          // Unsupported browser, or the speaker was unplugged. Default output still works.
          console.warn("[rtc] Could not set the playback device", err);
        }
      }
    },

    taps,

    async leave() {
      clearConnectTimer();
      if (!engine) return;
      try {
        await engine.stopAudioCapture();
        await engine.leaveRoom();
        // Must be called after leaveRoom() - stopCloudProxy() while still joined throws
        // STOP_CLOUD_PROXY_BEFORE_LEAVE.
        engine.stopCloudProxy();
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
    taps: SILENT_TAP,
    async join() {
      // Let the page render its placeholder stage immediately.
      callbacks.onRemoteVideo?.();
      callbacks.onConnected?.();
    },
    async leave() {},
    async setMicMuted() {},
  };
}
