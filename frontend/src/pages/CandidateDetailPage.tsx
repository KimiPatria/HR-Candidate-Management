import {
  Alert,
  Button,
  Callout,
  Card,
  Divider,
  Intent,
  NonIdealState,
  Section,
  SectionCard,
  Spinner,
  Tag,
} from "@blueprintjs/core";
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import DimensionRadar from "../components/DimensionRadar";
import Explain from "../components/Explain";
import {
  TranslateControl,
  TranslateNotice,
  useTranscriptTranslation,
} from "../components/TranscriptTranslate";
import { api, type CandidateDetail, type Evaluation, type Turn } from "../lib/api";
import { statusIcon, statusIntent, statusLabel } from "../lib/status";
import {
  completenessBlurb,
  completenessIcon,
  completenessIntent,
  completenessLabel,
  dimensionIcon,
  dimensionIntent,
  dimensionLabel,
  isPartial,
  qualityIcon,
  qualityIntent,
  qualityLabel,
  verdictBlurb,
  verdictIcon,
  verdictIntent,
  verdictLabel,
} from "../lib/verdict";

/**
 * One candidate, read end to end: the verdict, the summary HR actually reads, the
 * per-dimension reasoning behind it, and the transcript those citations point into.
 *
 * The ordering on this page mirrors the ordering the judge was made to follow - evidence
 * first, label second - so a reader can check the reasoning rather than take the tier on
 * trust. Every dimension's evidence links to the exact transcript line below it.
 */
export default function CandidateDetailPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<CandidateDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scoring, setScoring] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const load = useCallback(async () => {
    if (!sessionId) return;
    setDetail(await api.get<CandidateDetail>(`/candidates/${sessionId}`));
  }, [sessionId]);

  useEffect(() => {
    load().catch((err) =>
      setError(err instanceof Error ? err.message : "Could not load this candidate"),
    );
  }, [load]);

  async function score() {
    setScoring(true);
    setError(null);
    try {
      setDetail(await api.post<CandidateDetail>(`/candidates/${sessionId}/score`));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Scoring failed");
    } finally {
      setScoring(false);
    }
  }

  if (!detail) {
    return (
      <div className="stack">
        <Button
          variant="minimal"
          icon="arrow-left"
          text="All candidates"
          onClick={() => navigate("/candidates")}
        />
        <Section>
          <SectionCard>
            {error ? (
              <Callout intent={Intent.DANGER} icon="error" title="Could not load">
                {error}
              </Callout>
            ) : (
              <NonIdealState icon={<Spinner />} title="Loading candidate" />
            )}
          </SectionCard>
        </Section>
      </div>
    );
  }

  const { evaluation } = detail;

  return (
    <div className="stack">
      <Button
        variant="minimal"
        icon="arrow-left"
        text="All candidates"
        onClick={() => navigate("/candidates")}
      />

      {error && (
        <Callout intent={Intent.DANGER} icon="error" title="Something went wrong">
          {error}
        </Callout>
      )}

      <Section
        title={detail.name}
        icon="person"
        subtitle={
          [
            detail.position_title,
            detail.email,
            detail.interviewed_at
              ? `interviewed ${new Date(detail.interviewed_at).toLocaleString()}`
              : "not yet interviewed",
          ]
            .filter(Boolean)
            .join(" · ")
        }
        rightElement={
          <div className="row" style={{ gap: 8 }}>
            <Tag
              minimal
              intent={statusIntent(detail.session_status)}
              icon={statusIcon(detail.session_status)}
            >
              {statusLabel(detail.session_status)}
            </Tag>
            <Button
              variant="minimal"
              size="small"
              intent={Intent.DANGER}
              icon="trash"
              text="Delete"
              onClick={() => setConfirmDelete(true)}
            />
          </div>
        }
      >
        {detail.failure_reason && (
          <SectionCard>
            <Callout intent={Intent.DANGER} icon="error" title="The interview failed" compact>
              {detail.failure_reason}
            </Callout>
          </SectionCard>
        )}
      </Section>

      <Alert
        isOpen={confirmDelete}
        intent={Intent.DANGER}
        icon="trash"
        confirmButtonText="Delete candidate"
        cancelButtonText="Cancel"
        loading={deleting}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={async () => {
          setDeleting(true);
          try {
            await api.del(`/candidates/${sessionId}`);
            navigate("/candidates");
          } catch (err) {
            setError(err instanceof Error ? err.message : "Could not delete this candidate");
            setDeleting(false);
            setConfirmDelete(false);
          }
        }}
      >
        <p>
          Delete <strong>{detail.name}</strong> and their transcript and score? This cannot
          be undone.
        </p>
      </Alert>

      <AssessmentSection
        detail={detail}
        scoring={scoring}
        onScore={score}
      />

      {evaluation && evaluation.status === "complete" && (
        <BreakdownSection evaluation={evaluation} />
      )}

      <TranscriptSection
        turns={detail.turns}
        evaluation={evaluation}
        sessionId={detail.id}
        sourceLanguage={detail.language}
      />
    </div>
  );
}

// ------------------------------------------------------------------ assessment

function AssessmentSection({
  detail,
  scoring,
  onScore,
}: {
  detail: CandidateDetail;
  scoring: boolean;
  onScore: () => void;
}) {
  const { evaluation, scoring: state } = detail;

  return (
    <Section
      title={
        <Explain text="An LLM judge's read of the transcript. HR makes the decision.">
          Assessment
        </Explain>
      }
      icon="predictive-analysis"
      rightElement={
        <Button
          icon="refresh"
          size="small"
          text={evaluation ? "Re-run scoring" : "Run scoring"}
          loading={scoring}
          disabled={!state.can_score}
          onClick={onScore}
        />
      }
    >
      {!state.can_score && (
        <SectionCard>
          <Callout intent={Intent.WARNING} icon="warning-sign" title="Not scoreable yet" compact>
            <ul className="bp6-list" style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              {state.blockers.map((blocker) => (
                <li key={blocker}>{blocker}</li>
              ))}
            </ul>
            {!state.rubric_approved && (
              <p style={{ marginBottom: 0, marginTop: 8 }}>
                <Link to="/setup">Open interview setup</Link> to author and approve a
                rubric, then score this candidate.
              </p>
            )}
          </Callout>
        </SectionCard>
      )}

      {!evaluation && (
        <SectionCard>
          <NonIdealState
            icon="predictive-analysis"
            title="Not scored yet"
            description={
              state.can_score
                ? "Run scoring to evaluate this transcript against the approved rubric."
                : "This candidate will be scored automatically once the interview finishes and an approved rubric exists."
            }
          />
        </SectionCard>
      )}

      {evaluation?.status === "failed" && (
        <SectionCard>
          <Callout intent={Intent.DANGER} icon="error" title="Scoring did not complete">
            <p>
              {evaluation.error ??
                "The judge did not return a usable evaluation. No verdict was recorded rather than guessing one."}
            </p>
            <p style={{ marginBottom: 0 }}>
              The transcript below is unaffected. Re-run scoring to try again.
            </p>
          </Callout>
        </SectionCard>
      )}

      {evaluation?.status === "complete" && (
        <>
          <SectionCard>
            <VerdictBanner evaluation={evaluation} />
          </SectionCard>

          <SectionCard>
            <div className="assessment-split">
              <div>
                <h4 className="bp6-heading" style={{ marginTop: 0 }}>
                  Summary
                </h4>
                {evaluation.summary.trim() ? (
                  <p style={{ marginBottom: 0, whiteSpace: "pre-wrap" }}>
                    {evaluation.summary}
                  </p>
                ) : (
                  // The judge produced dimensions but no prose. Saying so beats an empty
                  // card that reads as "nothing to report".
                  <p className="bp6-text-muted" style={{ marginBottom: 0 }}>
                    The judge did not write a summary for this candidate. The dimension
                    notes below are still the reasoning it recorded; re-run scoring for a
                    fresh pass.
                  </p>
                )}
              </div>
              <DimensionRadar dimensions={evaluation.dimensions} />
            </div>
          </SectionCard>

          {evaluation.adjustment_reason && (
            <SectionCard>
              {/* The judge's own reading and the recorded verdict differ. Saying so is the
                  point: a silent override would hide the very thing HR should weigh. */}
              <Callout
                intent={Intent.WARNING}
                icon="flow-review"
                title="This verdict was adjusted after the judge ran"
                compact
              >
                <p style={{ marginBottom: evaluation.criteria_verdict ? 6 : 0 }}>
                  {evaluation.adjustment_reason}
                </p>
                {evaluation.criteria_verdict && (
                  <span className="bp6-text-muted bp6-text-small">
                    The rubric criteria alone read as{" "}
                    <strong>{verdictLabel(evaluation.criteria_verdict)}</strong>.
                  </span>
                )}
              </Callout>
            </SectionCard>
          )}

          <SectionCard>
            <div className="row" style={{ gap: 8 }}>
              <Tag
                minimal
                icon={qualityIcon(evaluation.transcript_quality)}
                intent={qualityIntent(evaluation.transcript_quality)}
              >
                {qualityLabel(evaluation.transcript_quality)}
              </Tag>
              {/* Its own tag rather than a shade of transcript quality: a clean recording
                  of half an interview is a good transcript of a short conversation, and
                  saying "degraded" there would blame the wrong thing. */}
              {evaluation.interview_completeness && (
                <Tag
                  minimal
                  icon={completenessIcon(evaluation.interview_completeness)}
                  intent={completenessIntent(evaluation.interview_completeness)}
                >
                  {completenessLabel(evaluation.interview_completeness)}
                </Tag>
              )}
              <Tag minimal icon="chat">
                {evaluation.candidate_turn_count} candidate turn
                {evaluation.candidate_turn_count === 1 ? "" : "s"}
              </Tag>
              {evaluation.rubric_version != null && (
                <Tag minimal icon="comparison" intent={state.rubric_stale ? Intent.WARNING : Intent.NONE}>
                  rubric v{evaluation.rubric_version}
                </Tag>
              )}
              {evaluation.completed_at && (
                <Tag minimal icon="time">
                  {new Date(evaluation.completed_at).toLocaleString()}
                </Tag>
              )}
              {evaluation.model === "mock" && (
                <Tag minimal intent={Intent.WARNING} icon="lab-test">
                  mock — not a real evaluation
                </Tag>
              )}
            </div>
            {evaluation.transcript_quality_note && (
              <p className="bp6-text-muted bp6-text-small" style={{ margin: "8px 0 0" }}>
                {evaluation.transcript_quality_note}
              </p>
            )}
            {/* Shown here too, for a completeness value the banner does not raise -
                "complete" is worth stating plainly when HR is checking a borderline call. */}
            {evaluation.interview_completeness_note &&
              !isPartial(evaluation.interview_completeness) && (
                <p className="bp6-text-muted bp6-text-small" style={{ margin: "4px 0 0" }}>
                  {evaluation.interview_completeness_note}
                </p>
              )}
            {state.rubric_stale && (
              <Callout
                intent={Intent.WARNING}
                icon="history"
                compact
                style={{ marginTop: 10 }}
              >
                This verdict was produced against rubric v{state.scored_against_version};
                the interview now uses v{state.rubric_current_version}. Re-run scoring to
                judge against the current wording.
              </Callout>
            )}
          </SectionCard>
        </>
      )}
    </Section>
  );
}

function VerdictBanner({ evaluation }: { evaluation: Evaluation }) {
  // The qualifier belongs in the banner, not further down the page. A reader who takes
  // only the headline away must take the caveat with it, or the headline is misleading.
  const partial = isPartial(evaluation.interview_completeness);
  return (
    <Callout
      intent={verdictIntent(evaluation.verdict)}
      icon={verdictIcon(evaluation.verdict)}
      className="verdict-banner"
    >
      <div className="verdict-title">
        {verdictLabel(evaluation.verdict)}
        {partial && (
          <Tag
            minimal
            round
            intent={Intent.WARNING}
            icon="stopwatch"
            className="verdict-qualifier"
          >
            partial interview
          </Tag>
        )}
      </div>
      <div className="bp6-text-muted">{verdictBlurb(evaluation.verdict)}</div>
      {partial && (
        <div className="verdict-qualifier-note">
          <strong>{completenessBlurb(evaluation.verdict)}</strong>
          {evaluation.interview_completeness_note && (
            <> {evaluation.interview_completeness_note}</>
          )}{" "}
          Judged on {evaluation.candidate_turn_count} candidate answer
          {evaluation.candidate_turn_count === 1 ? "" : "s"}.
        </div>
      )}
    </Callout>
  );
}

// ------------------------------------------------------------------- breakdown

function BreakdownSection({ evaluation }: { evaluation: Evaluation }) {
  return (
    <Section
      title={
        <Explain text="Each dimension judged on its own, with the transcript lines it rests on.">
          Rubric breakdown
        </Explain>
      }
      icon="th-list"
    >
      {evaluation.dimensions.map((dimension, index) => (
        <SectionCard key={dimension.key}>
          <div className="row row-between" style={{ alignItems: "start", gap: 12 }}>
            <div style={{ minWidth: 0 }}>
              <Explain text={dimension.intent}>
                <strong>
                  {index + 1}. {dimension.label}
                </strong>
              </Explain>
            </div>
            <Tag
              minimal
              size="large"
              icon={dimensionIcon(dimension.verdict)}
              intent={dimensionIntent(dimension.verdict)}
            >
              {dimensionLabel(dimension.verdict)}
            </Tag>
          </div>

          {dimension.note && (
            <p style={{ margin: "10px 0 0", whiteSpace: "pre-wrap" }}>{dimension.note}</p>
          )}

          {dimension.evidence.length > 0 && (
            <>
              <Divider style={{ margin: "12px 0 10px" }} />
              <div className="bp6-text-muted bp6-text-small" style={{ marginBottom: 6 }}>
                From the transcript
              </div>
              {dimension.evidence.map((item) => (
                <Card
                  key={`${dimension.key}-${item.turn}-${item.quote.slice(0, 12)}`}
                  compact
                  className="evidence-card"
                >
                  <a href={`#turn-${item.turn}`} className="evidence-link">
                    turn {item.turn}
                  </a>
                  <span>{item.quote}</span>
                </Card>
              ))}
            </>
          )}

          {dimension.evidence.length === 0 && dimension.verdict === "not_evidenced" && (
            <p className="bp6-text-muted bp6-text-small" style={{ margin: "10px 0 0" }}>
              Nothing in this transcript spoke to this dimension. That is an absence of
              evidence, not evidence against the candidate.
            </p>
          )}

          {dimension.evidence_warning && (
            <Callout
              intent={Intent.WARNING}
              icon="shield"
              compact
              style={{ marginTop: 10 }}
            >
              {dimension.evidence_warning}
            </Callout>
          )}
        </SectionCard>
      ))}
    </Section>
  );
}

// ------------------------------------------------------------------ transcript

function TranscriptSection({
  turns,
  evaluation,
  sessionId,
  sourceLanguage,
}: {
  turns: Turn[];
  evaluation: Evaluation | null;
  sessionId: string;
  sourceLanguage: string;
}) {
  // Every turn the judge leaned on, so a reader following a citation lands somewhere
  // visibly marked rather than counting rows.
  const cited = new Set(
    (evaluation?.dimensions ?? []).flatMap((d) => d.evidence.map((e) => e.turn)),
  );
  const translation = useTranscriptTranslation(sessionId);

  return (
    <Section
      title="Transcript"
      icon="chat"
      rightElement={
        <div className="row" style={{ gap: 6 }}>
          <TranslateControl
            state={translation}
            sourceLanguage={sourceLanguage}
            disabled={turns.length === 0}
          />
          <Tag minimal round>
            {turns.length}
          </Tag>
        </div>
      }
    >
      <SectionCard>
        <TranslateNotice state={translation} />
        {turns.length === 0 ? (
          <NonIdealState
            icon="chat"
            title="No transcript"
            description="This candidate has not spoken to the interviewer yet."
          />
        ) : (
          turns.map((turn) => (
            <div
              key={turn.id}
              id={`turn-${turn.turn_index}`}
              className={`turn ${turn.speaker}${cited.has(turn.turn_index) ? " cited" : ""}`}
            >
              <div className="row" style={{ gap: 6 }}>
                <span
                  className={`who ${
                    turn.speaker === "ai" ? "bp6-text-intent-primary" : "bp6-text-muted"
                  }`}
                >
                  {turn.speaker === "ai" ? "Interviewer" : "Candidate"} · {turn.turn_index}
                </span>
                {cited.has(turn.turn_index) && (
                  <Tag minimal icon="link">
                    cited
                  </Tag>
                )}
              </div>
              {/* The translation is an overlay, never a replacement: a turn it has no
                  entry for (empty, or recorded after the last fetch) shows its own text
                  rather than disappearing. */}
              <div>{translation.translations[turn.id] ?? turn.text}</div>
              {translation.translations[turn.id] && (
                <div className="turn-original">{turn.text}</div>
              )}
              {turn.guardrail_action && (
                <Tag minimal intent={Intent.WARNING} icon="shield" style={{ marginTop: 4 }}>
                  guardrail: {turn.guardrail_action}
                </Tag>
              )}
            </div>
          ))
        )}
      </SectionCard>
    </Section>
  );
}
