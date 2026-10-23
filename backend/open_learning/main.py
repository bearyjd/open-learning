import secrets
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select, text

from .ai import ModelClient, ModelError
from .config import Settings, get_settings
from .database import session_factory
from .fetching import FetchError, validate_url
from .forget import (
    edit_note,
    forget_note,
    forget_note_job,
    list_note_jobs,
    list_notes,
    note_capacity_check,
    purge_note_payloads,
)
from .limits import BodyLimitMiddleware
from .models import History, Insight, Interaction, Job, Preference, ReviewItem, SavedItem, now
from .notes import (
    MAX_LABEL_CHARS,
    MAX_TITLE_CHARS,
    MIN_TEXT_CHARS,
    validate_note,
    validate_note_edit,
)
from .schemas import (
    InsightOutput,
    JobListItem,
    JobOutput,
    LimitsOutput,
    NoteOutput,
    NoteTextOutput,
    ReviewOutput,
    TutorOutput,
)
from .services import (
    RowVanished,
    answer_review,
    ensure_review,
    serialize_insight,
    serialize_review,
    today_feed,
    tutor_chat,
)
from .versions import NO_STORE, check_replaceable, current_documents, note_text

TEXT_INGEST_PATH = "/api/v1/ingest/text"
# Largest legitimate non-note body is a tutor message (4000 chars, <= ~24 KiB JSON-escaped).
DEFAULT_BODY_LIMIT = 64 * 1024
UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


def create_app(settings: Settings | None = None, factory=None, model=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application):
        application.state.settings = settings or get_settings()  # Fail closed during startup.
        application.state.factory = factory or session_factory(
            application.state.settings.database_url
        )
        application.state.model = model or ModelClient(
            application.state.settings, application.state.factory
        )
        yield

    app = FastAPI(
        title="Open Learning",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def body_limit(scope) -> int:
        # Large cap only for note ingest (max-length note in worst-case JSON escaping, plus
        # slack); every other route buffers at most DEFAULT_BODY_LIMIT before authentication.
        route_path = scope["path"].removeprefix(scope.get("root_path", ""))
        if scope["method"] == "POST" and route_path == TEXT_INGEST_PATH:
            return 12 * scope["app"].state.settings.max_document_chars + 64 * 1024
        return DEFAULT_BODY_LIMIT

    app.add_middleware(BodyLimitMiddleware, limit=body_limit)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Never echo submitted request bodies back (notes carry private text).
        errors = [
            {k: v for k, v in e.items() if k not in {"input", "ctx"}} if e["loc"][:1] == ("body",) else e
            for e in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})

    bearer = HTTPBearer(auto_error=False)

    def authenticate(
        request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)
    ):
        if credentials is None or not secrets.compare_digest(
            credentials.credentials.encode(), request.app.state.settings.api_token.encode()
        ):
            raise HTTPException(401, "Invalid bearer token", headers={"WWW-Authenticate": "Bearer"})

    def database(request: Request):
        with request.app.state.factory() as db:
            yield db

    def find_insight(db, insight_id, lock=False):
        query = select(Insight).where(Insight.id == insight_id)
        item = db.scalar(query.with_for_update() if lock else query)
        if item is None:
            raise HTTPException(404, "Insight not found")
        return item

    def visible(db, items, serialize):
        """Serialize rows, skipping any deleted while the request was running."""
        out = []
        for item in items:
            try:
                out.append(serialize(db, item))
            except RowVanished:
                continue
        return out

    def serialized_or_404(db, item):
        try:
            return serialize_insight(db, item)
        except RowVanished as error:
            raise HTTPException(404, "Not found") from error

    api = APIRouter(prefix="/api/v1", dependencies=[Depends(authenticate)])

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @api.get("/health")
    def ready(db=Depends(database)):
        db.execute(text("SELECT 1"))
        return {"status": "ok"}

    @api.post("/ingest/url", status_code=202, response_model=JobOutput)
    def ingest(payload: UrlInput, db=Depends(database)):
        try:
            url = validate_url(payload.url)
        except FetchError as error:
            raise HTTPException(422, str(error)) from error
        job = Job(url=url)
        db.add(job)
        db.commit()
        return job_output(job)

    @api.post("/ingest/text", status_code=202, response_model=JobOutput)
    def ingest_text(payload: TextInput, request: Request, db=Depends(database)):
        settings = request.app.state.settings
        note = validate_note(payload.title, payload.text, payload.label, settings.max_document_chars)
        replaces = payload.replaces_document_id and payload.replaces_document_id.lower()
        if replaces is not None:
            check_replaceable(db, replaces, note.text)
        purge_note_payloads(db, settings)
        db.commit()
        note_capacity_check(db, settings)
        job = Job(
            kind="note",
            url=None,
            payload_title=note.title,
            payload_text=note.text,
            payload_label=note.label,
            replaces_document_id=replaces,
        )
        db.add(job)
        db.commit()
        return job_output(job)

    @api.get("/jobs/{job_id}", response_model=JobOutput)
    def job_status(job_id: str, db=Depends(database)):
        item = db.get(Job, job_id)
        if item is None:
            raise HTTPException(404, "Job not found")
        return job_output(item)

    @api.post("/jobs/{job_id}/retry", response_model=JobOutput)
    def retry(job_id: str, request: Request, db=Depends(database)):
        item = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if item is None:
            raise HTTPException(404, "Job not found")
        if item.status != "failed":
            raise HTTPException(409, "Only failed jobs can be retried")
        if item.kind == "note" and item.payload_text is None:
            raise HTTPException(409, "Note text is no longer available; submit it again")
        if item.kind == "note":
            note_capacity_check(db, request.app.state.settings)
        item.status, item.error = "queued", None
        db.commit()
        return job_output(item)

    @api.delete("/jobs/{job_id}", status_code=204)
    def delete_job(job_id: str, db=Depends(database)):
        forget_note_job(db, job_id)

    @api.get("/jobs", response_model=list[JobListItem])
    def jobs(
        kind: Literal["note"],
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        db=Depends(database),
    ):
        return list_note_jobs(db, limit, offset)

    @api.get("/notes", response_model=list[NoteOutput])
    def notes(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        include_superseded: bool = False,
        db=Depends(database),
    ):
        return list_notes(db, limit, offset, include_superseded)

    @api.get("/notes/{document_id}/text", response_model=NoteTextOutput)
    def get_note_text(document_id: str, response: Response, db=Depends(database)):
        result = note_text(db, document_id)
        response.headers.update(NO_STORE)
        return result

    @api.patch("/notes/{document_id}", response_model=NoteOutput)
    def patch_note(document_id: str, payload: NotePatch, db=Depends(database)):
        changes = validate_note_edit(payload.title, payload.label, payload.model_fields_set)
        return edit_note(db, document_id, changes)

    @api.get("/limits", response_model=LimitsOutput)
    def limits(request: Request):
        return {
            "max_document_chars": request.app.state.settings.max_document_chars,
            "min_text_chars": MIN_TEXT_CHARS,
            "max_title_chars": MAX_TITLE_CHARS,
            "max_label_chars": MAX_LABEL_CHARS,
        }

    @api.delete("/notes/{document_id}", status_code=204)
    def delete_note(document_id: str, db=Depends(database)):
        forget_note(db, document_id)

    @api.get("/feed/today", response_model=list[InsightOutput])
    def feed(db=Depends(database)):
        return today_feed(db)

    @api.get("/library", response_model=list[InsightOutput])
    def library(db=Depends(database)):
        items = db.scalars(
            select(Insight)
            .join(SavedItem)
            .where(Insight.document_id.in_(current_documents()))
            .order_by(SavedItem.created_at.desc())
            .limit(500)
        )
        return visible(db, items, serialize_insight)

    @api.get("/insights/{insight_id}", response_model=InsightOutput)
    def detail(insight_id: str, db=Depends(database)):
        return serialized_or_404(db, find_insight(db, insight_id))

    @api.post("/insights/{insight_id}/save", response_model=InsightOutput)
    def save(insight_id: str, db=Depends(database)):
        item = find_insight(db, insight_id, lock=True)
        if db.get(SavedItem, item.id) is None:
            db.add(SavedItem(insight_id=item.id))
            db.add(Interaction(insight_id=item.id, action="save"))
        try:
            ensure_review(db, item)
        except RowVanished as error:
            raise HTTPException(404, "Insight not found") from error
        db.commit()
        return serialized_or_404(db, item)

    @api.post("/insights/{insight_id}/feedback", status_code=204)
    def feedback(insight_id: str, payload: FeedbackInput, db=Depends(database)):
        item = find_insight(db, insight_id, lock=True)
        item.dismissed = True
        db.add(Interaction(insight_id=item.id, action=payload.action))
        db.commit()

    @api.get("/search", response_model=list[InsightOutput])
    def search(q: str = Query(min_length=1, max_length=200), db=Depends(database)):
        term = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        items = db.scalars(
            select(Insight)
            .join(SavedItem)
            .where(
                Insight.document_id.in_(current_documents()),
                or_(
                    Insight.title.ilike(term, escape="\\"),
                    Insight.idea.ilike(term, escape="\\"),
                    Insight.explanation.ilike(term, escape="\\"),
                ),
            )
            .order_by(Insight.created_at.desc())
            .limit(100)
        )
        return visible(db, items, serialize_insight)

    @api.post("/tutor/chat", response_model=TutorOutput)
    def tutor(payload: TutorInput, request: Request, db=Depends(database)):
        try:
            return tutor_chat(
                db, find_insight(db, payload.insight_id), payload.message, request.app.state.model
            )
        except ModelError as error:
            raise HTTPException(503, str(error)) from error
        except RowVanished as error:
            raise HTTPException(404, "Insight not found") from error

    @api.get("/reviews/due", response_model=list[ReviewOutput])
    def due(db=Depends(database)):
        items = db.scalars(
            select(ReviewItem)
            .where(
                ReviewItem.due_at <= now(),
                ReviewItem.insight_id.in_(
                    select(Insight.id).where(Insight.document_id.in_(current_documents()))
                ),
            )
            .order_by(ReviewItem.due_at)
            .limit(100)
        )
        return visible(db, items, serialize_review)

    @api.post("/reviews/{review_id}/answer", response_model=ReviewOutput)
    def review_answer(review_id: str, payload: AnswerInput, db=Depends(database)):
        # Lock order is insight, then review item (see forget.py); a delete holds them in that order.
        insight_id = db.scalar(select(ReviewItem.insight_id).where(ReviewItem.id == review_id))
        if insight_id is None:
            raise HTTPException(404, "Review not found")
        if db.scalar(select(Insight.id).where(Insight.id == insight_id).with_for_update()) is None:
            raise HTTPException(404, "Review not found")
        item = db.scalar(select(ReviewItem).where(ReviewItem.id == review_id).with_for_update())
        if item is None:
            raise HTTPException(404, "Review not found")
        try:
            return answer_review(db, item, payload.rating)
        except RowVanished as error:
            raise HTTPException(404, "Review not found") from error

    @api.get("/preferences")
    def preferences(db=Depends(database)):
        return {item.key: item.value for item in db.scalars(select(Preference))}

    @api.put("/preferences")
    def set_preferences(payload: PreferencesInput, db=Depends(database)):
        for key, value in payload.model_dump().items():
            db.merge(Preference(key=key, value={"value": value}))
        db.commit()
        return payload

    @api.get("/history")
    def history(db=Depends(database)):
        return [
            {
                "insight_id": item.insight_id,
                "role": item.role,
                "message": item.message,
                "created_at": item.created_at.isoformat(),
            }
            for item in db.scalars(select(History).order_by(History.created_at.desc()).limit(200))
        ]

    app.include_router(api)
    return app


class UrlInput(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class TextInput(BaseModel):
    # No length limits here: a pydantic length error would echo the whole text. See notes.py.
    model_config = ConfigDict(extra="forbid")
    title: str
    text: str
    label: str | None = None
    # Set to edit a note: the job publishes a new version of this (current) note document.
    replaces_document_id: str | None = Field(default=None, pattern=UUID_PATTERN)


class NotePatch(BaseModel):
    # Unbounded types for the same reason as TextInput; validation lives in notes.py.
    model_config = ConfigDict(extra="forbid")
    title: str | None = None
    label: str | None = None


class FeedbackInput(BaseModel):
    action: Literal["dismiss"]


class TutorInput(BaseModel):
    insight_id: str
    message: str = Field(default="", max_length=4000)


class AnswerInput(BaseModel):
    rating: Literal["forgotten", "difficult", "remembered", "easy"]


class PreferencesInput(BaseModel):
    interests: list[str] = Field(default_factory=list, max_length=20)
    daily_limit: int = Field(default=10, ge=1, le=20)


def job_output(item: Job):
    return {
        "id": item.id,
        "status": item.status,
        "error": item.error,
        "document_id": item.document_id,
        "replaces_document_id": item.replaces_document_id,
    }


app = create_app()
