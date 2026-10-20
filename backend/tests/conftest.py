import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from open_learning.ai import GroundedInsight, InsightBatch, TutorResult
from open_learning.config import Settings
from open_learning.fetching import FetchedDocument
from open_learning.main import create_app
from open_learning.models import Base

TOKEN = "0123456789abcdef0123456789abcdef"
PASSAGE = "Retrieval practice strengthens memory by asking learners to recall ideas before rereading them. "
ARTICLE = PASSAGE * 10


class FixtureModel:
    def insights(self, chunks):
        return InsightBatch(
            insights=[
                GroundedInsight(
                    title="Retrieval practice",
                    idea="Recall before rereading.",
                    explanation="Trying to recall strengthens memory.",
                    why_it_matters="Study more effectively.",
                    evidence=PASSAGE.strip(),
                    chunk_index=0,
                    tags=["learning"],
                )
            ]
        )

    def tutor(self, passage, title, message, history):
        return TutorResult(
            answer="Recall the idea before rereading. "
            + ("That matches the source." if message else ""),
            question="What does retrieval practice ask learners to do?"
            if message
            else "How does retrieval practice support memory?",
            evidence=PASSAGE.strip(),
        )


@pytest.fixture
def settings():
    return Settings(api_token=TOKEN)


@pytest.fixture
def factory(tmp_path):
    url = os.environ.get("OL_TEST_DATABASE_URL", "sqlite:///" + str(tmp_path / "test.db"))
    engine = create_engine(url)
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
    Base.metadata.create_all(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def client(factory, settings):
    with TestClient(create_app(settings, factory, FixtureModel())) as client:
        client.headers["Authorization"] = "Bearer " + TOKEN
        yield client


def article_fetcher(url, settings):
    return FetchedDocument(url, "Evidence for retrieval", ARTICLE)
