// Thin fetch wrapper. Everything goes through the Vite proxy at /api, so the HR session
// cookie is same-origin and needs no CORS handling.

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api${path}`, {
    credentials: "include",
    ...init,
    headers:
      init.body instanceof FormData
        ? init.headers
        : { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // Non-JSON error body; the status text is the best we have.
    }
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PUT", body: JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  upload: <T>(path: string, form: FormData) =>
    request<T>(path, { method: "POST", body: form }),
};

// ---------------------------------------------------------------------- types

export type Language = "en" | "id" | "zh";
export type DocType = "job_requirement" | "knowledge_base";
/** Where a document came from. The two drive values are kept distinct from "upload" so
 *  the document list can show provenance and link back to the file in its drive. */
export type DocSource = "upload" | "pasted" | "google_drive" | "onedrive";
export type DriveProvider = "google_drive" | "onedrive";

export interface Interview {
  id: string;
  title: string;
  position_title: string;
  language: string;
  guardrail_notes: string | null;
  status: string;
  voice_id: string | null;
  avatar_id: string | null;
  // Whether the plan and rubric draft are being generated off the back of a job
  // description upload. The setup page polls while this is "running".
  autopilot_status: "idle" | "running" | "done" | "failed";
  autopilot_step: "indexing" | "plan" | "rubric" | null;
  autopilot_error: string | null;
  created_at: string;
  // Computed server-side from current blockers (documents, plan, TTS voice) - not the
  // same as `status`, which only tracks whether a plan was last generated.
  can_create_sessions: boolean;
}

export interface InterviewDocument {
  id: string;
  doc_type: DocType;
  source: DocSource;
  filename: string | null;
  source_url: string | null;
  index_status: "pending" | "indexing" | "indexed" | "failed";
  index_error: string | null;
  chunk_count: number;
  /** How much text was recovered from the file. The number that tells a scanned PDF
   *  apart from a parsed one before anybody reads a question generated from it. */
  char_count: number;
  has_original_file: boolean;
  created_at: string;
}

/** A document plus the text that was actually chunked and embedded. Fetched only when
 *  someone opens a document to read it - the list polls, and shipping every job
 *  description on every poll would be a lot of bytes nobody asked for. */
export interface DocumentContent extends InterviewDocument {
  content_text: string;
}

/**
 * Both document scopes behind one shape, so the uploader, the list and the source
 * viewer are written once and used by the interview page and the company knowledge page
 * alike. The only thing that differs between the two is the URL prefix.
 */
export interface DocumentScope {
  /** Path prefix every document call hangs off. */
  base: string;
  /** Sent on ingest. Company knowledge derives it server-side, but the paste route
   *  takes it either way, so it is carried here rather than special-cased. */
  docType: DocType;
}

export const interviewScope = (interviewId: string): DocumentScope => ({
  base: `/interviews/${interviewId}/documents`,
  docType: "job_requirement",
});

export const companyScope: DocumentScope = {
  base: "/knowledge/documents",
  docType: "knowledge_base",
};

export const listDocuments = (scope: DocumentScope) =>
  api.get<InterviewDocument[]>(scope.base);

export const readDocument = (scope: DocumentScope, documentId: string) =>
  api.get<DocumentContent>(`${scope.base}/${documentId}/content`);

/** The original file, byte for byte. Only meaningful when `has_original_file` is set -
 *  pasted text was never a file. Returned as a URL rather than fetched, because the
 *  browser's own download handling is what should own the save dialog. */
export const documentDownloadUrl = (scope: DocumentScope, documentId: string) =>
  `/api${scope.base}/${documentId}/download`;

export const deleteDocument = (scope: DocumentScope, documentId: string) =>
  api.del(`${scope.base}/${documentId}`);

export const reindexDocument = (scope: DocumentScope, documentId: string) =>
  api.post<InterviewDocument>(`${scope.base}/${documentId}/reindex`);

export const uploadDocument = (scope: DocumentScope, file: File) => {
  const form = new FormData();
  form.append("doc_type", scope.docType);
  form.append("file", file);
  return api.upload<InterviewDocument>(`${scope.base}/upload`, form);
};

export const pasteDocument = (scope: DocumentScope, text: string, filename?: string) =>
  api.post<InterviewDocument>(`${scope.base}/paste`, {
    doc_type: scope.docType,
    content_text: text,
    filename: filename ?? null,
  });

/** A file the browser pulled out of Google Drive or OneDrive. The bytes go up as an
 *  ordinary upload - see lib/drive.ts for why the download happens here and not on the
 *  server. */
export const importDriveDocument = (
  scope: DocumentScope,
  provider: DriveProvider,
  file: File,
  sourceUrl: string,
) => {
  const form = new FormData();
  form.append("doc_type", scope.docType);
  form.append("provider", provider);
  form.append("source_url", sourceUrl);
  form.append("file", file);
  return api.upload<InterviewDocument>(`${scope.base}/import`, form);
};

// ------------------------------------------------------------------ integrations

export interface DriveIntegration {
  enabled: boolean;
  client_id: string;
  api_key: string;
  app_id: string;
}

export interface Integrations {
  google_drive: DriveIntegration;
  onedrive: DriveIntegration;
}

export const getIntegrations = () => api.get<Integrations>("/integrations");

export interface PlanItem {
  id: string;
  order_index: number;
  question: string;
  competency: string | null;
  must_ask: boolean;
  coverage_hint: string | null;
}

export interface Readiness {
  voice_id: string | null;
  avatar_id: string | null;
  avatar_enabled: boolean;
  ready: boolean;
  missing: string[];
  mock_ai: boolean;
  has_requirements: boolean;
  plan_item_count: number;
  can_create_sessions: boolean;
  blockers: string[];
  // Deliberately not part of `blockers`: an interview without a rubric still runs, it
  // just produces no verdict until one is approved.
  rubric_status: "none" | "draft" | "approved";
  rubric_approved: boolean;
}

export interface InterviewDetail extends Interview {
  documents: InterviewDocument[];
  plan_items: PlanItem[];
  readiness: Readiness;
}

export interface Session {
  id: string;
  interview_id: string;
  candidate_name: string;
  candidate_email: string | null;
  status: string;
  rtc_room_id: string | null;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
}

export interface SessionCreated extends Session {
  join_url: string;
  join_token: string;
  token_expires_at: string | null;
}

export interface JoinInfo {
  session_id: string;
  candidate_name: string;
  position_title: string;
  interview_title: string;
  language: string;
  status: string;
  consent_required: boolean;
}

// What /join/{token}/start returns. `voice_mode` selects the engine (see lib/engine.ts);
// in "local" mode the RTC fields are empty because there is no room to join.
export interface RTCCredentials {
  voice_mode: "rtc" | "local";
  app_id: string;
  room_id: string;
  user_id: string;
  token: string;
  task_id: string | null;
  avatar_id: string | null;
}

export interface Turn {
  id: string;
  turn_index: number;
  speaker: "ai" | "candidate" | "system";
  text: string;
  guardrail_action: string | null;
  timestamp: string;
}

// --------------------------------------------------------------- translation

/** Translations keyed by turn id, so they overlay the turns already on screen rather
 *  than replacing them with a parallel list that could drift out of order. */
export interface TranscriptTranslation {
  session_id: string;
  target_language: Language;
  translations: Record<string, string>;
  /** False when the provider could not be reached. The reviewer is looking at the
   *  original text and the UI has to say so rather than implying a translation. */
  translated: boolean;
  detail: string | null;
}

export interface DocumentTranslation {
  document_id: string;
  target_language: Language;
  source_language: Language | null;
  text: string;
  provider: string;
  used_for_indexing: boolean;
  created_at: string;
}

export const LANGUAGE_LABELS: Record<Language, string> = {
  en: "English",
  id: "Bahasa Indonesia",
  zh: "Mandarin Chinese",
};

/** Translate a whole transcript. Cached server-side per (turn, language), so calling
 *  this again for a language already fetched costs nothing. */
export const translateTranscript = (sessionId: string, target: Language) =>
  api.post<TranscriptTranslation>(
    `/sessions/${sessionId}/transcript/translate?target=${target}`,
  );

/** The cached translation only - never triggers a paid translation. 404 when absent.
 *  Addressed by document id alone: a company knowledge-base document belongs to no
 *  interview, and one route beats two that would have to stay in step. */
export const getDocumentTranslation = (documentId: string, target: Language) =>
  api.get<DocumentTranslation>(`/documents/${documentId}/translation?target=${target}`);

export const translateDocument = (documentId: string, target: Language) =>
  api.post<DocumentTranslation>(`/documents/${documentId}/translate?target=${target}`);

// ------------------------------------------------------------------- scoring

/** Four wide, named tiers. There is deliberately no number anywhere in this feature -
 *  see backend/app/models/rubric.py for why. */
export type Verdict = "strong_fit" | "decent_fit" | "not_a_fit" | "inconclusive";
export type DimensionVerdict = "strong" | "decent" | "not_a_fit" | "not_evidenced";
export type TranscriptQuality = "usable" | "degraded" | "unusable";
// Whether the conversation finished. Null means "not assessed" - an evaluation
// judged before this existed - and must not be read as "complete".
export type InterviewCompleteness = "complete" | "partial";
export type DimensionKey =
  | "background_fit"
  | "on_the_spot_reasoning"
  | "communication_clarity";

export interface RubricDimension {
  key: DimensionKey;
  label: string;
  intent: string;
  order_index: number;
  strong: string;
  decent: string;
  not_fit: string;
  filled: boolean;
}

export interface Rubric {
  id: string;
  interview_id: string;
  status: "draft" | "approved";
  source: "ai_draft" | "manual";
  version: number;
  approved_at: string | null;
  updated_at: string;
  complete: boolean;
  dimensions: RubricDimension[];
}

/** A candidate turn a verdict rests on. `turn` is the real turn_index, so the detail
 *  page can jump straight to the line in the transcript below. */
export interface Evidence {
  turn: number;
  quote: string;
}

export interface EvaluationDimension {
  key: DimensionKey;
  label: string;
  intent: string;
  order_index: number;
  verdict: DimensionVerdict;
  note: string;
  evidence: Evidence[];
  evidence_warning: string | null;
}

export interface Evaluation {
  id: string;
  status: "complete" | "failed";
  error: string | null;
  verdict: Verdict | null;
  criteria_verdict: "strong_fit" | "decent_fit" | "not_a_fit" | null;
  adjustment_reason: string | null;
  transcript_quality: TranscriptQuality | null;
  transcript_quality_note: string | null;
  interview_completeness: InterviewCompleteness | null;
  interview_completeness_note: string | null;
  summary: string;
  model: string | null;
  candidate_turn_count: number;
  rubric_version: number | null;
  completed_at: string | null;
  dimensions: EvaluationDimension[];
}

export interface ScoringState {
  can_score: boolean;
  blockers: string[];
  rubric_approved: boolean;
  rubric_current_version: number | null;
  scored_against_version: number | null;
  rubric_stale: boolean;
}

export interface Candidate {
  id: string;
  name: string;
  email: string | null;
  position_title: string;
  session_status: string;
  interviewed_at: string | null;
  verdict: Verdict | null;
  evaluation_status: "complete" | "failed" | null;
  interview_completeness: InterviewCompleteness | null;
}

export interface CandidateDetail {
  id: string;
  name: string;
  email: string | null;
  interview_id: string;
  interview_title: string;
  position_title: string;
  language: string;
  session_status: string;
  created_at: string;
  interviewed_at: string | null;
  ended_at: string | null;
  failure_reason: string | null;
  turns: Turn[];
  evaluation: Evaluation | null;
  rubric: Rubric | null;
  scoring: ScoringState;
}

// -------------------------------------------------------------- live transcript

export type TranscriptEvent =
  | ({ type: "turn" } & Turn)
  | { type: "partial"; speaker: string; text: string; final: boolean }
  | { type: "status"; status: string; reason?: string }
  // What the interviewer is doing between turns - listening, thinking, retrieving,
  // speaking. Emitted by the local voice pipeline; see lib/agentStatus.ts for the ids.
  | { type: "agent_status"; status: string }
  | { type: "ping" };

export function openTranscriptSocket(
  sessionId: string,
  token: string | null,
  onEvent: (event: TranscriptEvent) => void,
): WebSocket {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  const query = token ? `?token=${encodeURIComponent(token)}` : "";
  const ws = new WebSocket(
    `${scheme}://${window.location.host}/api/sessions/${sessionId}/ws${query}`,
  );
  ws.onmessage = (event) => {
    try {
      onEvent(JSON.parse(event.data) as TranscriptEvent);
    } catch {
      // Ignore anything that is not a JSON event.
    }
  };
  return ws;
}
