"""Alembic runs as a subprocess against a private database, never the conftest one."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

BACKEND = Path(__file__).resolve().parents[1]
PG_URL = os.environ.get("OL_TEST_MIGRATION_DATABASE_URL")
NOW = "2026-01-01T00:00:00+00:00"


@pytest.fixture(params=["sqlite", "postgresql"])
def database_url(request, tmp_path):
    if request.param == "sqlite":
        yield "sqlite:///" + str(tmp_path / "migrations.db")
        return
    if not PG_URL:
        pytest.skip("OL_TEST_MIGRATION_DATABASE_URL is not set")
    assert PG_URL != os.environ.get("OL_TEST_DATABASE_URL"), "use a separate database"
    reset_postgres(PG_URL)
    yield PG_URL
    reset_postgres(PG_URL)


def reset_postgres(url):
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.exec_driver_sql("DROP SCHEMA public CASCADE")
        connection.exec_driver_sql("CREATE SCHEMA public")
    engine.dispose()


def alembic(url, *args):
    env = {**os.environ, "OL_DATABASE_URL": url, "OL_API_TOKEN": "0123456789abcdef0123456789abcdef"}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def ok(url, *args):
    result = alembic(url, *args)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def run(engine, sql, **params):
    with engine.begin() as connection:
        return connection.execute(text(sql), params)


def seed_url_rows(engine):
    run(engine, "INSERT INTO sources (id, url, title, created_at) VALUES "
        "('s1', 'https://a.example/x', 'T', :n)", n=NOW)
    run(engine, "INSERT INTO jobs (id, url, status, attempts, created_at) VALUES "
        "('j1', 'https://a.example/x', 'completed', 1, :n)", n=NOW)


def seed_note_rows(engine):
    run(engine, "INSERT INTO sources (id, url, title, kind, label, created_at) VALUES "
        "('s2', 'note:abc', 'Note', 'note', 'lbl', :n)", n=NOW)
    jobs = (
        ("jq", "queued", "'t', 'body', NULL"),
        ("jf", "failed", "'t', 'body', 'l'"),
        ("jc", "completed", "NULL, NULL, NULL"),
    )
    for job_id, status, payload in jobs:
        run(engine, "INSERT INTO jobs (id, kind, url, status, attempts, payload_title, "
            "payload_text, payload_label, created_at) VALUES "
            f"('{job_id}', 'note', NULL, '{status}', 0, {payload}, :n)", n=NOW)


def test_upgrade_downgrade_roundtrip(database_url):
    engine = create_engine(database_url)
    ok(database_url, "upgrade", "0002")
    seed_url_rows(engine)
    ok(database_url, "upgrade", "head")
    assert run(engine, "SELECT kind, label FROM sources WHERE id = 's1'").one() == ("url", None)
    assert run(engine, "SELECT kind FROM jobs WHERE id = 'j1'").scalar() == "url"
    seed_note_rows(engine)

    refused = alembic(database_url, "downgrade", "0002")
    assert refused.returncode != 0
    assert "note jobs" in refused.stdout + refused.stderr
    # PostgreSQL rolls the whole run back (still 0004); SQLite keeps the completed 0004 downgrade.
    assert ok(database_url, "current").stdout.split()[0] in ("0003", "0004")
    assert run(engine, "SELECT COUNT(*) FROM jobs WHERE kind = 'note'").scalar() == 3

    run(engine, "UPDATE jobs SET status = 'failed' WHERE id = 'jq'")
    ok(database_url, "downgrade", "0002")
    columns = {column["name"] for column in inspect(engine).get_columns("jobs")}
    assert columns.isdisjoint({"kind", "payload_text", "payload_title", "payload_label"})
    assert run(engine, "SELECT url FROM jobs WHERE id = 'jf'").scalar() == "note:jf"
    assert run(engine, "SELECT url FROM sources WHERE id = 's2'").scalar() == "note:abc"

    ok(database_url, "upgrade", "head")
    assert run(engine, "SELECT kind FROM sources WHERE id = 's2'").scalar() == "note"
    assert run(engine, "SELECT kind, url FROM jobs WHERE id = 'jf'").one() == ("note", None)
    assert run(engine, "SELECT payload_text FROM jobs WHERE id = 'jf'").scalar() is None
    ok(database_url, "check")
    engine.dispose()


def test_constraints_and_indexes_survive_rebuild(database_url):
    engine = create_engine(database_url)
    ok(database_url, "upgrade", "head")
    inspector = inspect(engine)
    assert "ix_jobs_status" in {index["name"] for index in inspector.get_indexes("jobs")}
    with pytest.raises(IntegrityError):
        for source_id in ("a", "b"):
            run(engine, "INSERT INTO sources (id, url, title, kind, created_at) VALUES "
                f"('{source_id}', 'https://dup.example/', 't', 'url', :n)", n=NOW)
    bad_rows = (
        "INSERT INTO sources (id, url, title, kind, created_at) VALUES ('x', 'u', 't', 'bogus', :n)",
        "INSERT INTO sources (id, url, title, kind, created_at) VALUES ('x', 'note:k', 't', 'url', :n)",
        "INSERT INTO sources (id, url, title, kind, created_at) VALUES ('x', 'http://k', 't', 'note', :n)",
        "INSERT INTO jobs (id, kind, url, status, attempts, created_at) VALUES ('x', 'url', NULL, 'queued', 0, :n)",
        "INSERT INTO jobs (id, kind, url, status, attempts, created_at) VALUES ('x', 'note', 'http://k', 'queued', 0, :n)",
        "INSERT INTO jobs (id, kind, url, status, attempts, payload_text, created_at) VALUES ('x', 'url', 'http://k', 'queued', 0, 'p', :n)",
    )
    for sql in bad_rows:
        with pytest.raises(IntegrityError):
            run(engine, sql, n=NOW)
    ok(database_url, "check")
    engine.dispose()


def test_0004_job_document_link_roundtrip(database_url):
    engine = create_engine(database_url)
    ok(database_url, "upgrade", "0003")
    seed_note_rows(engine)
    ok(database_url, "upgrade", "head")
    columns = {column["name"] for column in inspect(engine).get_columns("jobs")}
    assert {"document_id", "replaces_document_id"} <= columns
    assert run(engine, "SELECT document_id, replaces_document_id FROM jobs WHERE id = 'jc'").one() == (None, None)
    run(engine, "UPDATE jobs SET document_id = 'doc-1', replaces_document_id = 'doc-0' WHERE id = 'jc'")
    ok(database_url, "check")

    ok(database_url, "downgrade", "0003")
    columns = {column["name"] for column in inspect(engine).get_columns("jobs")}
    assert columns.isdisjoint({"document_id", "replaces_document_id"})
    assert run(engine, "SELECT COUNT(*) FROM jobs WHERE kind = 'note'").scalar() == 3
    assert "ix_jobs_status" in {index["name"] for index in inspect(engine).get_indexes("jobs")}

    ok(database_url, "upgrade", "head")
    assert run(engine, "SELECT document_id FROM jobs WHERE id = 'jc'").scalar() is None
    ok(database_url, "check")
    engine.dispose()
