import {
  Alert,
  Button,
  Callout,
  Collapse,
  FormGroup,
  Intent,
  NonIdealState,
  Section,
  SectionCard,
  Spinner,
  Tag,
  TextArea,
} from "@blueprintjs/core";
import { useCallback, useEffect, useState } from "react";

import Explain from "./Explain";
import { api, type Rubric, type RubricDimension } from "../lib/api";

/**
 * Authoring the scoring rubric for one interview.
 *
 * The three dimensions are fixed and not editable here - only the band definitions are,
 * because what makes a rubric role-specific is what "strong" evidence looks like for
 * *this* job, not which axes exist. Fixing the axes is what lets HR compare a candidate
 * for one position against a candidate for another.
 *
 * The approve step is the reason this component is not just three textareas. An LLM
 * draft is a starting point; scoring real people against un-reviewed model output would
 * launder a guess into a hiring signal. So: edits always land the rubric in `draft`, and
 * only an explicit approval makes it usable.
 */

const BANDS = [
  {
    field: "strong" as const,
    label: "Strong",
    helper: "What clear, credible evidence looks like in a short get-to-know conversation.",
    intent: Intent.SUCCESS,
  },
  {
    field: "decent" as const,
    label: "Decent",
    helper: "Partial or mixed evidence — some of it there, some of it thin.",
    intent: Intent.PRIMARY,
  },
  {
    field: "not_fit" as const,
    label: "Not a fit",
    helper: "What falls clearly short of what this role needs.",
    intent: Intent.DANGER,
  },
];

type Bands = Pick<RubricDimension, "key" | "strong" | "decent" | "not_fit">;

export default function RubricEditor({
  interviewId,
  hasRequirements,
  onApprovalChange,
}: {
  interviewId: string;
  hasRequirements: boolean;
  /** Lets the setup page refresh its readiness panel when approval state moves. */
  onApprovalChange: () => void;
}) {
  const [rubric, setRubric] = useState<Rubric | null | undefined>(undefined);
  const [edits, setEdits] = useState<Bands[]>([]);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<null | "draft" | "save" | "approve" | "blank">(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmRedraft, setConfirmRedraft] = useState(false);

  const adopt = useCallback((next: Rubric | null) => {
    setRubric(next);
    setEdits(
      (next?.dimensions ?? []).map((d) => ({
        key: d.key,
        strong: d.strong,
        decent: d.decent,
        not_fit: d.not_fit,
      })),
    );
    // Open the first dimension so the editor is obviously editable, and leave the rest
    // folded so nine textareas do not bury the rest of the setup page.
    setOpen(new Set(next?.dimensions.slice(0, 1).map((d) => d.key) ?? []));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setRubric(undefined);
    api
      .get<Rubric | null>(`/interviews/${interviewId}/rubric`)
      .then((r) => !cancelled && adopt(r))
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Could not load the rubric");
        adopt(null);
      });
    return () => {
      cancelled = true;
    };
  }, [interviewId, adopt]);

  async function run(
    kind: "draft" | "save" | "approve" | "blank",
    call: () => Promise<Rubric>,
  ) {
    setBusy(kind);
    setError(null);
    try {
      adopt(await call());
      onApprovalChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(null);
    }
  }

  const dirty =
    rubric != null &&
    edits.some((edit) => {
      const saved = rubric.dimensions.find((d) => d.key === edit.key);
      return (
        !saved ||
        saved.strong !== edit.strong ||
        saved.decent !== edit.decent ||
        saved.not_fit !== edit.not_fit
      );
    });

  const allFilled = edits.every(
    (e) => e.strong.trim() && e.decent.trim() && e.not_fit.trim(),
  );

  return (
    <Section
      title={
        <Explain text="How finished interviews are judged. Three fixed dimensions, one set of bands per job.">
          Scoring rubric
        </Explain>
      }
      icon="comparison"
      rightElement={
        rubric ? (
          <div className="row" style={{ gap: 6 }}>
            <Tag
              minimal
              icon={rubric.status === "approved" ? "endorsed" : "edit"}
              intent={rubric.status === "approved" ? Intent.SUCCESS : Intent.WARNING}
            >
              {rubric.status === "approved" ? "approved" : "draft"}
            </Tag>
            <Button
              variant="minimal"
              size="small"
              icon="generate"
              text="Re-draft with AI"
              disabled={!hasRequirements || busy !== null}
              onClick={() => setConfirmRedraft(true)}
            />
          </div>
        ) : undefined
      }
    >
      {error && (
        <SectionCard>
          <Callout intent={Intent.DANGER} icon="error" compact>
            {error}
          </Callout>
        </SectionCard>
      )}

      {rubric === undefined && (
        <SectionCard>
          <NonIdealState icon={<Spinner />} title="Loading rubric" />
        </SectionCard>
      )}

      {rubric === null && (
        <SectionCard>
          <NonIdealState
            icon="comparison"
            title="No rubric yet"
            description={
              hasRequirements
                ? "Draft one from the job requirements, or write the bands yourself. Either way you review and approve before anything is scored."
                : "Add and index a job-requirement document to draft one automatically, or start from a blank rubric and write the bands yourself."
            }
            action={
              <div className="row" style={{ gap: 8, justifyContent: "center" }}>
                <Button
                  intent={Intent.PRIMARY}
                  icon="generate"
                  text="Draft with AI"
                  loading={busy === "draft"}
                  disabled={!hasRequirements || busy !== null}
                  onClick={() =>
                    run("draft", () =>
                      api.post<Rubric>(`/interviews/${interviewId}/rubric/draft`),
                    )
                  }
                />
                <Button
                  icon="manually-entered-data"
                  text="Write it myself"
                  loading={busy === "blank"}
                  disabled={busy !== null}
                  onClick={() =>
                    run("blank", () =>
                      api.post<Rubric>(`/interviews/${interviewId}/rubric/blank`),
                    )
                  }
                />
              </div>
            }
          />
        </SectionCard>
      )}

      {rubric && (
        <>
          <SectionCard>
            <StatusCallout rubric={rubric} dirty={dirty} allFilled={allFilled} />
          </SectionCard>

          {rubric.dimensions.map((dimension, index) => {
            const edit = edits.find((e) => e.key === dimension.key);
            if (!edit) return null;
            const expanded = open.has(dimension.key);
            const filled = Boolean(
              edit.strong.trim() && edit.decent.trim() && edit.not_fit.trim(),
            );
            return (
              <SectionCard key={dimension.key} padded={false}>
                <Explain text={dimension.intent} bare>
                  <Button
                    variant="minimal"
                    fill
                    alignText="start"
                    icon={expanded ? "chevron-down" : "chevron-right"}
                    onClick={() =>
                      setOpen((current) => {
                        const next = new Set(current);
                        if (!next.delete(dimension.key)) next.add(dimension.key);
                        return next;
                      })
                    }
                    style={{ padding: "10px 14px" }}
                    endIcon={
                      filled ? undefined : (
                        <Tag minimal intent={Intent.WARNING}>
                          incomplete
                        </Tag>
                      )
                    }
                    text={
                      <span className="explain-hint">
                        <strong>
                          {index + 1}. {dimension.label}
                        </strong>
                      </span>
                    }
                  />
                </Explain>
                <Collapse isOpen={expanded}>
                  <div style={{ padding: "0 16px 12px" }}>
                    {BANDS.map((band) => (
                      <FormGroup
                        key={band.field}
                        label={
                          <Explain text={band.helper}>
                            <Tag minimal intent={band.intent}>
                              {band.label}
                            </Tag>
                          </Explain>
                        }
                      >
                        <TextArea
                          fill
                          autoResize
                          value={edit[band.field]}
                          placeholder={`What ${band.label.toLowerCase()} evidence looks like for this role...`}
                          onChange={(e) => {
                            const value = e.target.value;
                            setEdits((current) =>
                              current.map((c) =>
                                c.key === dimension.key
                                  ? { ...c, [band.field]: value }
                                  : c,
                              ),
                            );
                          }}
                        />
                      </FormGroup>
                    ))}
                  </div>
                </Collapse>
              </SectionCard>
            );
          })}

          <SectionCard>
            <div className="row" style={{ gap: 8 }}>
              <Button
                icon="floppy-disk"
                text="Save rubric"
                loading={busy === "save"}
                disabled={!dirty || busy !== null}
                onClick={() =>
                  run("save", () =>
                    api.put<Rubric>(`/interviews/${interviewId}/rubric`, {
                      dimensions: edits,
                    }),
                  )
                }
              />
              <Button
                intent={Intent.SUCCESS}
                icon="endorsed"
                text="Approve for scoring"
                loading={busy === "approve"}
                disabled={
                  busy !== null ||
                  dirty ||
                  !allFilled ||
                  rubric.status === "approved"
                }
                onClick={() =>
                  run("approve", () =>
                    api.post<Rubric>(`/interviews/${interviewId}/rubric/approve`),
                  )
                }
              />
              <Button
                variant="minimal"
                size="small"
                text={open.size === rubric.dimensions.length ? "Collapse all" : "Expand all"}
                onClick={() =>
                  setOpen((current) =>
                    current.size === rubric.dimensions.length
                      ? new Set()
                      : new Set(rubric.dimensions.map((d) => d.key)),
                  )
                }
              />
            </div>
          </SectionCard>
        </>
      )}

      <Alert
        isOpen={confirmRedraft}
        intent={Intent.WARNING}
        icon="generate"
        confirmButtonText="Replace with a new draft"
        cancelButtonText="Keep what I have"
        onCancel={() => setConfirmRedraft(false)}
        onConfirm={() => {
          setConfirmRedraft(false);
          void run("draft", () =>
            api.post<Rubric>(`/interviews/${interviewId}/rubric/draft`),
          );
        }}
      >
        <p>
          This overwrites all nine band definitions with a fresh draft from the job
          requirements{rubric?.status === "approved" ? ", and un-approves the rubric" : ""}.
          Candidates already scored keep the verdict they were given.
        </p>
      </Alert>
    </Section>
  );
}

function StatusCallout({
  rubric,
  dirty,
  allFilled,
}: {
  rubric: Rubric;
  dirty: boolean;
  allFilled: boolean;
}) {
  if (dirty) {
    return (
      <Callout intent={Intent.WARNING} icon="edit" title="Unsaved changes" compact>
        Save the rubric before approving it.
      </Callout>
    );
  }
  if (!allFilled) {
    return (
      <Callout intent={Intent.WARNING} icon="warning-sign" title="Not finished" compact>
        Every dimension needs all three bands before the rubric can be approved. Until
        then, finished interviews are recorded but not scored.
      </Callout>
    );
  }
  if (rubric.status === "approved") {
    return (
      <Callout intent={Intent.SUCCESS} icon="endorsed" title="Approved for scoring" compact>
        Version {rubric.version}, approved{" "}
        {rubric.approved_at ? new Date(rubric.approved_at).toLocaleString() : ""}. Every
        interview that finishes from now on is judged against this wording. Editing it
        sends it back to draft.
      </Callout>
    );
  }
  return (
    <Callout intent={Intent.WARNING} icon="issue" title="Draft — not yet in use" compact>
      {rubric.source === "ai_draft"
        ? "This was drafted from the job requirements. Read it through, edit anything that does not match how you would actually judge this role, then approve it."
        : "Approve the rubric to start scoring finished interviews against it."}
    </Callout>
  );
}
