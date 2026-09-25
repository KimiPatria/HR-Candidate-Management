import {
  Alert,
  Button,
  Callout,
  Collapse,
  FormGroup,
  Icon,
  Intent,
  NonIdealState,
  Section,
  SectionCard,
  Spinner,
  Tag,
  TextArea,
} from "@blueprintjs/core";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import DocumentList, { useDocuments } from "../components/DocumentList";
import DocumentSources from "../components/DocumentSources";
import Explain from "../components/Explain";
import RubricEditor from "../components/RubricEditor";
import SessionsPanel from "../components/SessionsPanel";
import { api, interviewScope, type InterviewDetail } from "../lib/api";
import { LANGUAGES } from "./InterviewsPage";

/**
 * One interview, as three steps rather than seven panels.
 *
 * The old page put documents, readiness, plan, rubric and sessions on screen at once, in
 * an order that had nothing to do with the order anybody does them in. What is actually
 * true is that this is a sequence: describe the job, review what was drafted from it,
 * then invite people. Showing one step at a time is not a simplification of that - it is
 * the shape it already had.
 *
 * The rail stays visible so the sequence is legible from the first screen, and any step
 * can be opened out of order: somebody coming back to re-read a transcript should not
 * have to walk past the job description to reach it.
 */

type StepId = "job" | "review" | "invite";
type StepState = "done" | "current" | "attention" | "blocked" | "todo";

interface Step {
  id: StepId;
  title: string;
  blurb: string;
  state: StepState;
}

export default function InterviewWorkspace() {
  const { interviewId = "" } = useParams();
  const navigate = useNavigate();
  const scope = useMemo(() => interviewScope(interviewId), [interviewId]);

  const [detail, setDetail] = useState<InterviewDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [step, setStep] = useState<StepId | null>(null);

  const documents = useDocuments(scope);

  const loadDetail = useCallback(async () => {
    try {
      setDetail(await api.get<InterviewDetail>(`/interviews/${interviewId}`));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load this interview");
    }
  }, [interviewId]);

  useEffect(() => {
    void loadDetail();
  }, [loadDetail]);

  const refresh = useCallback(async () => {
    await Promise.all([loadDetail(), documents.reload()]);
  }, [loadDetail, documents]);

  // The plan and the rubric are generated off the back of indexing, on the server, with
  // nothing to push the answer back here - so poll while either half is still moving.
  const working = detail?.autopilot_status === "running" || documents.settling;
  useEffect(() => {
    if (!working) return;
    const timer = setInterval(() => void loadDetail(), 1500);
    return () => clearInterval(timer);
  }, [working, loadDetail]);

  const steps = useSteps(detail, step);

  // Open the first unfinished step on arrival, and leave it alone afterwards: a poll
  // landing while somebody is reading the rubric must not move them.
  const active = step ?? steps.find((s) => s.state !== "done")?.id ?? "job";

  if (error) {
    return (
      <div className="page-narrow">
        <Callout intent={Intent.DANGER} icon="error" title="Something went wrong">
          {error}
        </Callout>
      </div>
    );
  }
  if (!detail) {
    return (
      <div style={{ padding: 64, textAlign: "center" }}>
        <Spinner size={28} />
      </div>
    );
  }

  return (
    <div className="page-wide stack">
      <WorkspaceHeader detail={detail} onDeleted={() => navigate("/interviews")} onRefresh={refresh} />

      <div className="workspace">
        <nav className="step-rail" aria-label="Interview setup steps">
          {steps.map((s, index) => (
            <button
              key={s.id}
              type="button"
              className={`step ${s.id === active ? "active" : ""} state-${s.state}`}
              onClick={() => setStep(s.id)}
              aria-current={s.id === active}
            >
              <span className="step-marker">
                {s.state === "done" ? <Icon icon="tick" size={12} /> : index + 1}
              </span>
              <span className="step-text">
                <span className="step-title">{s.title}</span>
                <span className="step-blurb">{s.blurb}</span>
              </span>
            </button>
          ))}
        </nav>

        <div className="stack">
          {active === "job" && (
            <JobStep
              detail={detail}
              scope={scope}
              documents={documents}
              onChange={refresh}
              onContinue={() => setStep("review")}
            />
          )}
          {active === "review" && (
            <ReviewStep detail={detail} onRefresh={refresh} onContinue={() => setStep("invite")} />
          )}
          {active === "invite" && <InviteStep detail={detail} />}
        </div>
      </div>
    </div>
  );
}

/** Where each step stands right now. Derived from readiness rather than remembered, so
 *  removing a document takes the interview back a step instead of leaving a tick behind
 *  a thing that is no longer true. */
function useSteps(detail: InterviewDetail | null, chosen: StepId | null): Step[] {
  return useMemo(() => {
    const readiness = detail?.readiness;
    const hasJob = Boolean(readiness?.has_requirements);
    const hasPlan = (readiness?.plan_item_count ?? 0) > 0;
    const approved = Boolean(readiness?.rubric_approved);
    const canInvite = Boolean(readiness?.can_create_sessions);

    const review: StepState = !hasJob
      ? "blocked"
      : hasPlan && approved
        ? "done"
        : hasPlan
          ? "attention"
          : "todo";

    return [
      {
        id: "job",
        title: "Job description",
        blurb: hasJob ? "Added and indexed" : "What this role needs",
        state: hasJob ? "done" : "todo",
      },
      {
        id: "review",
        title: "Questions and scoring",
        blurb:
          review === "attention"
            ? "Rubric needs your sign-off"
            : review === "done"
              ? "Approved"
              : "Drafted from the job description",
        state: review,
      },
      {
        id: "invite",
        title: "Invite candidates",
        blurb: canInvite ? "Ready to send links" : "Finish the steps above first",
        state: canInvite ? (chosen === "invite" ? "current" : "todo") : "blocked",
      },
    ];
  }, [detail, chosen]);
}

function WorkspaceHeader({
  detail,
  onDeleted,
  onRefresh,
}: {
  detail: InterviewDetail;
  onDeleted: () => void;
  onRefresh: () => Promise<void>;
}) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [notes, setNotes] = useState(detail.guardrail_notes ?? "");
  const [saving, setSaving] = useState(false);

  useEffect(() => setNotes(detail.guardrail_notes ?? ""), [detail.id, detail.guardrail_notes]);

  const languageLabel =
    LANGUAGES.find((l) => l.value === detail.language)?.label ?? detail.language;

  return (
    <>
      <header className="page-header">
        <Button
          variant="minimal"
          size="small"
          icon="chevron-left"
          text="All interviews"
          onClick={onDeleted}
        />
        <div className="row row-between" style={{ marginTop: 8 }}>
          <div>
            <h1>{detail.title}</h1>
            <p className="bp6-text-muted">
              {detail.position_title} · {languageLabel}
            </p>
          </div>
          <div className="row" style={{ gap: 6 }}>
            <Button
              variant="minimal"
              icon="cog"
              text="Settings"
              active={settingsOpen}
              onClick={() => setSettingsOpen((open) => !open)}
            />
            <Button
              variant="minimal"
              intent={Intent.DANGER}
              icon="trash"
              aria-label="Delete interview"
              onClick={() => setConfirmDelete(true)}
            />
          </div>
        </div>
      </header>

      <Collapse isOpen={settingsOpen}>
        <Section compact>
          <SectionCard>
            <FormGroup
              label={
                <Explain text="Topics the interviewer must avoid or handle carefully.">
                  Extra guardrail instructions
                </Explain>
              }
              labelFor="notes"
              labelInfo="(optional)"
            >
              <TextArea
                id="notes"
                value={notes}
                fill
                autoResize
                placeholder="e.g. Do not discuss relocation packages or visa sponsorship."
                onChange={(e) => setNotes(e.target.value)}
              />
            </FormGroup>
            <Button
              icon="floppy-disk"
              text="Save instructions"
              loading={saving}
              disabled={notes === (detail.guardrail_notes ?? "")}
              onClick={async () => {
                setSaving(true);
                try {
                  await api.patch(`/interviews/${detail.id}`, { guardrail_notes: notes });
                  await onRefresh();
                } finally {
                  setSaving(false);
                }
              }}
            />
          </SectionCard>
        </Section>
      </Collapse>

      <Alert
        isOpen={confirmDelete}
        intent={Intent.DANGER}
        icon="trash"
        confirmButtonText="Delete interview"
        cancelButtonText="Cancel"
        onCancel={() => setConfirmDelete(false)}
        onConfirm={async () => {
          setConfirmDelete(false);
          await api.del(`/interviews/${detail.id}`);
          onDeleted();
        }}
      >
        <p>
          Delete <strong>{detail.title}</strong> and all of its sessions? This cannot be
          undone.
        </p>
      </Alert>
    </>
  );
}

function JobStep({
  detail,
  scope,
  documents,
  onChange,
  onContinue,
}: {
  detail: InterviewDetail;
  scope: ReturnType<typeof interviewScope>;
  documents: ReturnType<typeof useDocuments>;
  onChange: () => Promise<void>;
  onContinue: () => void;
}) {
  return (
    <>
      <Section title="Job description" icon="briefcase" subtitle="What this role needs">
        <SectionCard>
          <p className="bp6-text-muted" style={{ marginTop: 0 }}>
            Everything else is generated from this: the questions the interviewer asks and
            the rubric candidates are scored against. Add it however it reaches you.
          </p>
          <DocumentSources scope={scope} onAdded={() => void onChange()} />
        </SectionCard>

        <SectionCard padded={false}>
          {documents.documents === null ? (
            <div style={{ padding: 32, textAlign: "center" }}>
              <Spinner size={24} />
            </div>
          ) : (
            <DocumentList
              scope={scope}
              documents={documents.documents}
              language={detail.language}
              onChange={() => void onChange()}
              emptyDescription="Add the job description to unlock the question plan and the scoring rubric."
            />
          )}
        </SectionCard>
      </Section>

      {detail.readiness.has_requirements && (
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <Button
            intent={Intent.PRIMARY}
            icon="arrow-right"
            text="Review questions and scoring"
            onClick={onContinue}
          />
        </div>
      )}
    </>
  );
}

function ReviewStep({
  detail,
  onRefresh,
  onContinue,
}: {
  detail: InterviewDetail;
  onRefresh: () => Promise<void>;
  onContinue: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const running = detail.autopilot_status === "running";

  async function regenerate() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/interviews/${detail.id}/setup`);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not regenerate");
    } finally {
      setBusy(false);
    }
  }

  if (!detail.readiness.has_requirements) {
    return (
      <Section>
        <SectionCard>
          <NonIdealState
            icon="lightbulb"
            title="Nothing to review yet"
            description="Add the job description first — the questions and the rubric are both written from it."
          />
        </SectionCard>
      </Section>
    );
  }

  return (
    <>
      {error && (
        <Callout intent={Intent.DANGER} icon="error">
          {error}
        </Callout>
      )}

      {running && (
        <Callout intent={Intent.PRIMARY} icon="refresh" title="Writing this for you">
          {detail.autopilot_step === "indexing"
            ? "Reading the job description..."
            : detail.autopilot_step === "rubric"
              ? "The questions are ready. Drafting the scoring rubric from the job description..."
              : "Writing the question plan from the job description..."}
        </Callout>
      )}

      {detail.autopilot_status === "failed" && (
        <Callout
          intent={Intent.WARNING}
          icon="warning-sign"
          title="Could not finish on its own"
        >
          <p>{detail.autopilot_error}</p>
          <Button icon="refresh" text="Try again" loading={busy} onClick={regenerate} />
        </Callout>
      )}

      <Section
        title="Question plan"
        icon="numbered-list"
        subtitle="Asked of every candidate for this position, so answers stay comparable"
        rightElement={
          <Button
            variant="minimal"
            size="small"
            icon="generate"
            text="Regenerate both"
            loading={busy || running}
            onClick={regenerate}
          />
        }
      >
        <SectionCard>
          {detail.plan_items.length === 0 ? (
            <NonIdealState
              icon="numbered-list"
              title={running ? "Being written now" : "No questions yet"}
              description={
                running
                  ? "This takes a few seconds."
                  : "Use Regenerate above to write the plan from the job description."
              }
            />
          ) : (
            <ol className="plan-list">
              {detail.plan_items.map((item) => (
                <li key={item.id}>
                  <div className="row row-between" style={{ alignItems: "start" }}>
                    <span>{item.question}</span>
                    <div className="row" style={{ gap: 4 }}>
                      {item.must_ask && (
                        <Tag minimal intent={Intent.PRIMARY}>
                          must ask
                        </Tag>
                      )}
                      {item.competency && <Tag minimal>{item.competency}</Tag>}
                    </div>
                  </div>
                  {item.coverage_hint && (
                    <div className="bp6-text-muted bp6-text-small">
                      Looking for: {item.coverage_hint}
                    </div>
                  )}
                </li>
              ))}
            </ol>
          )}
        </SectionCard>
      </Section>

      <RubricEditor
        interviewId={detail.id}
        hasRequirements={detail.readiness.has_requirements}
        onApprovalChange={() => void onRefresh()}
        // Re-mount when the automatic draft lands, so the editor picks it up instead of
        // sitting on the empty rubric it loaded a moment earlier.
        key={`${detail.id}-${detail.autopilot_status}`}
      />

      <div className="row" style={{ justifyContent: "flex-end" }}>
        <Button
          intent={Intent.PRIMARY}
          icon="arrow-right"
          text="Invite candidates"
          disabled={!detail.readiness.can_create_sessions}
          onClick={onContinue}
        />
      </div>
    </>
  );
}

function InviteStep({ detail }: { detail: InterviewDetail }) {
  const { readiness } = detail;
  return (
    <>
      {!readiness.can_create_sessions && (
        <Callout intent={Intent.WARNING} icon="warning-sign" title="Not ready to invite yet">
          <ul className="bp6-list" style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {readiness.blockers.map((blocker) => (
              <li key={blocker}>{blocker}</li>
            ))}
          </ul>
        </Callout>
      )}

      {/* A warning, never a blocker. The interview runs and records a transcript without
          an approved rubric; it simply produces no verdict until one is approved, and
          candidates can be scored retroactively at any point. */}
      {readiness.can_create_sessions && !readiness.rubric_approved && (
        <Callout intent={Intent.WARNING} icon="comparison" title="No approved rubric">
          Interviews will run and be recorded, but finished candidates will not be scored
          until you approve the rubric on the previous step.
        </Callout>
      )}

      {readiness.mock_ai && (
        <Callout intent={Intent.WARNING} icon="lab-test" title="MOCK_AI is on">
          Interviews run against canned responses — no RTC room, no avatar, no real
          speech.
        </Callout>
      )}

      {readiness.can_create_sessions && !readiness.avatar_enabled && (
        <Callout intent={Intent.PRIMARY} icon="headset" title="Voice-only interview">
          No avatar credential is set, so the interview runs with audio and transcript but
          no avatar video.
        </Callout>
      )}

      <Section title="Candidates" icon="people">
        <SectionCard>
          <SessionsPanel
            interviewId={detail.id}
            language={detail.language}
            canCreate={readiness.can_create_sessions}
          />
        </SectionCard>
      </Section>
    </>
  );
}
