import { Intent } from "@blueprintjs/core";
import type { IconName } from "@blueprintjs/icons";

/**
 * Statuses arrive from the backend as free-form strings on four different shapes
 * (interview, document index, session, candidate). Mapping them in one place keeps a
 * "failed" tag looking the same wherever it turns up.
 */
const INTENTS: Record<string, Intent> = {
  // Terminal, good.
  indexed: Intent.SUCCESS,
  completed: Intent.SUCCESS,
  ready: Intent.SUCCESS,
  // In flight.
  in_progress: Intent.PRIMARY,
  indexing: Intent.PRIMARY,
  // Waiting on someone.
  pending: Intent.WARNING,
  draft: Intent.WARNING,
  created: Intent.WARNING,
  not_ready: Intent.WARNING,
  // Terminal, bad.
  failed: Intent.DANGER,
  expired: Intent.DANGER,
};

const ICONS: Record<string, IconName> = {
  indexed: "tick-circle",
  completed: "tick-circle",
  ready: "tick-circle",
  in_progress: "record",
  indexing: "refresh",
  pending: "time",
  draft: "edit",
  created: "envelope",
  not_ready: "warning-sign",
  failed: "error",
  expired: "disable",
};

export function statusIntent(status: string): Intent {
  return INTENTS[status] ?? Intent.NONE;
}

export function statusIcon(status: string): IconName | undefined {
  return ICONS[status];
}

/** "in_progress" reads badly in a tag; show "in progress". */
export function statusLabel(status: string): string {
  return status.replace(/_/g, " ");
}
