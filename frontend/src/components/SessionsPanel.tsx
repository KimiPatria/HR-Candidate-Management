import {
  Button,
  Callout,
  Dialog,
  DialogBody,
  DialogFooter,
  FormGroup,
  HTMLTable,
  InputGroup,
  Intent,
  NonIdealState,
  Tag,
} from "@blueprintjs/core";
import { useCallback, useEffect, useState } from "react";

import {
  TranslateControl,
  TranslateNotice,
  useTranscriptTranslation,
} from "./TranscriptTranslate";
import { api, type Session, type SessionCreated, type Turn } from "../lib/api";
import { statusIcon, statusIntent, statusLabel } from "../lib/status";

/** Inviting candidates, and the links already sent. */
export default function SessionsPanel({
  interviewId,
  language,
  canCreate,
}: {
  interviewId: string;
  /** The interview's own language, so the translate menu can mark which option is the
   *  original rather than offering to translate it into itself. */
  language: string;
  canCreate: boolean;
}) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [lastLink, setLastLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [viewing, setViewing] = useState<{ id: string; name: string; turns: Turn[] } | null>(
    null,
  );
  const [loadingTranscript, setLoadingTranscript] = useState<string | null>(null);

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
    setBusy(true);
    try {
      const created = await api.post<SessionCreated>(`/interviews/${interviewId}/sessions`, {
        candidate_name: name,
        candidate_email: email || null,
      });
      setLastLink(created.join_url);
      setCopied(false);
      setName("");
      setEmail("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create session");
    } finally {
      setBusy(false);
    }
  }

  async function openTranscript(session: Session) {
    setLoadingTranscript(session.id);
    try {
      const data = await api.get<{ turns: Turn[] }>(`/sessions/${session.id}/transcript`);
      setViewing({ id: session.id, name: session.candidate_name, turns: data.turns });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the transcript");
    } finally {
      setLoadingTranscript(null);
    }
  }

  return (
    <div className="stack">
      {error && (
        <Callout intent={Intent.DANGER} icon="error">
          {error}
        </Callout>
      )}

      <form onSubmit={create}>
        <div className="row" style={{ gap: 12, alignItems: "flex-end" }}>
          <FormGroup label="Candidate name" labelFor="cand-name" style={{ flex: 2, margin: 0 }}>
            <InputGroup id="cand-name" value={name} onValueChange={setName} fill />
          </FormGroup>
          <FormGroup
            label="Email"
            labelInfo="(optional)"
            labelFor="cand-email"
            style={{ flex: 2, margin: 0 }}
          >
            <InputGroup id="cand-email" type="email" value={email} onValueChange={setEmail} fill />
          </FormGroup>
          <Button
            type="submit"
            intent={Intent.PRIMARY}
            icon="link"
            text="Create link"
            loading={busy}
            disabled={!canCreate || !name.trim()}
          />
        </div>
      </form>

      {lastLink && (
        <Callout intent={Intent.SUCCESS} icon="link" title="Send this link to the candidate">
          <code className="bp6-code breakable" style={{ display: "block", margin: "6px 0" }}>
            {lastLink}
          </code>
          <Button
            size="small"
            icon={copied ? "tick" : "clipboard"}
            text={copied ? "Copied" : "Copy link"}
            intent={copied ? Intent.SUCCESS : Intent.NONE}
            onClick={async () => {
              await navigator.clipboard.writeText(lastLink);
              setCopied(true);
              setTimeout(() => setCopied(false), 2000);
            }}
          />
        </Callout>
      )}

      {sessions.length === 0 ? (
        <NonIdealState
          icon="people"
          title="No candidates invited yet"
          description="Create a link above and send it to the candidate. They open it in a browser — there is nothing for them to install."
        />
      ) : (
        <HTMLTable interactive striped compact style={{ width: "100%" }}>
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
                <td style={{ fontWeight: 500 }}>{session.candidate_name}</td>
                <td>
                  <Tag
                    minimal
                    intent={statusIntent(session.status)}
                    icon={statusIcon(session.status)}
                  >
                    {statusLabel(session.status)}
                  </Tag>
                </td>
                <td className="bp6-text-muted">
                  {new Date(session.created_at).toLocaleString()}
                </td>
                <td style={{ textAlign: "right" }}>
                  <Button
                    variant="minimal"
                    size="small"
                    icon="chat"
                    text="Transcript"
                    loading={loadingTranscript === session.id}
                    onClick={() => openTranscript(session)}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </HTMLTable>
      )}

      <TranscriptDialog viewing={viewing} language={language} onClose={() => setViewing(null)} />
    </div>
  );
}

function TranscriptDialog({
  viewing,
  language,
  onClose,
}: {
  viewing: { id: string; name: string; turns: Turn[] } | null;
  language: string;
  onClose: () => void;
}) {
  // Same helper the candidate review page uses, so a transcript reads identically
  // whichever surface it is opened from.
  const translation = useTranscriptTranslation(viewing?.id ?? null);

  return (
    <Dialog
      isOpen={viewing !== null}
      onClose={onClose}
      title={viewing ? `Transcript — ${viewing.name}` : "Transcript"}
      icon="chat"
      style={{ width: 640 }}
    >
      <DialogBody>
        <div className="row" style={{ justifyContent: "flex-end", marginBottom: 4 }}>
          <TranslateControl
            state={translation}
            sourceLanguage={language}
            disabled={!viewing || viewing.turns.length === 0}
          />
        </div>
        <TranslateNotice state={translation} />
        {viewing?.turns.length === 0 && (
          <NonIdealState
            icon="chat"
            title="No turns recorded yet"
            description="The transcript fills in once the candidate starts the interview."
          />
        )}
        {viewing?.turns.map((turn) => (
          <div key={turn.id} className={`turn ${turn.speaker}`}>
            <div
              className={`who ${turn.speaker === "ai" ? "bp6-text-intent-primary" : "bp6-text-muted"}`}
            >
              {turn.speaker === "ai" ? "Interviewer" : "Candidate"}
            </div>
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
        ))}
      </DialogBody>
      <DialogFooter actions={<Button intent={Intent.PRIMARY} text="Close" onClick={onClose} />} />
    </Dialog>
  );
}
