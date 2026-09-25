import type { Candidate, Verdict } from "../lib/api";
import { verdictLabel } from "../lib/verdict";

/**
 * At-a-glance breakdown of the candidates currently shown on the table below - the
 * question this answers is "how is this hiring round going", which a sorted list of rows
 * cannot answer without reading every row. Reacts to the page's own search/status filter
 * so "how is this position going" is just filtering down to it.
 *
 * "Not yet scored" folds together no-verdict-yet and scoring-failed on purpose: from an
 * HR reader's chair both mean the same thing right now - this candidate has no usable
 * verdict and needs attention - and splitting them into two similar, adjacent segments
 * would cost more in visual confusion than the distinction is worth here. The table rows
 * still show the failed state separately for anyone who needs it.
 */
const SEGMENTS: { key: Verdict | "not_scored"; label: string; cssVar: string }[] = [
  { key: "strong_fit", label: verdictLabel("strong_fit"), cssVar: "--verdict-strong" },
  { key: "decent_fit", label: verdictLabel("decent_fit"), cssVar: "--verdict-decent" },
  { key: "not_a_fit", label: verdictLabel("not_a_fit"), cssVar: "--verdict-not-fit" },
  { key: "inconclusive", label: verdictLabel("inconclusive"), cssVar: "--verdict-inconclusive" },
  { key: "not_scored", label: "Not yet scored", cssVar: "--verdict-none" },
];

export default function VerdictSummary({ candidates }: { candidates: Candidate[] }) {
  if (candidates.length === 0) return null;

  const counts: Record<string, number> = Object.fromEntries(
    SEGMENTS.map((s) => [s.key, 0]),
  );
  for (const c of candidates) {
    counts[c.verdict ?? "not_scored"] += 1;
  }

  const total = candidates.length;
  const scored = total - counts.not_scored;
  const present = SEGMENTS.filter((s) => counts[s.key] > 0);

  return (
    <div className="verdict-summary">
      <div className="verdict-summary-stats">
        <div className="stat-tile">
          <div className="stat-value">{total}</div>
          <div className="stat-label">candidate{total === 1 ? "" : "s"}</div>
        </div>
        <div className="stat-tile">
          <div className="stat-value">{scored}</div>
          <div className="stat-label">scored</div>
        </div>
      </div>

      <div
        className="verdict-bar"
        role="img"
        aria-label={present
          .map((s) => `${s.label}: ${counts[s.key]} of ${total}`)
          .join(", ")}
      >
        {present.map((s) => (
          <div
            key={s.key}
            className="verdict-bar-segment"
            style={{
              width: `${(counts[s.key] / total) * 100}%`,
              background: `var(${s.cssVar})`,
            }}
            title={`${s.label} — ${counts[s.key]} (${Math.round((counts[s.key] / total) * 100)}%)`}
          />
        ))}
      </div>

      <div className="verdict-summary-legend">
        {present.map((s) => (
          <span key={s.key} className="legend-item">
            <span className="legend-dot" style={{ background: `var(${s.cssVar})` }} />
            {s.label} <span className="bp6-text-muted">{counts[s.key]}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
