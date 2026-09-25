import {
  Button,
  Callout,
  Dialog,
  DialogBody,
  DialogFooter,
  FormGroup,
  HTMLSelect,
  InputGroup,
  Intent,
  NonIdealState,
  Spinner,
  Tag,
} from "@blueprintjs/core";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, type Interview, type Language } from "../lib/api";

/**
 * Every interview, and the way in to a new one.
 *
 * A list page rather than the old left rail. The rail meant every interview shared a
 * screen with the full detail of one of them, so the first thing anybody saw was seven
 * stacked panels for a position they had not chosen yet. Choosing comes first now, and
 * the detail lives behind it.
 */

export const LANGUAGES: { value: Language; label: string }[] = [
  { value: "en", label: "English" },
  { value: "id", label: "Bahasa Indonesia" },
  { value: "zh", label: "Mandarin Chinese" },
];

export default function InterviewsPage() {
  const [interviews, setInterviews] = useState<Interview[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [query, setQuery] = useState("");
  const navigate = useNavigate();

  const load = useCallback(async () => {
    try {
      setInterviews(await api.get<Interview[]>("/interviews"));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load interviews");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /*
   * Filtering happens here rather than server-side: the whole list is already in memory
   * and an HR team runs tens of positions, not thousands, so a round trip per keystroke
   * would buy nothing. The language label is searchable alongside the two titles because
   * "mandarin" is a thing somebody will type, and it is not in either title.
   */
  const visible = useMemo(() => {
    if (!interviews) return [];
    const needle = query.trim().toLowerCase();
    if (!needle) return interviews;
    return interviews.filter((interview) =>
      [interview.title, interview.position_title, languageLabel(interview.language)]
        .filter(Boolean)
        .some((field) => field.toLowerCase().includes(needle)),
    );
  }, [interviews, query]);

  const filtering = query.trim() !== "";

  return (
    <div className="page-full stack">
      <header className="page-header row row-between">
        <div>
          <h1>Interviews</h1>
          <p className="bp6-text-muted">
            One per position you are hiring for. Each carries its own job description,
            question plan and scoring rubric.
          </p>
        </div>
        <Button
          intent={Intent.PRIMARY}
          icon="add"
          text="New interview"
          onClick={() => setCreating(true)}
        />
      </header>

      {interviews && interviews.length > 0 && (
        <div className="row" style={{ gap: 10 }}>
          <InputGroup
            leftIcon="search"
            placeholder="Search by title, position or language..."
            value={query}
            onValueChange={setQuery}
            aria-label="Search interviews"
            size="large"
            style={{ flex: 1, minWidth: 240 }}
            rightElement={
              query ? (
                <Button
                  variant="minimal"
                  icon="cross"
                  onClick={() => setQuery("")}
                  aria-label="Clear search"
                />
              ) : undefined
            }
          />
          <Tag minimal round size="large">
            {filtering
              ? `${visible.length} of ${interviews.length}`
              : `${interviews.length}`}
          </Tag>
        </div>
      )}

      {error && (
        <Callout intent={Intent.DANGER} icon="error" title="Something went wrong">
          {error}
        </Callout>
      )}

      {interviews === null && (
        <div style={{ padding: 48, textAlign: "center" }}>
          <Spinner size={28} />
        </div>
      )}

      {interviews?.length === 0 && (
        <NonIdealState
          icon="folder-open"
          title="No interviews yet"
          description="Create one, add the job description, and the question plan and scoring rubric are drafted for you."
          action={
            <Button
              intent={Intent.PRIMARY}
              icon="add"
              text="New interview"
              onClick={() => setCreating(true)}
            />
          }
        />
      )}

      {interviews && interviews.length > 0 && visible.length === 0 && (
        <NonIdealState
          icon="search"
          title="No interviews match"
          description={`Nothing here is called "${query.trim()}". Try the position title instead.`}
          action={<Button icon="cross" text="Clear search" onClick={() => setQuery("")} />}
        />
      )}

      {visible.length > 0 && (
        <div className="card-grid">
          {visible.map((interview) => (
            <button
              key={interview.id}
              type="button"
              className="interview-card"
              onClick={() => navigate(`/interviews/${interview.id}`)}
            >
              <div className="interview-card-title">{interview.title}</div>
              <div className="bp6-text-muted bp6-text-small">
                {interview.position_title}
              </div>
              <div className="interview-card-meta">
                <ReadyTag interview={interview} />
                <Tag minimal icon="translate">
                  {languageLabel(interview.language)}
                </Tag>
                <span className="bp6-text-muted bp6-text-small">
                  {new Date(interview.created_at).toLocaleDateString()}
                </span>
              </div>
            </button>
          ))}
        </div>
      )}

      <CreateDialog
        isOpen={creating}
        onClose={() => setCreating(false)}
        onCreated={(id) => navigate(`/interviews/${id}`)}
      />
    </div>
  );
}

/** Falls back to the raw code so a language the backend gains before this list does
 *  still renders something a person can read. */
function languageLabel(code: string): string {
  return LANGUAGES.find((l) => l.value === code)?.label ?? code;
}

/**
 * `status` on the row only tracks whether a plan was last generated, so it can still
 * read "ready" after a document was removed. `can_create_sessions` is computed from
 * current blockers - the same check the interview page shows - so the two never
 * disagree.
 */
function ReadyTag({ interview }: { interview: Interview }) {
  if (interview.autopilot_status === "running") {
    return (
      <Tag minimal intent={Intent.PRIMARY} icon="refresh">
        preparing
      </Tag>
    );
  }
  return interview.can_create_sessions ? (
    <Tag minimal intent={Intent.SUCCESS} icon="tick-circle">
      ready for candidates
    </Tag>
  ) : (
    <Tag minimal intent={Intent.WARNING} icon="issue">
      setup unfinished
    </Tag>
  );
}

function CreateDialog({
  isOpen,
  onClose,
  onCreated,
}: {
  isOpen: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const [title, setTitle] = useState("");
  const [position, setPosition] = useState("");
  const [language, setLanguage] = useState<Language>("en");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api.post<Interview>("/interviews", {
        title,
        position_title: position,
        language,
      });
      setTitle("");
      setPosition("");
      onCreated(created.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create the interview");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog isOpen={isOpen} onClose={onClose} title="New interview" icon="add" style={{ width: 480 }}>
      <form onSubmit={submit}>
        <DialogBody>
          {error && (
            <Callout intent={Intent.DANGER} icon="error" compact style={{ marginBottom: 12 }}>
              {error}
            </Callout>
          )}
          <FormGroup label="Internal title" labelFor="new-title" labelInfo="(what you will recognise it by)">
            <InputGroup
              id="new-title"
              value={title}
              autoFocus
              placeholder="Supply Chain Analyst — Q3 intake"
              onValueChange={setTitle}
            />
          </FormGroup>
          <FormGroup label="Position title" labelFor="new-position" labelInfo="(what the candidate sees)">
            <InputGroup
              id="new-position"
              value={position}
              placeholder="Regional Supply Chain Analyst"
              onValueChange={setPosition}
            />
          </FormGroup>
          <FormGroup
            label="Interview language"
            labelFor="new-language"
            helperText="The language the interviewer speaks and listens in."
          >
            <HTMLSelect
              id="new-language"
              fill
              value={language}
              onChange={(e) => setLanguage(e.currentTarget.value as Language)}
              options={LANGUAGES}
            />
          </FormGroup>
        </DialogBody>
        <DialogFooter
          actions={
            <>
              <Button text="Cancel" onClick={onClose} />
              <Button
                type="submit"
                intent={Intent.PRIMARY}
                icon="arrow-right"
                text="Create and continue"
                loading={busy}
                disabled={!title.trim() || !position.trim()}
              />
            </>
          }
        />
      </form>
    </Dialog>
  );
}
