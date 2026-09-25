import { Tooltip } from "@blueprintjs/core";
import type { ReactNode } from "react";

/**
 * A heading, label or graphic that can explain itself on demand.
 *
 * Every static "what this thing means" gloss in the app goes through here rather than
 * sitting permanently under the thing it describes. HR reads those sentences once, on
 * their first day; after that they are furniture between the reader and the candidate.
 *
 * Three rules this enforces so a hidden explanation does not become a lost one:
 *
 *  - The target is visibly marked (dotted underline, help cursor). An explanation nobody
 *    can tell is there is worse than one that takes up room.
 *  - It opens on keyboard focus as well as hover, so the explanation is not mouse-only.
 *  - It opens BELOW, after a deliberate pause. A tooltip that fires instantly on every
 *    heading the pointer crosses on its way somewhere else is noise, not help.
 *
 * What deliberately does NOT belong in here: anything the reader has to act on (device
 * checks, consent), anything about this particular candidate rather than the field in
 * general, and warnings. Those stay on the page.
 */

/** Long enough to mean "I stopped here", short enough not to read as broken. */
const OPEN_DELAY_MS = 700;

export default function Explain({
  text,
  children,
  bare = false,
}: {
  text: ReactNode;
  children: ReactNode;
  /**
   * The child is already a focusable control (a Button, say). Skips the wrapper, because
   * a focusable span inside a button is two tab stops for one thing and screen readers
   * announce the mess. The caller owns the visual hint in that case - `explain-hint`.
   */
  bare?: boolean;
}) {
  const tooltip = (
    <Tooltip
      content={<div className="explain-tooltip">{text}</div>}
      placement="bottom"
      compact
      hoverOpenDelay={OPEN_DELAY_MS}
    >
      {bare ? (
        children
      ) : (
        <span className="explain-target" tabIndex={0}>
          {children}
        </span>
      )}
    </Tooltip>
  );
  return tooltip;
}
