/**
 * What the interviewer is doing right now, in words the candidate can act on.
 *
 * Silence in a voice interview is ambiguous in a way it never is with a human: a
 * two-second pause could be the model thinking, the network stalling, or the microphone
 * being dead, and the candidate's reaction to those three is completely different. This
 * is the strip that tells them which one it is.
 *
 * The ids come from the backend (`agent_status` events on the transcript socket, emitted
 * from services/voice/pipeline.py at the points where the work actually changes) so the
 * label is not a guess about timing - it is the pipeline saying what stage it is in.
 * Two of them are set by the browser instead, because it knows sooner: `paused` the
 * moment the mute button is pressed, and `connecting` before any socket exists.
 */

import { Intent } from "@blueprintjs/core";
import type { IconName } from "@blueprintjs/icons";
import { useEffect, useRef, useState } from "react";

export type AgentStatus =
  | "connecting"
  | "listening"
  | "hearing"
  | "thinking"
  | "retrieving"
  | "composing"
  | "speaking"
  | "paused"
  | "ended";

export interface AgentStatusView {
  label: string;
  icon: IconName;
  intent: Intent;
  /** Whether the interviewer owes the candidate a reply - drives the working animation. */
  working: boolean;
}

/**
 * Labels are written from the candidate's point of view and in the present continuous,
 * because that is what makes a pause feel like someone doing something rather than a
 * stalled page. They also stay honest about the machine underneath - "checking the role
 * brief" is retrieval over the uploaded documents, not a euphemism for thinking.
 */
export const AGENT_STATUS: Record<AgentStatus, AgentStatusView> = {
  connecting: {
    label: "Connecting",
    icon: "refresh",
    intent: Intent.NONE,
    working: true,
  },
  listening: {
    label: "Listening",
    icon: "headset",
    intent: Intent.SUCCESS,
    working: false,
  },
  hearing: {
    label: "Listening to your answer",
    icon: "record",
    intent: Intent.SUCCESS,
    working: false,
  },
  thinking: {
    label: "Thinking about your answer",
    icon: "lightbulb",
    intent: Intent.PRIMARY,
    working: true,
  },
  retrieving: {
    label: "Checking the role brief",
    icon: "search-text",
    intent: Intent.PRIMARY,
    working: true,
  },
  composing: {
    label: "Formulating a response",
    icon: "edit",
    intent: Intent.PRIMARY,
    working: true,
  },
  speaking: {
    label: "Speaking",
    icon: "volume-up",
    intent: Intent.PRIMARY,
    working: false,
  },
  paused: {
    label: "Paused - your mic is muted",
    icon: "disable",
    intent: Intent.WARNING,
    working: false,
  },
  ended: {
    label: "Interview ended",
    icon: "tick-circle",
    intent: Intent.NONE,
    working: false,
  },
};

const KNOWN = new Set(Object.keys(AGENT_STATUS));

/** Narrow an id off the wire, so a status added server-side does not blank the strip. */
export function toAgentStatus(value: unknown): AgentStatus | null {
  return typeof value === "string" && KNOWN.has(value) ? (value as AgentStatus) : null;
}

/**
 * How long a status must stay on screen before the next one may replace it.
 *
 * The turn pipeline can pass through guardrails, retrieval and the first token in well
 * under a tenth of a second on a cached path, and rendering that faithfully is a strobe
 * of three labels nobody can read. Holding each one briefly makes the sequence legible
 * without ever showing something that is not happening - the label is always one the
 * pipeline really reported, just held a beat longer.
 */
const MIN_DWELL_MS = 450;

/**
 * Rate-limit status changes to something readable.
 *
 * Only the newest pending status is kept: if three arrive during one dwell, the two
 * stale ones are dropped rather than queued, because showing "thinking" after the
 * interviewer has already started speaking would be a lie in a way that skipping it is
 * not.
 */
export function useSteadyStatus(status: AgentStatus | null): AgentStatus | null {
  const [shown, setShown] = useState<AgentStatus | null>(status);
  const pending = useRef<AgentStatus | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (status === shown) return;

    // Mid-dwell: remember the newest and let the running timer pick it up.
    if (timer.current !== null) {
      pending.current = status;
      return;
    }

    setShown(status);
    const tick = () => {
      const next = pending.current;
      pending.current = null;
      if (next === null) {
        timer.current = null;
        return;
      }
      setShown(next);
      timer.current = setTimeout(tick, MIN_DWELL_MS);
    };
    timer.current = setTimeout(tick, MIN_DWELL_MS);
  }, [status, shown]);

  useEffect(
    () => () => {
      if (timer.current !== null) clearTimeout(timer.current);
    },
    [],
  );

  return shown;
}
