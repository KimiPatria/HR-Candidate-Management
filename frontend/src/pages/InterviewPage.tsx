import {
  Alignment,
  Button,
  Callout,
  Card,
  Collapse,
  H4,
  Intent,
  Navbar,
  NavbarGroup,
  NavbarHeading,
  NonIdealState,
  Spinner,
} from "@blueprintjs/core";
import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import AvatarStage from "../components/AvatarStage";
import DeviceCheck, {
  type DeviceCheckHandle,
  type DeviceSelection,
} from "../components/DeviceCheck";
import TranscriptPanel, { type PartialCaption } from "../components/TranscriptPanel";
import { AGENT_STATUS, toAgentStatus, type AgentStatus } from "../lib/agentStatus";
import {
  api,
  openTranscriptSocket,
  type JoinInfo,
  type RTCCredentials,
  type TranscriptEvent,
  type Turn,
} from "../lib/api";
import type { AudioTap } from "../lib/audioTap";
import { createInterviewEngine, type InterviewEngine } from "../lib/engine";
import type { EngineMode } from "../lib/rtc";

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
  const [rtcConnected, setRtcConnected] = useState(false);
  const [hasVideo, setHasVideo] = useState(false);
  const [muted, setMuted] = useState(false);
  const [mode, setMode] = useState<EngineMode | null>(null);
  // Which devices the candidate settled on in the pre-flight check, and the live audio
  // readings the visualiser draws from. The tap only exists once an engine does.
  const [devices, setDevices] = useState<DeviceSelection>({});
  const [tap, setTap] = useState<AudioTap | null>(null);
  // What the interviewer is doing, as reported by the backend pipeline.
  const [agentStatus, setAgentStatus] = useState<AgentStatus | null>(null);
  // Connection-path notes from the RTC engine (which transport won, state changes,
  // timeouts). Collapsed by default - it exists so a candidate on a network where this
  // fails can read back something specific instead of "it didn't work".
  const [diagnostics, setDiagnostics] = useState<string[]>([]);
  const [showDiagnostics, setShowDiagnostics] = useState(false);

  const engineRef = useRef<InterviewEngine | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const joinedRef = useRef(false);
  const deviceCheckRef = useRef<DeviceCheckHandle>(null);

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
      case "agent_status": {
        // Unknown ids are dropped rather than blanking the strip - a backend that grows
        // a new stage should not make the current one disappear on an older frontend.
        const next = toAgentStatus(event.status);
        if (next) setAgentStatus(next);
        break;
      }
      default:
        break;
    }
  }, []);

  // Stable identity: DeviceCheck reports its selection from an effect, so a new function
  // every render would turn that into a render loop.
  const handleDeviceChange = useCallback((selection: DeviceSelection) => {
    setDevices(selection);
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
    joinedRef.current = false;
    setRtcConnected(false);
    setDiagnostics([]);
    setAgentStatus(null);
    // Hand the microphone back before the engine opens its own capture on the same
    // device - see DeviceCheckHandle.release().
    deviceCheckRef.current?.release();
    try {
      const creds = await api.post<RTCCredentials>(`/join/${token}/start`);
      openSocket(info!.session_id);

      const engine = await createInterviewEngine(
        creds,
        token!,
        {
          onRemoteVideo: () => setHasVideo(true),
          onError: (message) => setError(`Connection problem: ${message}`),
          onConnected: () => setRtcConnected(true),
          onDiagnostic: (note) => setDiagnostics((current) => [...current, note]),
        },
        devices,
      );
      engineRef.current = engine;
      setMode(engine.mode);
      setTap(engine.taps);
      // AvatarStage (and the container div engine.join() renders into) only mounts once
      // phase flips to "live", so containerRef.current is always null here - the actual
      // join happens in handleContainerReady below, once the container exists.
      setPhase("live");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start the interview");
      setPhase("error");
    }
  }

  function handleContainerReady(el: HTMLDivElement) {
    containerRef.current = el;
    if (joinedRef.current || !engineRef.current) return;
    joinedRef.current = true;
    engineRef.current.join(el).catch((err) => {
      setError(err instanceof Error ? err.message : "Could not join the interview room");
    });
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

  /*
   * What the status strip actually shows.
   *
   * Mostly the backend's word - it is the only thing that knows the difference between
   * retrieval and generation. Three cases are overridden locally because the browser
   * knows them sooner or knows them alone: connecting and ended are page states with no
   * pipeline behind them, and muting is a decision the candidate just made, so waiting
   * for a server round trip to acknowledge it would feel broken.
   *
   * Muting does not override "speaking": while the interviewer is mid-sentence, that is
   * the more useful fact, and the mute button already shows its own state.
   */
  const displayStatus: AgentStatus =
    phase === "connecting"
      ? "connecting"
      : phase === "ended"
        ? "ended"
        : muted && agentStatus !== "speaking"
          ? "paused"
          : (agentStatus ?? (rtcConnected ? "listening" : "connecting"));

  if (phase === "loading") {
    return (
      <div className="centred">
        <NonIdealState icon={<Spinner />} title="Loading your interview" />
      </div>
    );
  }

  if (phase === "error" && !info) {
    return (
      <div className="centred">
        <Card elevation={2}>
          <NonIdealState
            icon="error"
            title="This link is not valid"
            description={
              <>
                <p>{error}</p>
                <p>Please contact the recruiter who sent it to you.</p>
              </>
            }
          />
        </Card>
      </div>
    );
  }

  if (phase === "consent") {
    return (
      <div className="centred">
        <Card elevation={2}>
          <H4>Before we begin</H4>
          <p className="bp6-text-muted">
            {info?.position_title} · interview for {info?.candidate_name}
          </p>
          <ul className="bp6-list" style={{ paddingLeft: 18 }}>
            {CONSENT_TEXT.map((line) => (
              <li key={line} style={{ marginBottom: 8 }}>
                {line}
              </li>
            ))}
          </ul>
          <Button
            intent={Intent.PRIMARY}
            size="large"
            fill
            icon="tick"
            text="I understand, continue"
            onClick={acceptConsent}
          />
        </Card>
      </div>
    );
  }

  // The green room. Everything that can be checked before the interview is live gets
  // checked here, because once it starts the candidate has one take.
  if (phase === "ready" || phase === "connecting") {
    return (
      <div className="centred">
        <Card elevation={2} className="greenroom">
          <H4>{info?.interview_title}</H4>
          <p className="bp6-text-muted">
            {info?.position_title} · Hello {info?.candidate_name}
          </p>
          <p>
            The interviewer will greet you and ask the first question as soon as you join.
            Find a quiet spot, then check your microphone and speakers below.
          </p>
          <DeviceCheck ref={deviceCheckRef} onChange={handleDeviceChange} />
          {error && (
            <Callout intent={Intent.DANGER} icon="error" style={{ margin: "12px 0" }}>
              {error}
            </Callout>
          )}
          <Button
            intent={Intent.PRIMARY}
            size="large"
            fill
            icon="phone"
            text={phase === "connecting" ? "Connecting..." : "Join interview"}
            loading={phase === "connecting"}
            onClick={start}
            style={{ marginTop: 18 }}
          />
        </Card>
      </div>
    );
  }

  if (phase === "ended") {
    return (
      <div className="centred">
        <Card elevation={2}>
          <NonIdealState
            icon="tick-circle"
            title="Interview complete"
            description={`Thank you, ${info?.candidate_name ?? ""}. The hiring team will be in touch about next steps.`}
          />
          {error && (
            <Callout intent={Intent.DANGER} icon="error">
              {error}
            </Callout>
          )}
        </Card>
      </div>
    );
  }

  return (
    <div className="shell">
      <Navbar>
        <NavbarGroup align={Alignment.START}>
          <NavbarHeading>{info?.position_title}</NavbarHeading>
        </NavbarGroup>
        <NavbarGroup align={Alignment.END}>
          <span className="bp6-text-muted">{info?.candidate_name}</span>
        </NavbarGroup>
      </Navbar>
      <main>
        {error && (
          <Callout intent={Intent.DANGER} icon="error" style={{ marginBottom: 12 }}>
            {error}
          </Callout>
        )}
        <div className="interview-layout">
          <AvatarStage
            onContainerReady={handleContainerReady}
            hasVideo={hasVideo && mode === "live"}
            mode={mode}
            status={AGENT_STATUS[displayStatus].label}
            tap={tap}
          >
            <Card compact elevation={2}>
              <div className="row" style={{ gap: 6 }}>
                <Button
                  icon={muted ? "disable" : "microphone"}
                  intent={muted ? Intent.WARNING : Intent.NONE}
                  text={muted ? "Unmute" : "Mute"}
                  onClick={async () => {
                    const next = !muted;
                    await engineRef.current?.setMicMuted(next);
                    setMuted(next);
                  }}
                />
                <Button
                  icon="phone"
                  intent={Intent.DANGER}
                  text="End interview"
                  onClick={end}
                />
              </div>
            </Card>

            {mode !== "mock" && diagnostics.length > 0 && (
              <Card compact elevation={2} style={{ maxWidth: 320 }}>
                <Button
                  variant="minimal"
                  size="small"
                  fill
                  alignText="start"
                  icon="diagnosis"
                  endIcon={showDiagnostics ? "chevron-up" : "chevron-down"}
                  text="Connection details"
                  onClick={() => setShowDiagnostics((v) => !v)}
                />
                <Collapse isOpen={showDiagnostics}>
                  <ul
                    className="bp6-list bp6-text-muted bp6-text-small"
                    style={{ paddingLeft: 18, margin: "6px 0 0" }}
                  >
                    {diagnostics.map((note, i) => (
                      <li key={`${i}-${note}`}>{note}</li>
                    ))}
                  </ul>
                </Collapse>
              </Card>
            )}
          </AvatarStage>
          <TranscriptPanel
            turns={turns}
            partial={partial}
            connected={connected}
            agentStatus={displayStatus}
          />
        </div>
      </main>
    </div>
  );
}
