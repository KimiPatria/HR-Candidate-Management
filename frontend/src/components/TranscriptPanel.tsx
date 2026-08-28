import { useEffect, useRef } from "react";

import type { Turn } from "../lib/api";

export interface PartialCaption {
  speaker: string;
  text: string;
}

export default function TranscriptPanel({
  turns,
  partial,
  connected,
}: {
  turns: Turn[];
  partial: PartialCaption | null;
  connected: boolean;
}) {
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Follow the conversation as it grows.
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight, behavior: "smooth" });
  }, [turns.length, partial?.text]);

  return (
    <div className="transcript">
      <header>
        <span>Transcript</span>
        <span className={`badge ${connected ? "indexed" : "pending"}`}>
          {connected ? "live" : "reconnecting"}
        </span>
      </header>
      <div className="transcript-body" ref={bodyRef}>
        {turns.length === 0 && !partial && (
          <p className="muted">The conversation will appear here.</p>
        )}
        {turns.map((turn) => (
          <div key={turn.id} className={`turn ${turn.speaker}`}>
            <div className="who">{turn.speaker === "ai" ? "Interviewer" : "You"}</div>
            <div>{turn.text}</div>
            {turn.guardrail_action && (
              <div className="flag">guardrail: {turn.guardrail_action}</div>
            )}
          </div>
        ))}
        {partial && (
          <div className={`turn partial ${partial.speaker}`}>
            <div className="who">
              {partial.speaker === "ai" ? "Interviewer" : "You"} (speaking)
            </div>
            <div>{partial.text}</div>
          </div>
        )}
      </div>
    </div>
  );
}
