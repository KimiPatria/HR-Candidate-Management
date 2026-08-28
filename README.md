# AI Candidate Interviewer

Three-page app: HR configures an interview from job requirements and reference docs,
candidates take a live avatar-led interview over BytePlus RTC, results land on a
candidate page.

The full vertical slice is built and runs end to end today in **mock mode** — no
credentials, no network calls. Switching to live is a matter of filling `.env` and
working through the VERIFY list below.

---

## Running it

First-time setup:

```bash
cd backend && uv venv && uv pip install -e . && cp .env.example .env && cd ..
cd frontend && npm install && cd ..
```

Then, one command from the repo root starts both the backend (uvicorn --reload) and the
frontend (Vite dev server), interleaving their logs, and stops both on Ctrl+C:

```bash
python dev.py
```

Open http://localhost:5173 and sign in with `HR_PASSWORD` from `backend/.env`
(the checked-in dev value is `wilmar-dev`).

To prove the whole pipeline without touching the UI:

```bash
cd backend && .venv/Scripts/python scripts/smoke_test.py
```

That walks auth → interview → document ingestion → plan generation → session creation →
consent → join → six conversation turns → guardrail deflections → SSE streaming shape →
transcript → teardown, and prints a pass/fail line for each. It currently passes clean.

---

## How a turn actually works

`POST /v1/chat/completions` is the endpoint RTC calls on every candidate utterance. The
ordering inside it is deliberate — everything cheap and local happens before anything
that costs a network round trip, because time-to-first-token is what the candidate hears
as responsiveness:

1. **Planner decides** ask / follow-up / wrap-up from `SessionPlanProgress` (local DB).
2. **Rule-based guardrail** on the utterance — injection, off-task, hiring-outcome
   questions. Microseconds, no network. A block short-circuits here with a canned
   deflection and never calls the LLM at all.
3. **Retrieval and memory in parallel** — one round trip, not two.
4. **Retrieval-confidence guardrail**: if the candidate asked a question the documents
   do not cover, deflect rather than let the model invent facts about the company.
5. **Stream ModelArk** back as OpenAI-format SSE chunks.
6. **Persist turns**, then run the LLM audit *off* the hot path.

The LLM-based guardrail from the original plan moved to step 6. Running it inline would
have added a full round trip of dead air before every single answer.

### Who drives the interview

`PlanItem` rows are generated once per **interview** (not per session), so every
candidate for a position gets the same core questions and answers stay comparable.
`SessionPlanProgress` tracks per-candidate coverage — which question is open, how many
follow-ups it has had, what is still pending. That is the state that lets the
interviewer probe a thin answer, move on when a topic is covered, and know when it is
done. Coverage is judged heuristically (answer length, follow-up cap of 2) rather than
with an LLM call, again for latency; the cap guarantees forward progress even when the
heuristic is wrong.

---

## Mock mode

`MOCK_AI=true` (the default) makes every provider return canned data:

| Service | Mock behaviour |
| --- | --- |
| ModelArk | Deterministic replies driven by the planner directive in the prompt |
| VDB KnowledgeBase | Real local TF-IDF search over document text in the DB |
| VikingDB Memory | Recent turns read from `TranscriptTurn` |
| RTC | `StartVoiceChat` / `StopVoiceChat` skipped, synthetic room token |
| Voice / avatar | Synthetic ids, readiness checks pass |

Retrieval is deliberately *real* in mock mode so the guardrail confidence thresholds and
prompt assembly can be tuned before credentials land.

Note the mock LLM returns non-JSON, so plan generation falls back to
`planner._fallback_plan` — four generic questions. That is the fallback path working as
intended; with live ModelArk you get 8–12 questions generated from the job spec.

---

## Before going live — the VERIFY list

Everything below is written against BytePlus APIs I could not verify without your docs
and console. Each is marked `VERIFY` in the source. **Do not flip `MOCK_AI=false` before
working through these**, and please send me the RTC conversational-AI docs so I can
close them out properly.

| # | File | What needs checking |
| --- | --- | --- |
| 1 | `services/byteplus_rtc.py` | `generate_token` — AccessToken v001 binary layout. A byte-level mismatch produces tokens the SDK rejects silently at join. Cross-check against the official AccessToken sample. |
| 2 | `services/byteplus_rtc.py` | `_signed_headers` — Volc V4 request signature. |
| 3 | `services/byteplus_rtc.py` | `_voice_chat_config` — StartVoiceChat payload, especially the `LLMConfig` custom-LLM block and the Flash Avatar block. |
| 4 | `api/llm.py` | The exact request shape RTC sends, above all **how it identifies the session**. `_resolve_session_id` accepts a custom header, several body fields, and a `SESSION_ID:` system message, so it should keep working whichever one is real — but confirm. |
| 5 | `api/rtc_webhook.py` | Callback event names and payload envelope. |
| 6 | `services/rag.py` | VDB KnowledgeBase index/search endpoint paths and payloads. |
| 7 | `services/memory.py` | VikingDB Memory endpoint paths and payloads. |
| 8 | `services/modelark.py` | Base URL and endpoint-id-as-`model`. |
| 9 | `services/tts_voice.py` | Voice Replication upload endpoint. |
| 10 | `frontend/src/lib/rtc.ts` | Web SDK method names (`createEngine` / `joinRoom` / `setRemoteVideoPlayer` / `startAudioCapture`). |
| 11 | `services/byteplus_rtc.py` | Which field inside `AvatarConfig.ProviderParams` selects the trained avatar character - guessing `AvatarId` set to the `resource_id` from `scripts/train_avatar.py`. |
| 12 | `api/rtc_webhook.py` | Subtitle callback format - docs say binary, this assumes JSON. |

The RTC Web SDK is an **optional** dependency, loaded by dynamic import. Without it the
interview page runs in mock mode and stays fully clickable. Install when ready:

```bash
cd frontend && npm install @byteplus/rtc
```

---

## Avatar setup

The Flash Avatar console gives you a *batch* training/rendering API
(`byteplus_sdk.visual.VisualService`), not a live SDK — it trains a lip-sync model from
a video of a real person and hands back a `resource_id`. That training step is one-time
setup; `scripts/train_avatar.py` wraps it:

```bash
cd backend
uv pip install -e ".[avatar]"
.venv/Scripts/python scripts/train_avatar.py <public-https-url-of-a-video>
```

It needs `BYTEPLUS_ACCESS_KEY`/`BYTEPLUS_SECRET_KEY` already in `.env` (same account
keys used elsewhere — this is not a separate credential) and prints the resulting
`resource_id` to paste in as `AVATAR_ID`. The video must be a publicly fetchable HTTPS
URL and, since it trains a likeness, of someone who has given written consent.

What is *not* yet confirmed: whether that `resource_id` is literally the value BytePlus
RTC's live `AvatarConfig` expects for real-time rendering during an interview, or where
exactly in that config it goes — see VERIFY #11 below. Training is proven to work
(a real submit call above got a normal structured response); wiring the result into a
live room is the remaining unknown.

## Going live

1. Fill `backend/.env` (see `.env.example`). `GET /health` lists what is still missing.
2. Start a tunnel so RTC can reach your machine, and set `PUBLIC_BASE_URL` to it:
   ```bash
   cloudflared tunnel --url http://localhost:8000
   ```
   RTC needs `PUBLIC_BASE_URL/v1/chat/completions` and `PUBLIC_BASE_URL/rtc/callback`.
   `CANDIDATE_APP_URL` is separate — that is the frontend origin the candidate opens.
3. Work through the VERIFY table.
4. Set `MOCK_AI=false`.

---

## Layout

```
backend/app/
  api/        auth, interviews, documents, sessions, llm, rtc_webhook, candidates
  core/       config, db, security
  models/     Interview, InterviewDocument, PlanItem,
              InterviewSession, SessionPlanProgress, TranscriptTurn
  services/   byteplus_rtc, modelark, rag, memory, guardrails,
              tts_voice, planner, prompts, turns, transcript_hub, extract
  scripts/    smoke_test.py
frontend/src/
  pages/      SetupPage, InterviewPage, CandidatesPage, LoginPage
  components/ AvatarStage, TranscriptPanel, DocumentUploader
  lib/        api.ts (fetch + types + WebSocket), rtc.ts (SDK wrapper)
```

The browser API lives under `/api`; `/v1/chat/completions` and `/rtc/callback` stay at
the root because BytePlus calls them. Vite proxies `/api` (WebSocket included) to
FastAPI, so it is all one origin and the HR cookie just works.

---

## Decisions made along the way

- **HR auth** — one shared password from env, HMAC-signed HTTP-only cookie, 12h.
- **Documents** — original file kept on disk next to the extracted text, so a bad
  extraction is recoverable. `.pdf`, `.docx`, `.txt`, `.md`; scanned PDFs are rejected
  with a message rather than indexed as empty.
- **Transcript delivery** — WebSocket, with `GET /sessions/{id}/transcript` as a polling
  fallback. Reconnects replay history. RTC subtitles feed *interim* captions only and
  are never persisted, because the LLM endpoint already writes the final text once —
  persisting both would double every line.
- **Consent** — a session cannot start until the candidate accepts a recording notice
  (`consent_accepted_at`). Relevant given Wilmar's SG/ID footprint; the wording in
  `InterviewPage.tsx` is a placeholder your legal team should replace.
- **Join links** — single URL-safe token, 72h expiry by default, expiry enforced on
  every candidate route.
- **Interview limits** — 30 minutes or 60 turns, then the planner forces a wrap-up.
- **Timestamps** — a `TZDateTime` column type normalises everything to aware UTC, since
  SQLite reads back naive datetimes and would otherwise crash every comparison.
- **DB** — SQLite by default, models kept Postgres-compatible (string UUIDs, no
  dialect-specific types). `create_all` on startup; swap to Alembic once the schema
  settles.
- **Evaluation is out of scope** — no scoring, no rubric. The candidates page lists real
  sessions and their status; that is all it claims to do.

## Known limits

- `transcript_hub` is in-process. More than one uvicorn worker means a subscriber on
  worker A never sees a turn published on worker B — swap it for Redis pub/sub before
  scaling out.
- `create_all` handles no migrations. Schema changes need a fresh DB or Alembic.
- The repo lives in a OneDrive-synced folder. `node_modules` and `.venv` in a synced
  directory cause sync thrash and slow installs — worth moving to a local path.
