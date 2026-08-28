import { useCallback, useEffect, useState } from "react";

import DocumentUploader from "../components/DocumentUploader";
import {
  api,
  type Interview,
  type InterviewDetail,
  type Language,
  type Session,
  type SessionCreated,
  type Turn,
} from "../lib/api";

const LANGUAGES: { value: Language; label: string }[] = [
  { value: "en", label: "English" },
  { value: "id", label: "Bahasa Indonesia" },
  { value: "zh", label: "Mandarin Chinese" },
];

export default function SetupPage() {
  const [interviews, setInterviews] = useState<Interview[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<InterviewDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadList = useCallback(async () => {
    const rows = await api.get<Interview[]>("/interviews");
    setInterviews(rows);
    setSelectedId((current) => current ?? rows[0]?.id ?? null);
  }, []);

  const loadDetail = useCallback(async () => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    setDetail(await api.get<InterviewDetail>(`/interviews/${selectedId}`));
  }, [selectedId]);

  useEffect(() => {
    loadList().catch((err) => setError(err.message));
  }, [loadList]);

  useEffect(() => {
    loadDetail().catch((err) => setError(err.message));
  }, [loadDetail]);

  return (
    <div className="split">
      <div>
        <CreateInterview
          onCreated={async (id) => {
            await loadList();
            setSelectedId(id);
          }}
        />
        <div className="card">
          <h2>Interviews</h2>
          <ul className="list">
            {interviews.length === 0 && <p className="muted">None yet.</p>}
            {interviews.map((interview) => (
              <li
                key={interview.id}
                className={interview.id === selectedId ? "active" : ""}
                onClick={() => setSelectedId(interview.id)}
              >
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <strong>{interview.title}</strong>
                  <span className={`badge ${interview.status}`}>{interview.status}</span>
                </div>
                <div className="meta">{interview.position_title}</div>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div>
        {error && <div className="notice error">{error}</div>}
        {!detail && <div className="card muted">Select or create an interview.</div>}
        {detail && (
          <InterviewDetailPanel
            detail={detail}
            onRefresh={async () => {
              await loadDetail();
              await loadList();
            }}
            onDeleted={async () => {
              setSelectedId(null);
              await loadList();
            }}
          />
        )}
      </div>
    </div>
  );
}

function CreateInterview({ onCreated }: { onCreated: (id: string) => void }) {
  const [title, setTitle] = useState("");
  const [position, setPosition] = useState("");
  const [language, setLanguage] = useState<Language>("en");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const created = await api.post<Interview>("/interviews", {
        title,
        position_title: position,
        language,
      });
      setTitle("");
      setPosition("");
      onCreated(created.id);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h2>New interview</h2>
      <label htmlFor="title">Internal title</label>
      <input
        id="title"
        value={title}
        placeholder="Supply Chain Analyst - Q3 intake"
        onChange={(e) => setTitle(e.target.value)}
      />
      <label htmlFor="position">Position title</label>
      <input
        id="position"
        value={position}
        placeholder="Regional Supply Chain Analyst"
        onChange={(e) => setPosition(e.target.value)}
      />
      <label htmlFor="language">Interview language</label>
      <select
        id="language"
        value={language}
        onChange={(e) => setLanguage(e.target.value as Language)}
      >
        {LANGUAGES.map((l) => (
          <option key={l.value} value={l.value}>
            {l.label}
          </option>
        ))}
      </select>
      <button type="submit" disabled={busy || !title.trim() || !position.trim()}>
        Create
      </button>
    </form>
  );
}

function InterviewDetailPanel({
  detail,
  onRefresh,
  onDeleted,
}: {
  detail: InterviewDetail;
  onRefresh: () => Promise<void>;
  onDeleted: () => Promise<void>;
}) {
  const [notes, setNotes] = useState(detail.guardrail_notes ?? "");
  const [planBusy, setPlanBusy] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);

  useEffect(() => setNotes(detail.guardrail_notes ?? ""), [detail.id, detail.guardrail_notes]);

  async function generatePlan() {
    setPlanBusy(true);
    setPlanError(null);
    try {
      await api.post(`/interviews/${detail.id}/plan`);
      await onRefresh();
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : "Plan generation failed");
    } finally {
      setPlanBusy(false);
    }
  }

  return (
    <>
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h3 style={{ margin: 0 }}>{detail.title}</h3>
          <button
            className="danger"
            onClick={async () => {
              if (!confirm(`Delete "${detail.title}" and all its sessions?`)) return;
              await api.del(`/interviews/${detail.id}`);
              await onDeleted();
            }}
          >
            Delete
          </button>
        </div>
        <p className="muted" style={{ marginTop: 4 }}>
          {detail.position_title} ·{" "}
          {LANGUAGES.find((l) => l.value === detail.language)?.label ?? detail.language}
        </p>

        <label htmlFor="notes">Extra guardrail instructions (optional)</label>
        <textarea
          id="notes"
          value={notes}
          placeholder="e.g. Do not discuss relocation packages or visa sponsorship."
          onChange={(e) => setNotes(e.target.value)}
          style={{ minHeight: 70 }}
        />
        <button
          className="secondary"
          onClick={async () => {
            await api.patch(`/interviews/${detail.id}`, { guardrail_notes: notes });
            await onRefresh();
          }}
        >
          Save instructions
        </button>
      </div>

      <ReadinessCard detail={detail} />

      <DocumentUploader
        interviewId={detail.id}
        documents={detail.documents}
        onChange={() => void onRefresh()}
      />

      <div className="card">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h2 style={{ margin: 0 }}>Question plan</h2>
          <button onClick={generatePlan} disabled={planBusy || !detail.readiness.has_requirements}>
            {planBusy
              ? "Generating..."
              : detail.plan_items.length
                ? "Regenerate plan"
                : "Generate plan"}
          </button>
        </div>
        {planError && <div className="notice error">{planError}</div>}
        {!detail.readiness.has_requirements && (
          <div className="notice warn">
            Add and index at least one job-requirement document first - the plan is
            generated from it.
          </div>
        )}
        {detail.plan_items.length === 0 ? (
          <p className="muted">No plan yet.</p>
        ) : (
          <ol style={{ paddingLeft: 18 }}>
            {detail.plan_items.map((item) => (
              <li key={item.id} style={{ marginBottom: 8 }}>
                {item.question}
                {item.competency && (
                  <span className="badge" style={{ marginLeft: 8 }}>
                    {item.competency}
                  </span>
                )}
                {item.coverage_hint && (
                  <div className="meta muted">Looking for: {item.coverage_hint}</div>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>

      <SessionsCard interviewId={detail.id} canCreate={detail.readiness.can_create_sessions} />
    </>
  );
}

function ReadinessCard({ detail }: { detail: InterviewDetail }) {
  const { readiness } = detail;
  return (
    <div className="card">
      <h2>Readiness</h2>
      {readiness.mock_ai && (
        <div className="notice warn">
          MOCK_AI is on. Interviews run against canned responses - no RTC room, no avatar,
          no real speech.
        </div>
      )}
      {readiness.can_create_sessions ? (
        <div className="notice">Ready to invite candidates.</div>
      ) : (
        <div className="notice warn">
          Still needed:
          <ul style={{ margin: "6px 0 0 18px" }}>
            {readiness.blockers.map((blocker) => (
              <li key={blocker}>{blocker}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="row">
        <span className="badge">voice: {readiness.voice_id ?? "not set"}</span>
        <span className="badge">avatar: {readiness.avatar_id ?? "not set"}</span>
        <span className="badge">{readiness.plan_item_count} questions</span>
      </div>
    </div>
  );
}

function SessionsCard({
  interviewId,
  canCreate,
}: {
  interviewId: string;
  canCreate: boolean;
}) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [lastLink, setLastLink] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [viewing, setViewing] = useState<{ name: string; turns: Turn[] } | null>(null);

  const load = useCallback(async () => {
    setSessions(await api.get<Session[]>(`/interviews/${interviewId}/sessions`));
  }, [interviewId]);

  useEffect(() => {
    load().catch(() => setSessions([]));
    setLastLink(null);
  }, [load]);

  async function create(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const created = await api.post<SessionCreated>(`/interviews/${interviewId}/sessions`, {
        candidate_name: name,
        candidate_email: email || null,
      });
      setLastLink(created.join_url);
      setName("");
      setEmail("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create session");
    }
  }

  return (
    <div className="card">
      <h2>Candidate sessions</h2>
      {error && <div className="notice error">{error}</div>}
      {!canCreate && (
        <div className="notice warn">
          Finish the readiness checklist above before inviting candidates.
        </div>
      )}
      <form onSubmit={create}>
        <label htmlFor="cand-name">Candidate name</label>
        <input id="cand-name" value={name} onChange={(e) => setName(e.target.value)} />
        <label htmlFor="cand-email">Email (optional)</label>
        <input
          id="cand-email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <button type="submit" disabled={!canCreate || !name.trim()}>
          Create interview link
        </button>
      </form>

      {lastLink && (
        <div className="notice" style={{ marginTop: 12 }}>
          <div>Send this link to the candidate:</div>
          <div className="mono">{lastLink}</div>
          <button
            className="secondary"
            style={{ marginTop: 8 }}
            onClick={() => navigator.clipboard.writeText(lastLink)}
          >
            Copy link
          </button>
        </div>
      )}

      {viewing && (
        <div className="notice" style={{ marginTop: 12 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <strong>Transcript - {viewing.name}</strong>
            <button className="secondary" onClick={() => setViewing(null)}>
              Close
            </button>
          </div>
          {viewing.turns.length === 0 && <p className="muted">No turns recorded yet.</p>}
          {viewing.turns.map((turn) => (
            <div key={turn.id} className={`turn ${turn.speaker}`}>
              <div className="who">
                {turn.speaker === "ai" ? "Interviewer" : "Candidate"}
              </div>
              <div>{turn.text}</div>
              {turn.guardrail_action && (
                <div className="flag">guardrail: {turn.guardrail_action}</div>
              )}
            </div>
          ))}
        </div>
      )}

      {sessions.length > 0 && (
        <table style={{ marginTop: 12 }}>
          <thead>
            <tr>
              <th>Candidate</th>
              <th>Status</th>
              <th>Created</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {sessions.map((session) => (
              <tr key={session.id}>
                <td>{session.candidate_name}</td>
                <td>
                  <span className={`badge ${session.status}`}>{session.status}</span>
                </td>
                <td className="muted">{new Date(session.created_at).toLocaleString()}</td>
                <td>
                  <a href={`/transcript/${session.id}`} onClick={(e) => e.preventDefault()}>
                    <button
                      className="secondary"
                      onClick={async () => {
                        const data = await api.get<{ turns: { speaker: string; text: string }[] }>(
                          `/sessions/${session.id}/transcript`,
                        );
                        const text = data.turns
                          .map((t) => `${t.speaker === "ai" ? "Interviewer" : "Candidate"}: ${t.text}`)
                          .join("\n\n");
                        alert(text || "No transcript yet.");
                      }}
                    >
                      Transcript
                    </button>
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
