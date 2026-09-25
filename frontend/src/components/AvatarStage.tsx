import { Callout, Intent } from "@blueprintjs/core";
import { useEffect, useRef } from "react";

import type { AudioTap } from "../lib/audioTap";
import type { EngineMode } from "../lib/rtc";
import VoiceVisualizer from "./VoiceVisualizer";

/**
 * Where the Flash Avatar video lands. The RTC SDK renders the remote stream into the
 * container div, so this component owns the element and hands the ref upward rather
 * than managing any media itself.
 *
 * Intentionally not a Blueprint Card - this is a black video surface, and Blueprint
 * only supplies the secondary chrome (status callouts, the controls in `children`).
 *
 * The voice visualiser appears either way: full size in the middle of a voice-only
 * interview, and as a compact strip floating over the video when an avatar is present.
 * Even with a talking face on screen, the strip is the only thing that shows whether the
 * candidate's own microphone is being heard.
 */
export default function AvatarStage({
  onContainerReady,
  hasVideo,
  mode,
  status,
  tap,
  children,
}: {
  onContainerReady: (el: HTMLDivElement) => void;
  hasVideo: boolean;
  mode: EngineMode | null;
  status: string;
  tap: AudioTap | null;
  children?: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (ref.current) onContainerReady(ref.current);
  }, [onContainerReady]);

  return (
    <div className="avatar-stage">
      <div ref={ref} style={{ width: "100%", height: "100%" }} />
      {hasVideo ? (
        <div className="visualizer-strip">
          <VoiceVisualizer tap={tap} variant="strip" />
        </div>
      ) : (
        <div className="avatar-placeholder">
          <VoiceVisualizer tap={tap} variant="stage" />
          <div className="bp6-heading" style={{ margin: "18px 0 12px" }}>
            {status}
          </div>
          {mode === "mock" && (
            <Callout intent={Intent.WARNING} icon="lab-test" compact>
              RTC SDK not installed — running without audio or video. Install
              @byteplus/rtc to connect a real room.
            </Callout>
          )}
          {mode === "local" && (
            <Callout intent={Intent.PRIMARY} icon="headset" compact>
              Voice-only interview — speak normally and the interviewer will reply.
            </Callout>
          )}
        </div>
      )}
      <div className="stage-overlay">{children}</div>
    </div>
  );
}
