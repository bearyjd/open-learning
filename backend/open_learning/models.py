from datetime import UTC, datetime
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
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
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ident)
    url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text, default="")
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
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=ident)
    url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
