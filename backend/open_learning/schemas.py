from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class InsightOutput(BaseModel):
    id: str
    title: str
    idea: str
    explanation: str
    why_it_matters: str
    source_title: str
    source_url: str
    source_kind: Literal["url", "note"]
    source_label: str
    document_id: str
    evidence: str
    tags: list[str]
    saved: bool
    coverage: str
    source_truncated: bool
    model: str
    model_endpoint: str


class JobOutput(BaseModel):
    id: str
    status: str
    error: str | None


class JobListItem(BaseModel):
    id: str
    status: str
    created_at: datetime


class NoteOutput(BaseModel):
    document_id: str
    title: str
    label: str | None
    created_at: datetime


class ReviewOutput(BaseModel):
    id: str
    insight_id: str
    question: str
    due_at: datetime
    interval_days: float
    insight_title: str


class Citation(BaseModel):
    document_id: str
    source_url: str
    source_kind: Literal["url", "note"]
    source_label: str
    passage: str


class TutorOutput(BaseModel):
    answer: str
    question: str
    citations: list[Citation]
    review_id: str
