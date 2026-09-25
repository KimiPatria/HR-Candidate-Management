# AI Candidate Interviewer

HR describes a job, candidates take a live avatar-led interview over BytePlus RTC, and
results land on a candidate page.

Three HR surfaces:

- **Interviews** — one per position. Add the job description and the question plan and
  scoring rubric are drafted from it automatically; you review and approve the rubric,
  then invite candidates. Laid out as three steps rather than one long page.
- **Company knowledge** — facts about the business, added once and shared by every
  interview. Static by nature, so it lives outside any one position.
- **Candidates** — everyone interviewed, their transcripts and their verdicts.

Job descriptions and company knowledge can be pasted, uploaded, or picked straight out of
Google Drive or OneDrive. Whatever the source, the extracted text stays readable after
indexing, and the original file stays downloadable.

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

## Scoring a finished interview

After an interview ends, an LLM judge reads the saved transcript and files the candidate
into one of four tiers, with a plain-language summary underneath. HR still decides; the
judge only triages.

**Strong Fit · Decent Fit · Not a Fit · Inconclusive.** Four, named, and wide on purpose.
An LLM judge cannot reproduce a 0-100 score, or even a 1-5 star, consistently across runs
on the same transcript - the run-to-run noise swamps the signal at that resolution. It
*can* reliably tell "clearly good", "mixed", "clearly short" and "I could not read this"
apart. So there is no number anywhere in this feature, and the prompt forbids inventing
one.

**Inconclusive is not a soft rejection.** It exists so a candidate is never marked down
for our transcription. The judge rates transcript quality as a separate output, and an
`unusable` rating forces Inconclusive regardless of what the criteria found. That override
is Python, in `evaluator._resolve_verdict`, not a prompt instruction - it is far too
important to leave to the model's own discipline. A session where the candidate barely
spoke short-circuits to Inconclusive without calling the model at all.

**Every dimension verdict must cite the transcript.** The judge returns candidate turn
numbers for each dimension; `evaluator._apply_evidence_gate` checks each citation against
the real transcript and drops any that does not exist or that points at the interviewer
rather than the candidate. A positive verdict left with no surviving citation is
downgraded to `not_evidenced` - which is why that value exists separately from
`not_a_fit`, the same way Inconclusive is separate at the top level. Nothing said is not
the same as something said badly. Both overrides are shown to HR on the candidate page,
never applied silently.

The judge runs once per finished interview - no self-consistency sampling - and is
scheduled from every path that can end one (HR ends it, the candidate ends it, the RTC
callback fires when they close the tab), off the response path. `POST
/api/candidates/{id}/score` re-runs it when the rubric changed or a pass failed.

### The rubric

Three fixed dimensions, the same on every rubric, because that is what makes two
candidates for two different jobs comparable at all:

1. **Relevant Background & Domain Fit** - does their stated experience credibly match the role
2. **On-the-Spot Reasoning** - can they think through a scenario, not just recite
3. **Communication Clarity** - can they explain their own work understandably

What varies per job is only what Strong / Decent / Not a Fit *evidence* looks like for
that role. HR authors those nine band definitions on the interview setup page, either by
drafting them from the job spec with the LLM or by writing them from a blank rubric; both
paths land in the same editor. Bands are calibrated for a short get-to-know conversation,
so "Strong" means clear credible signal, not an exhaustive deep dive.

Nothing is scored until HR presses **Approve**. An auto-draft is a starting point, and
scoring real people against un-reviewed model output would launder a guess into a hiring
signal. Editing an approved rubric sends it back to draft - the approval was a statement
about specific wording, so once the wording moves it no longer refers to anything.
Verdicts record the rubric version they were judged against, and the candidate page says
so when the rubric has moved on since.

## Mock mode

`MOCK_AI=true` (the default) makes every provider return canned data:

| Service | Mock behaviour |
| --- | --- |
| ModelArk | Deterministic replies driven by the planner directive in the prompt; real JSON for the rubric drafter and the scoring judge |
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

Most of `byteplus_rtc.py` is no longer a guess: it's been cross-checked against the
official reference implementation in `byteplus-sdk/RTC_AIGC_Demo` (both the Node server
and the React client's typed config layer) and, further, proven against the **live**
`StartVoiceChat` API using this account's real credentials — a call with the ASR/TTS/
LLM shape below returned `{"Result": "ok"}`, confirming the token algorithm, the V4
request signature, and the ASR/TTS/LLM field names all work end-to-end. What remains:

| # | File | What needs checking |
| --- | --- | --- |
| 1 | `api/rtc_webhook.py` | Callback event names and payload envelope. |
| 2 | `api/rtc_webhook.py` | Subtitle callback format — docs say binary, this assumes JSON. Affects the live transcript panel, not audio/video/avatar. |
| 3 | `services/byteplus_rtc.py` | Whether `en-US`/`zh-CN` are really the only ASR language options — no Bahasa Indonesia in the confirmed type, so an `id`-language interview currently falls back to `en-US`. On the `local` path this turned out to be real and is worked around; see **Indonesian recognition** below. |
| 4 | `services/rag.py` | VDB KnowledgeBase real ingestion needs `add_type: tos\|url\|lark`, not inline text — mock mode's local search covers this for now. |
| 5 | `services/memory.py` | VikingDB Memory endpoint paths and payloads — optional, falls back to reading recent turns from the transcript table. |
| 6 | `services/tts_voice.py` | Voice Replication upload endpoint — not needed since this account uses stock voices/avatars. |
| 7 | `frontend/src/lib/rtc.ts` | Web SDK method names (`createEngine` / `joinRoom` / `setRemoteVideoPlayer` / `startAudioCapture`). |

The RTC Web SDK is an **optional** dependency, loaded by dynamic import. Without it the
interview page runs in mock mode and stays fully clickable. Install when ready:

```bash
cd frontend && npm install @byteplus/rtc
```

---

## Avatar and voice: using BytePlus stock presets (no recording needed)

No custom voice cloning or avatar training required. Two things confirmed live:

- **Avatar**: this account's RTC app is provisioned for the **Akool** third-party avatar
  integration, not native BytePlus/Volcano avatar — proven by a real `StartVoiceChat`
  call: the native avatar shape was rejected ("akool avatar: ProviderParams is
  required"), while an Akool-shaped payload was accepted. `AVATAR_ID` defaults to
  `dvp_Tristan_cloth2_1080P` ("Tristan"), a real confirmed Akool preset. The one missing
  piece is `AKOOL_API_KEY`, which comes from **Akool (akool.com)**, not the BytePlus
  console — ask whoever originally set up this RTC app's avatar entitlement how that key
  was provisioned, since it has to be tied to the same account for Akool to authorize it.
- **Voice**: `TTS_VOICE_ID` defaults to `en_male_tim_uranus_bigtts`, a real Seed TTS 2.0
  stock voice name (not a credential — swap for any other `*_uranus_bigtts` voice from
  BytePlus's list, e.g. `en_female_stokie_uranus_bigtts`).

`scripts/train_avatar.py` still exists for the opposite case — training a custom avatar
from a video of a consenting real person — but is not needed for the current setup.

## Going live

`MODELARK_ENDPOINT_ID` is filled in and `MOCK_AI=false` works for `VOICE_MODE=local`.

For `VOICE_MODE=rtc`, one thing is confirmed still missing as of the last check:

- **`SEED_SPEECH_APP_ID` / `SEED_SPEECH_ACCESS_TOKEN`** — these are the legacy pair
  StartVoiceChat's `ASRConfig`/`TTSConfig` need, from a *different* console page than the
  modern `SEED_SPEECH_API_KEY` above: `console.byteplus.com/voice/app` ("Old Console") →
  Create Application → Trial/Official Use, which surfaces an App ID, an Access Token, and
  a Secret Key (the last currently unused by any call here) as three separate values.
  `app/services/speech_check.py` verifies these live at startup and will say plainly
  whether they were accepted, still hold a copy of the modern key, or are simply unset.

Once those are filled:

1. Start a tunnel so RTC can reach your machine, and set `PUBLIC_BASE_URL` to it:
   ```bash
   cloudflared tunnel --url http://localhost:8000
   ```
   RTC needs `PUBLIC_BASE_URL/v1/chat/completions` and `PUBLIC_BASE_URL/rtc/callback`.
   `CANDIDATE_APP_URL` is separate — that is the frontend origin the candidate opens.
2. Set `MOCK_AI=false`.
3. Create an interview, add job-requirement documents, generate the plan, create a
   candidate session, and open the join link.

---

## Layout

```
backend/app/
  api/        auth, interviews, documents, knowledge, integrations, sessions,
              llm, rtc_webhook, candidates, rubrics, translate
  core/       config, db, migrate, security
  models/     Interview, InterviewDocument, DocumentChunk, PlanItem,
              InterviewSession, SessionPlanProgress, TranscriptTurn,
              Rubric, RubricDimension, Evaluation, EvaluationDimension
  services/   byteplus_rtc, modelark, rag, memory, guardrails, ingest,
              autopilot, tts_voice, planner, prompts, turns, transcript_hub,
              extract, rubric, evaluator
  scripts/    smoke_test.py
frontend/src/
  pages/      InterviewsPage, InterviewWorkspace, KnowledgePage,
              InterviewPage, CandidatesPage, CandidateDetailPage, LoginPage
  components/ AvatarStage, TranscriptPanel, DocumentSources, DocumentList,
              SessionsPanel, RubricEditor
  lib/        api.ts (fetch + types + WebSocket), drive.ts (Drive/OneDrive
              pickers), rtc.ts (SDK wrapper)
```

Documents live in one table across two scopes. A job-requirement document carries the
interview it belongs to; a company knowledge-base document carries a null interview id,
and `rag.search` treats a null-scoped chunk as in scope for every interview. That is the
whole of the company-wide knowledge base — no second pipeline, no second viewer.

`core/migrate.py` applies the schema changes `Base.metadata.create_all` cannot make on
an existing SQLite file (added columns, dropped NOT NULLs). It runs on every start and is
a no-op once the database is current. Swap it and `create_all` for Alembic together.

The browser API lives under `/api`; `/v1/chat/completions` and `/rtc/callback` stay at
the root because BytePlus calls them. Vite proxies `/api` (WebSocket included) to
FastAPI, so it is all one origin and the HR cookie just works.

---

## Decisions made along the way

- **Setup autopilot** — indexing a job description runs plan generation and the rubric
  draft on its own, because both are derived from that one input and asking for them
  separately was asking HR to press two buttons whose answer was already determined.
  Rubric *approval* stays manual and always will: scoring people against un-reviewed
  model output would launder a guess into a hiring decision. An approved rubric is never
  redrafted underneath.
- **Drive imports download in the browser** — the file is fetched by the browser from
  Google's or Microsoft's own API and re-uploaded to us as ordinary bytes. Sending a URL
  and a token for the server to fetch would put a user's drive credential in our process
  and turn our backend into something that fetches URLs a client chose.
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
- **Scoring is four named tiers, never a number** — see "Scoring" above. An LLM judge
  cannot reproduce a fine-grained score across runs; it can tell four wide tiers apart.
- **A rubric is a warning, not a blocker** — an interview with no approved rubric still
  runs and still records a transcript. It just produces no verdict until one is
  approved, and the candidate can then be scored retroactively. Blocking on it would
  have stranded every interview created before scoring existed.

## Known limits

- `transcript_hub` is in-process. More than one uvicorn worker means a subscriber on
  worker A never sees a turn published on worker B — swap it for Redis pub/sub before
  scaling out.
- `create_all` handles no migrations. Schema changes need a fresh DB or Alembic.
- The repo lives in a OneDrive-synced folder. `node_modules` and `.venv` in a synced
  directory cause sync thrash and slow installs — worth moving to a local path.

### Indonesian recognition (stop-gap)

BytePlus Seed ASR cannot hear Bahasa Indonesia on its **streaming** models. Confirmed
twice: once by probing the gateway directly with synthesised Indonesian audio, and once
by the same phrases in the BytePlus console playground, which returned Chinese. A support
question is open with BytePlus; until it is answered, this is a workaround rather than a
fix.

Fed identical Indonesian audio, one account, one API key, one resource id:

| Model | Path | Heard |
| --- | --- | --- |
| streaming | `/api/v3/sauc/bigmodel_async` | `Syndicate a bug a Manager, Project de Bruijn Technology…` |
| streaming | `/api/v3/sauc/bigmodel` | `Cybergeeks. Baggy Manager. Cyborg Person. Technology…` |
| non-streaming | `/api/v3/sauc/bigmodel_nostream` | `Saya bergabung sebagai manager proyek di perusahaan technology…` |

A `language` field is accepted on all three and changes nothing on any of them — `id-ID`,
`en-US` and omitting it returned byte-identical transcripts. Only the choice of model
matters, so that is what `voice/asr.py:for_language()` switches on, keyed off
`STREAMING_LANGUAGES`. English and Mandarin keep the streaming recogniser; anything else
gets `UtteranceASRStream`.

What the non-streaming model costs, since it returns nothing until an utterance ends:

- **End-of-utterance is ours to detect.** The capture gate in `micWorklet.js` already
  computes it, so it now reports every change as `{"type": "speech"}` on the voice
  socket. `UtteranceASRStream` opens one recognition connection per utterance and closes
  it on that cue. A silence watchdog finalises anyway if the cue is lost.
- **Latency grows with answer length** — measured ~0.3 s after a 4 s answer, ~1.5 s after
  8 s, ~2.9 s after 48 s. `_END_OF_TURN_SECONDS_UTTERANCE` is cut to 0.4 s to compensate,
  and answers longer than a minute are recognised in pieces and rejoined.
- **No live captions.** This model volunteers a progress result only about every 23 s of
  audio. The `agent_status` strip is driven off the capture gate instead, so the
  candidate still gets feedback that they are being heard.
- **Barge-in comes from the gate**, not from the words: an interruption cannot be
  reported by a recogniser that stays silent until the speaker stops. Sustained speech
  over the interviewer for `_BARGE_IN_HOLD_SECONDS` counts as cutting in.

When BytePlus ships Indonesian on the streaming model, add `"id"` to
`STREAMING_LANGUAGES` and the whole utterance path stops being reachable.
