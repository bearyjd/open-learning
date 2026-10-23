from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    return datetime.now(UTC)


def ident() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint("kind IN ('url', 'note')", name="ck_sources_kind"),
        CheckConstraint("(kind = 'note') = (url LIKE 'note:%')", name="ck_sources_note_key"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ident)
    # A note's key is "note:" + content hash; it is internal and never sent to clients.
    url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(16), default="url", server_default="url")
    label: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("source_id", "content_hash", name="uq_document_content"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ident)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    title: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    generation_coverage: Mapped[str] = mapped_column(String(20), default="excerpt")
    extraction_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    # Note versions (E2b-4): an edit publishes version N+1 and marks the replaced row
    # superseded, which hides it from listings. No foreign key: versions are forgotten one by one.
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    replaces_document_id: Mapped[str | None] = mapped_column(String(36))
    superseded: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ident)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    # Optional vectors are deliberately unpopulated until a matching embedding model is configured.
    embedding: Mapped[list[float] | None] = mapped_column(Vector().with_variant(JSON(), "sqlite"))
    embedding_model: Mapped[str | None] = mapped_column(Text)


class Insight(Base):
    __tablename__ = "insights"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ident)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    chunk_id: Mapped[str] = mapped_column(ForeignKey("chunks.id"))
    title: Mapped[str] = mapped_column(Text)
    idea: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text)
    why_it_matters: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    model: Mapped[str] = mapped_column(Text)
    model_endpoint: Mapped[str] = mapped_column(Text)
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Topic(Base):
    __tablename__ = "topics"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ident)
    name: Mapped[str] = mapped_column(String(100), unique=True)


class Collection(Base):
    __tablename__ = "collections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ident)
    name: Mapped[str] = mapped_column(String(100))


class SavedItem(Base):
    __tablename__ = "saved_items"
    insight_id: Mapped[str] = mapped_column(ForeignKey("insights.id"), primary_key=True)
    collection_id: Mapped[str | None] = mapped_column(ForeignKey("collections.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Interaction(Base):
    __tablename__ = "interactions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ident)
    insight_id: Mapped[str] = mapped_column(ForeignKey("insights.id"), index=True)
    action: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DailyFeed(Base):
    __tablename__ = "daily_feeds"
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    insight_ids: Mapped[list[str]] = mapped_column(JSON, default=list)


class ReviewItem(Base):
    __tablename__ = "review_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ident)
    insight_id: Mapped[str] = mapped_column(ForeignKey("insights.id"), index=True, unique=True)
    question: Mapped[str] = mapped_column(Text)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    interval_days: Mapped[float] = mapped_column(Float, default=0)


class History(Base):
    __tablename__ = "history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ident)
    insight_id: Mapped[str] = mapped_column(ForeignKey("insights.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ReviewHistory(Base):
    __tablename__ = "review_history"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ident)
    review_id: Mapped[str] = mapped_column(ForeignKey("review_items.id"), index=True)
    rating: Mapped[str] = mapped_column(String(20))
    interval_days: Mapped[float] = mapped_column(Float)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Preference(Base):
    __tablename__ = "preferences"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "(kind = 'url' AND url IS NOT NULL AND payload_text IS NULL AND payload_title IS NULL "
            "AND payload_label IS NULL) OR (kind = 'note' AND url IS NULL)",
            name="ck_jobs_kind_payload",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ident)
    kind: Mapped[str] = mapped_column(String(16), default="url", server_default="url")
    url: Mapped[str | None] = mapped_column(Text)
    # Note text lives here until the job completes; a failed job keeps it so retry works.
    payload_title: Mapped[str | None] = mapped_column(Text)
    payload_text: Mapped[str | None] = mapped_column(Text)
    payload_label: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    error: Mapped[str | None] = mapped_column(Text)
    # Set when a note job completes; no foreign key (forget_note deletes linked jobs itself).
    document_id: Mapped[str | None] = mapped_column(String(36), index=True)
    # Set on a note edit: the note version this job's text replaces once it completes.
    replaces_document_id: Mapped[str | None] = mapped_column(String(36))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CloudBudget(Base):
    __tablename__ = "cloud_budget"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_cloud_budget_singleton"),
        CheckConstraint(
            "lifetime_limit IS NULL OR (lifetime_limit > 0 AND lifetime_limit < 1000000000000000000)",
            name="ck_cloud_limit",
        ),
        CheckConstraint(
            "spent >= 0 AND spent < 1000000000000000000 AND held >= 0 AND held < 1000000000000000000 AND debt >= 0 AND debt < 1000000000000000000",
            name="ck_cloud_balances",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    lifetime_limit: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    spent: Mapped[Decimal] = mapped_column(Numeric(30, 12), default=Decimal(0))
    held: Mapped[Decimal] = mapped_column(Numeric(30, 12), default=Decimal(0))
    debt: Mapped[Decimal] = mapped_column(Numeric(30, 12), default=Decimal(0))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CloudAttempt(Base):
    __tablename__ = "cloud_attempt"
    __table_args__ = (
        CheckConstraint(
            "reserved >= 0 AND reserved < 1000000000000000000 AND "
            "prompt_rate >= 0 AND prompt_rate < 1000000000000000000 AND "
            "completion_rate >= 0 AND completion_rate < 1000000000000000000 AND "
            "request_fee_cap >= 0 AND request_fee_cap < 1000000000000000000",
            name="ck_cloud_attempt_amounts",
        ),
    )
    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, default=ident)
    budget_id: Mapped[int] = mapped_column(ForeignKey("cloud_budget.id"), default=1)
    key_sha256: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(Text)
    provider_tag: Mapped[str] = mapped_column(Text)
    provider_name: Mapped[str] = mapped_column(Text)
    provider_name_unique: Mapped[bool] = mapped_column(Boolean)
    context_length: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    prompt_rate: Mapped[Decimal] = mapped_column(Numeric(30, 12))
    completion_rate: Mapped[Decimal] = mapped_column(Numeric(30, 12))
    request_fee_cap: Mapped[Decimal] = mapped_column(Numeric(30, 12))
    reserved: Mapped[Decimal] = mapped_column(Numeric(30, 12))
    metadata_sha256: Mapped[str] = mapped_column(String(64))
    metadata_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    request_bytes: Mapped[int] = mapped_column(Integer)
    generation_id: Mapped[str | None] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CloudSettlement(Base):
    __tablename__ = "cloud_settlement"
    __table_args__ = (
        CheckConstraint("actual >= 0 AND actual < 1000000000000000000", name="ck_cloud_actual"),
    )
    attempt_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("cloud_attempt.id"), primary_key=True
    )
    actual: Mapped[Decimal] = mapped_column(Numeric(30, 12))
    generation_id: Mapped[str] = mapped_column(Text)
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
