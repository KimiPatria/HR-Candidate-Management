import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import AvatarStage from "../components/AvatarStage";
import TranscriptPanel, { type PartialCaption } from "../components/TranscriptPanel";
import {
  api,
  openTranscriptSocket,
  type JoinInfo,
  type RTCCredentials,
  type TranscriptEvent,
  type Turn,
} from "../lib/api";
import { createEngine, type EngineMode, type InterviewEngine } from "../lib/rtc";

type Phase = "loading" | "consent" | "ready" | "connecting" | "live" | "ended" | "error";

const CONSENT_TEXT = [
  "This interview is conducted by an AI interviewer on behalf of the hiring team.",
  "Your microphone audio is transcribed, and the transcript is stored and shared with the hiring team as part of your application.",
  "No hiring decision is made by the AI. A human reviews every interview.",
  "You can end the interview at any time using the End interview button.",
];

export default function InterviewPage() {
  const { token } = useParams<{ token: string }>();
  const [phase, setPhase] = useState<Phase>("loading");
  const [info, setInfo] = useState<JoinInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [partial, setPartial] = useState<PartialCaption | null>(null);
  const [connected, setConnected] = useState(false);
  const [hasVideo, setHasVideo] = useState(false);
  const [muted, setMuted] = useState(false);
  const [mode, setMode] = useState<EngineMode | null>(null);

  const engineRef = useRef<InterviewEngine | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!token) return;
    api
      .get<JoinInfo>(`/join/${token}`)
      .then((data) => {
        setInfo(data);
        if (data.status === "completed") setPhase("ended");
        else setPhase(data.consent_required ? "consent" : "ready");
      })
      .catch((err) => {
        setError(err.message);
        setPhase("error");
      });
  }, [token]);

  // Tear down the room and the socket if the candidate navigates away.
  useEffect(() => {
    return () => {
      socketRef.current?.close();
      void engineRef.current?.leave();
    };
  }, []);

  const handleEvent = useCallback((event: TranscriptEvent) => {
    switch (event.type) {
      case "turn":
        setPartial(null);
        setTurns((current) =>
          current.some((t) => t.id === event.id) ? current : [...current, event],
        );
        break;
      case "partial":
        setPartial(event.final ? null : { speaker: event.speaker, text: event.text });
        break;
      case "status":
        if (event.status === "completed" || event.status === "failed") setPhase("ended");
        if (event.status === "failed") setError(event.reason ?? "The interview ended unexpectedly");
        break;
      default:
        break;
    }
  }, []);

  function openSocket(sessionId: string) {
    const ws = openTranscriptSocket(sessionId, token ?? null, handleEvent);
    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    socketRef.current = ws;
  }

  async function acceptConsent() {
    await api.post(`/join/${token}/consent`);
    setPhase("ready");
  }

  async function start() {
    setPhase("connecting");
    setError(null);
    try {
      const creds = await api.post<RTCCredentials>(`/join/${token}/start`);
      openSocket(info!.session_id);

      const engine = await createEngine(creds, {
        onRemoteVideo: () => setHasVideo(true),
        onError: (message) => setError(`Connection problem: ${message}`),
      });
      engineRef.current = engine;
      setMode(engine.mode);
      if (containerRef.current) await engine.join(containerRef.current);
      setPhase("live");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start the interview");
      setPhase("error");
    }
  }

  async function end() {
    try {
      await engineRef.current?.leave();
      await api.post(`/join/${token}/end`);
    } finally {
      socketRef.current?.close();
      setPhase("ended");
    }
  }

  if (phase === "loading") return <div className="centred muted">Loading your interview...</div>;

  if (phase === "error" && !info) {
    return (
      <div className="centred">
        <div className="card">
          <h3>This link is not valid</h3>
          <p className="muted">{error}</p>
          <p className="muted">Please contact the recruiter who sent it to you.</p>
        </div>
      </div>
    );
  }

  if (phase === "consent") {
    return (
      <div className="centred">
        <div className="card">
          <h3>Before we begin</h3>
          <p className="muted">
            {info?.position_title} · interview for {info?.candidate_name}
          </p>
          <ul style={{ paddingLeft: 18 }}>
            {CONSENT_TEXT.map((line) => (
              <li key={line} style={{ marginBottom: 6 }}>
                {line}
              </li>
            ))}
          </ul>
          <button onClick={acceptConsent}>I understand, continue</button>
        </div>
      </div>
    );
  }

  if (phase === "ready" || phase === "connecting") {
    return (
      <div className="centred">
        <div className="card">
          <h3>{info?.interview_title}</h3>
          <p className="muted">
            {info?.position_title} · Hello {info?.candidate_name}
          </p>
          <p>
            The interviewer will greet you and ask the first question as soon as you join.
            Find a quiet spot and allow microphone access when your browser asks.
          </p>
          {error && <div className="notice error">{error}</div>}
          <button onClick={start} disabled={phase === "connecting"}>
            {phase === "connecting" ? "Connecting..." : "Start interview"}
          </button>
        </div>
      </div>
    );
  }

  if (phase === "ended") {
    return (
      <div className="centred">
        <div className="card">
          <h3>Interview complete</h3>
          <p className="muted">
            Thank you, {info?.candidate_name}. The hiring team will be in touch about next
            steps.
          </p>
          {error && <div className="notice error">{error}</div>}
        </div>
      </div>
    );
  }

  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">{info?.position_title}</span>
        <nav />
        <span className="muted">{info?.candidate_name}</span>
      </header>
      <main>
        {error && <div className="notice error">{error}</div>}
        <div className="interview-layout">
          <AvatarStage
            onContainerReady={(el) => {
              containerRef.current = el;
            }}
            hasVideo={hasVideo && mode === "live"}
            mode={mode}
            status="Interviewer is listening"
          >
            <button
              className="secondary"
              onClick={async () => {
                const next = !muted;
                await engineRef.current?.setMicMuted(next);
                setMuted(next);
              }}
            >
              {muted ? "Unmute microphone" : "Mute microphone"}
            </button>
            <button className="danger" onClick={end}>
              End interview
            </button>
          </AvatarStage>
          <TranscriptPanel turns={turns} partial={partial} connected={connected} />
        </div>
      </main>
    </div>
  );
}
