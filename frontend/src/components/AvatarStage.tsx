import { useEffect, useRef } from "react";

import type { EngineMode } from "../lib/rtc";

/**
 * Where the Flash Avatar video lands. The RTC SDK renders the remote stream into the
 * container div, so this component owns the element and hands the ref upward rather
 * than managing any media itself.
 */
export default function AvatarStage({
  onContainerReady,
  hasVideo,
  mode,
  status,
  children,
}: {
  onContainerReady: (el: HTMLDivElement) => void;
  hasVideo: boolean;
  mode: EngineMode | null;
  status: string;
  children?: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (ref.current) onContainerReady(ref.current);
  }, [onContainerReady]);

  return (
    <div className="avatar-stage">
      <div ref={ref} style={{ width: "100%", height: "100%" }} />
      {!hasVideo && (
        <div className="avatar-placeholder" style={{ position: "absolute" }}>
          <div className="avatar-orb" />
          <div>{status}</div>
          {mode === "mock" && (
            <div className="muted" style={{ marginTop: 8, fontSize: 12 }}>
              RTC SDK not installed - running without audio or video.
              <br />
              Install @byteplus/rtc to connect a real room.
            </div>
          )}
        </div>
      )}
      <div className="stage-overlay">{children}</div>
    </div>
  );
}
