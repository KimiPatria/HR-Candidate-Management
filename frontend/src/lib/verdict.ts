import { Intent } from "@blueprintjs/core";
import type { IconName } from "@blueprintjs/icons";

import type {
  DimensionVerdict,
  InterviewCompleteness,
  TranscriptQuality,
  Verdict,
} from "./api";

/**
 * How the four tiers look, in one place, the way lib/status.ts does for session states.
 *
 * Two colour choices worth stating, because both are load-bearing rather than aesthetic:
 *
 * - Decent Fit is blue, not amber. Amber reads as "something went wrong"; a mixed
 *   candidate is a normal outcome, not a warning.
 * - Inconclusive is amber and Not a Fit is red, and they must never be confused for each
 *   other. Not a Fit is a judgement about the candidate. Inconclusive is a judgement
 *   about the transcript, and the candidate is owed a re-interview rather than a
 *   rejection. Making them share a colour would quietly turn our transcription failures
 *   into their rejections.
 */
const VERDICT_INTENTS: Record<Verdict, Intent> = {
  strong_fit: Intent.SUCCESS,
  decent_fit: Intent.PRIMARY,
  not_a_fit: Intent.DANGER,
  inconclusive: Intent.WARNING,
};

const VERDICT_ICONS: Record<Verdict, IconName> = {
  strong_fit: "endorsed",
  decent_fit: "tick-circle",
  not_a_fit: "cross-circle",
  inconclusive: "help",
};

const VERDICT_LABELS: Record<Verdict, string> = {
  strong_fit: "Strong Fit",
  decent_fit: "Decent Fit",
  not_a_fit: "Not a Fit",
  inconclusive: "Inconclusive",
};

/** The one-line gloss shown under the tier, so a reader who has never seen this page
 *  knows what the label is claiming without opening documentation. */
const VERDICT_BLURBS: Record<Verdict, string> = {
  strong_fit: "Clear, credible evidence across all three dimensions.",
  decent_fit: "Partial or mixed evidence — some dimensions met, others thin or unaddressed.",
  not_a_fit: "Evidence clearly falls short of what this role requires.",
  inconclusive:
    "The transcript was too degraded or incomplete to judge this candidate fairly. This is not a rejection.",
};

export function verdictIntent(verdict: Verdict | null): Intent {
  return verdict ? VERDICT_INTENTS[verdict] : Intent.NONE;
}

export function verdictIcon(verdict: Verdict | null): IconName {
  return verdict ? VERDICT_ICONS[verdict] : "help";
}

export function verdictLabel(verdict: Verdict | null): string {
  return verdict ? VERDICT_LABELS[verdict] : "Not scored";
}

export function verdictBlurb(verdict: Verdict | null): string {
  return verdict ? VERDICT_BLURBS[verdict] : "";
}

// ------------------------------------------------------------ per-dimension

const DIMENSION_INTENTS: Record<DimensionVerdict, Intent> = {
  strong: Intent.SUCCESS,
  decent: Intent.PRIMARY,
  not_a_fit: Intent.DANGER,
  // Neutral on purpose. "Nothing was said about this" is an absence, not a failing, and
  // colouring it red would score the candidate for a question nobody got round to asking.
  not_evidenced: Intent.NONE,
};

const DIMENSION_ICONS: Record<DimensionVerdict, IconName> = {
  strong: "endorsed",
  decent: "tick-circle",
  not_a_fit: "cross-circle",
  not_evidenced: "circle",
};

const DIMENSION_LABELS: Record<DimensionVerdict, string> = {
  strong: "Strong",
  decent: "Decent",
  not_a_fit: "Not a fit",
  not_evidenced: "Not evidenced",
};

export function dimensionIntent(verdict: DimensionVerdict): Intent {
  return DIMENSION_INTENTS[verdict] ?? Intent.NONE;
}

export function dimensionIcon(verdict: DimensionVerdict): IconName {
  return DIMENSION_ICONS[verdict] ?? "circle";
}

export function dimensionLabel(verdict: DimensionVerdict): string {
  return DIMENSION_LABELS[verdict] ?? verdict;
}

// -------------------------------------------------------- transcript quality

const QUALITY_INTENTS: Record<TranscriptQuality, Intent> = {
  usable: Intent.SUCCESS,
  degraded: Intent.WARNING,
  unusable: Intent.DANGER,
};

const QUALITY_LABELS: Record<TranscriptQuality, string> = {
  usable: "Transcript usable",
  degraded: "Transcript degraded",
  unusable: "Transcript unusable",
};

const QUALITY_ICONS: Record<TranscriptQuality, IconName> = {
  usable: "tick",
  degraded: "warning-sign",
  unusable: "error",
};

export function qualityIntent(quality: TranscriptQuality | null): Intent {
  return quality ? QUALITY_INTENTS[quality] : Intent.NONE;
}

export function qualityLabel(quality: TranscriptQuality | null): string {
  return quality ? QUALITY_LABELS[quality] : "Transcript not assessed";
}

export function qualityIcon(quality: TranscriptQuality | null): IconName {
  return quality ? QUALITY_ICONS[quality] : "help";
}

// ---------------------------------------------------- interview completeness

/**
 * Completeness is a qualifier on the tier, never a tier of its own. A candidate who gave
 * two good answers before the call dropped is still a Decent Fit on what she said - but
 * "Decent Fit" alone, rendered identically to a candidate who talked for fifteen minutes,
 * tells the reader something untrue by omission. So every place the verdict appears, this
 * rides alongside it.
 *
 * Deliberately amber and never red: an interview that did not finish is our problem to
 * fix, not a mark against the person. Red here would read as a judgement on them.
 */
const COMPLETENESS_LABELS: Record<InterviewCompleteness, string> = {
  complete: "Full interview",
  partial: "Partial interview",
};

const COMPLETENESS_ICONS: Record<InterviewCompleteness, IconName> = {
  complete: "tick",
  partial: "stopwatch",
};

/** True when the verdict needs the qualifier shown next to it. */
export function isPartial(completeness: InterviewCompleteness | null): boolean {
  return completeness === "partial";
}

export function completenessIntent(
  completeness: InterviewCompleteness | null,
): Intent {
  return completeness === "partial" ? Intent.WARNING : Intent.NONE;
}

export function completenessLabel(
  completeness: InterviewCompleteness | null,
): string {
  return completeness ? COMPLETENESS_LABELS[completeness] : "Completeness not assessed";
}

export function completenessIcon(
  completeness: InterviewCompleteness | null,
): IconName {
  return completeness ? COMPLETENESS_ICONS[completeness] : "help";
}

/** What a partial interview means for the verdict above it, in one line. */
export function completenessBlurb(verdict: Verdict | null): string {
  return verdict === "inconclusive"
    ? "The interview ended before it had run its course."
    : "The interview ended early, so this tier rests on only part of a conversation. Dimensions that were never asked about are marked Not evidenced below.";
}
