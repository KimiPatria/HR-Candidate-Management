import { Icon, Intent, Section, SectionCard, Tag } from "@blueprintjs/core";
import { useEffect, useRef } from "react";

import { AGENT_STATUS, useSteadyStatus, type AgentStatus } from "../lib/agentStatus";
import type { Turn } from "../lib/api";

export interface PartialCaption {
  speaker: string;
  text: string;
}

export default function TranscriptPanel({
  turns,
  partial,
  connected,
  agentStatus,
}: {
  turns: Turn[];
  partial: PartialCaption | null;
  connected: boolean;
  agentStatus: AgentStatus | null;
}) {
  const bodyRef = useRef<HTMLDivElement>(null);
  // Held on screen long enough to read - see useSteadyStatus.
  const steady = useSteadyStatus(agentStatus);
  const view = steady ? AGENT_STATUS[steady] : null;

  useEffect(() => {
    // Follow the conversation as it grows.
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight, behavior: "smooth" });
  }, [turns.length, partial?.text]);

  return (
    <Section
      className="transcript-section"
      title="Transcript"
      icon="chat"
      compact
      rightElement={
        <Tag
          minimal
          round
          intent={connected ? Intent.SUCCESS : Intent.WARNING}
          icon={connected ? "record" : "refresh"}
        >
          {connected ? "live" : "reconnecting"}
        </Tag>
      }
    >
      {view && (
        /*
         * Above the transcript rather than inside it: this is the interviewer's state
         * right now, not a thing it said, and letting it scroll away with the history
         * would defeat the point. `aria-live` so a screen reader announces the change -
         * a blind candidate has even less to go on during a silence than a sighted one.
         */
        <div
          className={`agent-status intent-${steady}`}
          role="status"
          aria-live="polite"
        >
          <Icon icon={view.icon} size={12} />
          <span className="agent-status-label">{view.label}</span>
          {view.working && (
            <span className="agent-status-dots" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
          )}
        </div>
      )}
      <SectionCard padded={false}>
        <div className="transcript-body" ref={bodyRef}>
          {turns.length === 0 && !partial && (
            <p className="bp6-text-muted">The conversation will appear here.</p>
          )}
          {turns.map((turn) => (
            <div key={turn.id} className={`turn ${turn.speaker}`}>
              <div
                className={`who ${
                  turn.speaker === "ai" ? "bp6-text-intent-primary" : "bp6-text-muted"
                }`}
              >
                {turn.speaker === "ai" ? "Interviewer" : "You"}
              </div>
              <div>{turn.text}</div>
              {turn.guardrail_action && (
                <Tag minimal intent={Intent.WARNING} icon="shield" style={{ marginTop: 4 }}>
                  guardrail: {turn.guardrail_action}
                </Tag>
              )}
            </div>
          ))}
          {partial && (
            <div className={`turn partial ${partial.speaker}`}>
              <div
                className={`who ${
                  partial.speaker === "ai" ? "bp6-text-intent-primary" : "bp6-text-muted"
                }`}
              >
                {partial.speaker === "ai" ? "Interviewer" : "You"} (speaking)
              </div>
              <div>{partial.text}</div>
            </div>
          )}
        </div>
      </SectionCard>
    </Section>
  );
}
