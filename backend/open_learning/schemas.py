from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


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
    source_version: int = 1
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
    document_id: str | None = None
    replaces_document_id: str | None = None


class JobListItem(BaseModel):
    id: str
    status: str
    created_at: datetime
    document_id: str | None = None
    replaces_document_id: str | None = None


class FeedOutput(BaseModel):
    id: str
    url: str
    title: str
    created_at: datetime
    last_fetched_at: datetime | None
    last_error: str | None
    consecutive_failures: int
    paused: bool


class DiscoveredOutput(BaseModel):
    id: str
    feed_id: str
    url: str
    title: str
    published_at: datetime | None
    snippet: str
    status: Literal["new", "ingested", "dismissed"]
    created_at: datetime
    score: float = 0
    matched_topics: list[str] = Field(default_factory=list)


class DiscoveryPage(BaseModel):
    items: list[DiscoveredOutput]
    next_cursor: str | None


class NoteOutput(BaseModel):
    document_id: str
    title: str
    label: str | None
    created_at: datetime
    char_count: int
    version: int = 1
    replaces_document_id: str | None = None
    superseded: bool = False


class NoteTextOutput(BaseModel):
    document_id: str
    title: str
    label: str | None
    version: int
    text: str


class LimitsOutput(BaseModel):
    max_document_chars: int
    min_text_chars: int
    max_title_chars: int
    max_label_chars: int


class ReviewOutput(BaseModel):
    id: str
    insight_id: str
    question: str
    due_at: datetime
    interval_days: float
    insight_title: str
    document_id: str


class Citation(BaseModel):
    document_id: str
    source_url: str
    source_kind: Literal["url", "note"]
    source_label: str
    source_version: int = 1
    passage: str


class TutorOutput(BaseModel):
    answer: str
    question: str
    citations: list[Citation]
    review_id: str
