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
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  upload: <T>(path: string, form: FormData) =>
    request<T>(path, { method: "POST", body: form }),
};

// ---------------------------------------------------------------------- types

export type Language = "en" | "id" | "zh";
export type DocType = "job_requirement" | "knowledge_base";

export interface Interview {
  id: string;
  title: string;
  position_title: string;
  language: string;
  guardrail_notes: string | null;
  status: string;
  voice_id: string | null;
  avatar_id: string | null;
  created_at: string;
}

export interface InterviewDocument {
  id: string;
  doc_type: DocType;
  source: "upload" | "pasted";
  filename: string | null;
  index_status: "pending" | "indexing" | "indexed" | "failed";
  index_error: string | null;
  chunk_count: number;
  created_at: string;
}

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
  ready: boolean;
  missing: string[];
  mock_ai: boolean;
  has_requirements: boolean;
  plan_item_count: number;
  can_create_sessions: boolean;
  blockers: string[];
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

export interface RTCCredentials {
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

export interface Candidate {
  id: string;
  name: string;
  email: string | null;
  position_title: string;
  session_status: string;
  interviewed_at: string | null;
}

// -------------------------------------------------------------- live transcript

export type TranscriptEvent =
  | ({ type: "turn" } & Turn)
  | { type: "partial"; speaker: string; text: string; final: boolean }
  | { type: "status"; status: string; reason?: string }
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
