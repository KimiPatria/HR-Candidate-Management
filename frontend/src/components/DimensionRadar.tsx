import Explain from "./Explain";
import type { DimensionVerdict, EvaluationDimension } from "../lib/api";
import { dimensionLabel } from "../lib/verdict";

/**
 * The three dimension verdicts as one shape, so the balance between them is readable
 * before any of the prose below is. A candidate strong on background but thin on
 * reasoning has a visibly lopsided triangle; that asymmetry is the thing HR is scanning
 * for, and it does not survive being spread across three separate cards.
 *
 * Three axes means a triangle, not the pentagon these charts usually are. The geometry
 * below is written for any axis count, so if the fixed dimension set in
 * models/rubric.py ever grows, this draws the pentagon without a change here - but it
 * will not invent axes to reach a nicer polygon. Every corner is a real judged
 * dimension.
 *
 * NOT EVIDENCED SITS AT THE CENTRE, and that is the one thing on this chart that can
 * mislead: geometrically it looks identical to "worst possible", but it means the
 * transcript never touched the dimension at all - the distinction lib/verdict.ts keeps
 * a separate neutral colour for. So that axis is drawn dashed with a hollow vertex, and
 * the chart's own explanation (see Explain) spells it out for anyone who hovers. A
 * reader who only looks at the shape must not read an unasked question as a failed one.
 */

// Radius, in rings out from the centre. Four outcomes, three rings.
const LEVEL: Record<DimensionVerdict, number> = {
  not_evidenced: 0,
  not_a_fit: 1,
  decent: 2,
  strong: 3,
};

// Same status palette as the Tags in the breakdown below and the summary bar on the
// candidates list - one mapping of tier to colour across the whole app, defined in
// index.css. not_evidenced deliberately has no fill: an absence should not read as a
// coloured result.
const VERTEX_FILL: Record<DimensionVerdict, string> = {
  strong: "var(--verdict-strong)",
  decent: "var(--verdict-decent)",
  not_a_fit: "var(--verdict-not-fit)",
  not_evidenced: "none",
};

/** Axis labels have to fit beside a 240px chart; the full dimension names do not. */
const SHORT_LABELS: Record<string, string> = {
  background_fit: "Background",
  on_the_spot_reasoning: "Reasoning",
  communication_clarity: "Clarity",
};

const SIZE = 240;
const CENTRE = SIZE / 2;
const MAX_R = 74;
const RINGS = 3;

/*
 * The plot is 240 square but the axis labels sit outside it - "Not evidenced" under the
 * bottom-left corner reaches about 36px past the left edge. The viewBox is widened to
 * contain them rather than letting them overflow, which would put them on top of the
 * summary text sharing this row. Kept symmetric about the centre so the triangle stays
 * optically centred in the column.
 */
const PAD_X = 44;
const VIEW_BOX = `${-PAD_X} 0 ${SIZE + PAD_X * 2} ${SIZE - 48}`;

function pointAt(radius: number, index: number, count: number): [number, number] {
  // -90° puts the first axis straight up, so the shape reads the same way every time
  // rather than rotating when the dimension count changes.
  const angle = ((-90 + (360 / count) * index) * Math.PI) / 180;
  return [CENTRE + radius * Math.cos(angle), CENTRE + radius * Math.sin(angle)];
}

function polygon(radius: number, count: number): string {
  return Array.from({ length: count }, (_, i) => pointAt(radius, i, count).join(","))
    .join(" ");
}

export default function DimensionRadar({
  dimensions,
}: {
  dimensions: EvaluationDimension[];
}) {
  // Two axes is a line, one is a dot - neither is a chart. The cards below carry it.
  if (dimensions.length < 3) return null;

  const count = dimensions.length;
  const anyUnevidenced = dimensions.some((d) => d.verdict === "not_evidenced");

  const shape = dimensions
    .map((d, i) => pointAt((LEVEL[d.verdict] / RINGS) * MAX_R, i, count).join(","))
    .join(" ");

  return (
    <Explain
      text={
        <>
          <div>Rings, centre out: not a fit · decent · strong.</div>
          {anyUnevidenced && (
            <div style={{ marginTop: 6 }}>
              A dashed axis means nothing in the transcript spoke to that dimension. That
              is an absence of evidence, not evidence against the candidate.
            </div>
          )}
        </>
      }
    >
      <figure className="dimension-radar">
        <svg
          viewBox={VIEW_BOX}
          role="img"
          aria-label={dimensions
            .map((d) => `${d.label}: ${dimensionLabel(d.verdict)}`)
            .join(". ")}
        >
        {/* Rings, outermost first so the inner ones stay legible over them. */}
        {Array.from({ length: RINGS }, (_, i) => (
          <polygon
            key={i}
            className="radar-ring"
            points={polygon((MAX_R * (RINGS - i)) / RINGS, count)}
          />
        ))}

        {dimensions.map((d, i) => {
          const [x, y] = pointAt(MAX_R, i, count);
          return (
            <line
              key={d.key}
              className={`radar-spoke${d.verdict === "not_evidenced" ? " unevidenced" : ""}`}
              x1={CENTRE}
              y1={CENTRE}
              x2={x}
              y2={y}
            />
          );
        })}

        {/* The candidate's shape is deliberately colourless: every colour on this chart
            belongs to a dimension's own tier, so the fill cannot be mistaken for one. */}
        <polygon className="radar-shape" points={shape} />

        {dimensions.map((d, i) => {
          const [x, y] = pointAt((LEVEL[d.verdict] / RINGS) * MAX_R, i, count);
          return (
            <circle
              key={d.key}
              className={`radar-vertex${d.verdict === "not_evidenced" ? " unevidenced" : ""}`}
              cx={x}
              cy={y}
              r={5}
              style={{ fill: VERTEX_FILL[d.verdict] }}
            />
          );
        })}

        {dimensions.map((d, i) => {
          const [x, y] = pointAt(MAX_R + 16, i, count);
          const angle = ((-90 + (360 / count) * i) * Math.PI) / 180;
          const cos = Math.cos(angle);
          const anchor = Math.abs(cos) < 0.2 ? "middle" : cos > 0 ? "start" : "end";
          // The two lines grow away from the centre, never back towards it: on an axis
          // pointing up, a second line placed below the first lands on top of its own
          // vertex.
          const first = Math.sin(angle) < -0.2 ? y - 13 : y;
          return (
            <g key={d.key} className="radar-label">
              <text x={x} y={first} textAnchor={anchor}>
                {SHORT_LABELS[d.key] ?? d.label}
              </text>
              <text
                x={x}
                y={first + 13}
                textAnchor={anchor}
                className="radar-label-verdict"
              >
                {dimensionLabel(d.verdict)}
              </text>
            </g>
          );
        })}
        </svg>
      </figure>
    </Explain>
  );
}
