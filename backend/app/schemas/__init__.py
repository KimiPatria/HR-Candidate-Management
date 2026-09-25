from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

DocType = Literal["job_requirement", "knowledge_base"]
# Where a document came from. The drive values are kept distinct from "upload" so the
# document list can show provenance and offer a link back to the file in its drive.
DocSource = Literal["upload", "pasted", "google_drive", "onedrive"]
DriveProvider = Literal["google_drive", "onedrive"]
Language = Literal["en", "id", "zh"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------------- auth


class LoginRequest(BaseModel):
    password: str


# -------------------------------------------------------------------- interview


class InterviewCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    position_title: str = Field(min_length=1, max_length=200)
    language: Language = "en"
    guardrail_notes: str | None = None


class InterviewUpdate(BaseModel):
    title: str | None = None
    position_title: str | None = None
    language: Language | None = None
    guardrail_notes: str | None = None
    status: Literal["draft", "ready", "archived"] | None = None


class DocumentOut(ORMModel):
    id: str
    doc_type: DocType
    source: DocSource
    filename: str | None
    source_url: str | None = None
    index_status: str
    index_error: str | None
    chunk_count: int
    # Both computed properties on the model, not columns. They are what turns the list
    # from "indexed, 4 chunks" into something a reader can sanity-check: how much text
    # came out, and whether the original file is still there to open.
    char_count: int = 0
    has_original_file: bool = False
    created_at: datetime


class DocumentContentOut(DocumentOut):
    """A document with the text that was actually indexed.

    Kept off `DocumentOut` on purpose: the setup page lists documents on every poll while
    indexing settles, and shipping the full text of every job description and company
    handbook on each of those is a lot of bytes nobody asked for. It is fetched once,
    when someone opens a document to read it.
    """

    content_text: str


class PlanItemOut(ORMModel):
    id: str
    order_index: int
    question: str
    competency: str | None
    must_ask: bool
    coverage_hint: str | None


class InterviewOut(ORMModel):
    id: str
    title: str
    position_title: str
    language: str
    guardrail_notes: str | None
    status: str
    voice_id: str | None
    avatar_id: str | None
    # Whether plan generation and the rubric draft are running off the back of a
    # job-description upload, and what failed if they did. See services/autopilot.py.
    autopilot_status: str = "idle"
    autopilot_step: str | None = None
    autopilot_error: str | None = None
    created_at: datetime
    # Computed, not a DB column - `status` alone doesn't reflect blockers added after
    # the plan was generated (a document un-indexed, TTS voice unset). Defaults to False
    # so routes that return a bare Interview ORM object (create/update) don't 500; the
    # list and detail routes always fill in the real value.
    can_create_sessions: bool = False


class InterviewDetail(InterviewOut):
    documents: list[DocumentOut] = []
    plan_items: list[PlanItemOut] = []
    readiness: dict = {}


class PasteDocument(BaseModel):
    doc_type: DocType
    content_text: str = Field(min_length=1)
    filename: str | None = None


# ------------------------------------------------------------------ integrations


class DriveIntegration(BaseModel):
    """What the browser needs to open one drive's file picker.

    These are public client identifiers - the kind that ship inside any single-page app
    that talks to Google or Microsoft - not secrets. The user's own OAuth consent is what
    grants access to their files, and that token never leaves their browser.
    """

    enabled: bool = False
    client_id: str = ""
    api_key: str = ""
    app_id: str = ""


class IntegrationsOut(BaseModel):
    google_drive: DriveIntegration
    onedrive: DriveIntegration


# ---------------------------------------------------------------------- session


class SessionCreate(BaseModel):
    candidate_name: str = Field(min_length=1, max_length=200)
    candidate_email: EmailStr | None = None
    expires_in_hours: int = Field(default=72, ge=1, le=720)


class SessionOut(ORMModel):
    id: str
    interview_id: str
    candidate_name: str
    candidate_email: str | None
    status: str
    rtc_room_id: str | None
    started_at: datetime | None
    ended_at: datetime | None
    created_at: datetime


class SessionCreated(SessionOut):
    join_url: str
    join_token: str
    token_expires_at: datetime | None


class JoinInfo(BaseModel):
    """What the candidate page can see before consenting. Deliberately minimal - no
    interview internals, no plan, no other candidates."""

    session_id: str
    candidate_name: str
    position_title: str
    interview_title: str
    language: str
    status: str
    consent_required: bool


class RTCCredentials(BaseModel):
    """What the candidate page needs to start talking.

    `voice_mode` tells the frontend which engine to build. In "local" mode the RTC
    fields below are empty and unused - the browser opens our own /join/{token}/voice
    WebSocket instead of joining an RTC room - so they are optional rather than
    required. See `app/core/config.py::voice_mode`.
    """

    voice_mode: str = "rtc"
    app_id: str = ""
    room_id: str = ""
    user_id: str = ""
    token: str = ""
    task_id: str | None = None
    avatar_id: str | None = None


class TurnOut(ORMModel):
    id: str
    turn_index: int
    speaker: str
    text: str
    guardrail_action: str | None
    timestamp: datetime


class TranscriptOut(BaseModel):
    session_id: str
    status: str
    turns: list[TurnOut]


# ------------------------------------------------------------------ translation


class TranscriptTranslationOut(BaseModel):
    """Translations keyed by turn id, so the frontend can overlay them onto the turns it
    already holds instead of re-fetching a parallel transcript that could drift."""

    session_id: str
    target_language: str
    translations: dict[str, str]
    # False when the provider could not be reached and the caller is looking at the
    # original text. The UI says so rather than pretending the languages match.
    translated: bool = True
    detail: str | None = None


class DocumentTranslationOut(ORMModel):
    document_id: str
    target_language: str
    source_language: str | None
    text: str
    provider: str
    used_for_indexing: bool
    created_at: datetime


# ----------------------------------------------------------------------- rubric

DimensionKey = Literal["background_fit", "on_the_spot_reasoning", "communication_clarity"]
# Four tiers, named and wide. See app/models/rubric.py for why there is no number here.
Verdict = Literal["strong_fit", "decent_fit", "not_a_fit", "inconclusive"]
DimensionVerdict = Literal["strong", "decent", "not_a_fit", "not_evidenced"]
TranscriptQuality = Literal["usable", "degraded", "unusable"]
InterviewCompleteness = Literal["complete", "partial"]


class RubricDimensionIn(BaseModel):
    """One dimension's band definitions as HR edits them. The dimension itself is not
    editable - only what Strong, Decent and Not a Fit mean for this particular job."""

    key: DimensionKey
    strong: str = ""
    decent: str = ""
    not_fit: str = ""


class RubricSave(BaseModel):
    dimensions: list[RubricDimensionIn]


class RubricDimensionOut(BaseModel):
    key: str
    label: str
    intent: str
    order_index: int
    strong: str
    decent: str
    not_fit: str
    filled: bool


class RubricOut(BaseModel):
    id: str
    interview_id: str
    status: Literal["draft", "approved"]
    source: Literal["ai_draft", "manual"]
    version: int
    approved_at: datetime | None
    updated_at: datetime
    complete: bool
    dimensions: list[RubricDimensionOut]


# ------------------------------------------------------------------- evaluation


class EvidenceOut(BaseModel):
    """A candidate turn the judge's verdict rests on. `turn` is the real turn_index, so
    the UI can point HR at the exact line in the transcript below."""

    turn: int
    quote: str


class EvaluationDimensionOut(BaseModel):
    key: str
    label: str
    intent: str
    order_index: int
    verdict: DimensionVerdict
    note: str
    evidence: list[EvidenceOut]
    evidence_warning: str | None


class EvaluationOut(BaseModel):
    id: str
    status: Literal["complete", "failed"]
    error: str | None
    verdict: Verdict | None
    # What the three dimensions alone supported, before the transcript-quality gate.
    criteria_verdict: Literal["strong_fit", "decent_fit", "not_a_fit"] | None
    adjustment_reason: str | None
    transcript_quality: TranscriptQuality | None
    transcript_quality_note: str | None
    # Whether the conversation finished. Null on evaluations judged before this field
    # existed, and on any run where the judge did not answer - the UI treats null as
    # "not assessed" rather than assuming the interview completed.
    interview_completeness: InterviewCompleteness | None
    interview_completeness_note: str | None
    summary: str
    model: str | None
    candidate_turn_count: int
    rubric_version: int | None
    completed_at: datetime | None
    dimensions: list[EvaluationDimensionOut]


class ScoringState(BaseModel):
    """Whether this candidate can be scored right now, and what is in the way."""

    can_score: bool
    blockers: list[str]
    rubric_approved: bool
    rubric_current_version: int | None
    scored_against_version: int | None
    # True when the rubric has been edited since this verdict was produced, which makes
    # the verdict a reading of wording that is no longer on screen.
    rubric_stale: bool


# ------------------------------------------------------------------- candidates


class CandidateOut(BaseModel):
    id: str
    name: str
    email: str | None
    position_title: str
    session_status: str
    interviewed_at: datetime | None
    # Null until the interview finishes and the judge runs.
    verdict: Verdict | None = None
    evaluation_status: Literal["complete", "failed"] | None = None
    # Qualifies the verdict rather than replacing it. On the list this is the difference
    # between a Decent Fit reached on a full conversation and one reached on two answers,
    # which is otherwise invisible until the candidate is opened.
    interview_completeness: InterviewCompleteness | None = None


class CandidateDetail(BaseModel):
    id: str
    name: str
    email: str | None
    interview_id: str
    interview_title: str
    position_title: str
    language: str
    session_status: str
    created_at: datetime
    interviewed_at: datetime | None
    ended_at: datetime | None
    failure_reason: str | None
    turns: list[TurnOut]
    evaluation: EvaluationOut | None
    rubric: RubricOut | None
    scoring: ScoringState
