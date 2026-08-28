from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

DocType = Literal["job_requirement", "knowledge_base"]
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
    source: Literal["upload", "pasted"]
    filename: str | None
    index_status: str
    index_error: str | None
    chunk_count: int
    created_at: datetime


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
    created_at: datetime


class InterviewDetail(InterviewOut):
    documents: list[DocumentOut] = []
    plan_items: list[PlanItemOut] = []
    readiness: dict = {}


class PasteDocument(BaseModel):
    doc_type: DocType
    content_text: str = Field(min_length=1)
    filename: str | None = None


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
    app_id: str
    room_id: str
    user_id: str
    token: str
    task_id: str | None
    avatar_id: str | None


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


# --------------------------------------------------------------- candidates stub


class CandidateOut(BaseModel):
    id: str
    name: str
    email: str | None
    position_title: str
    session_status: str
    interviewed_at: datetime | None
