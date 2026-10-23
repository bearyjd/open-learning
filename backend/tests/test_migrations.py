"""Alembic runs as a subprocess against a private database, never the conftest one."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DataError, IntegrityError

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


def test_0007_pinned_topics_roundtrip(database_url):
    engine = create_engine(database_url)
    ok(database_url, "upgrade", "head")
    assert "pinned_topics" in inspect(engine).get_table_names()
    run(engine, "INSERT INTO pinned_topics (id,term,created_at) VALUES ('p1','orchid',:n)", n=NOW)
    for ident, term in (("p2", "orchid"), ("p3", ""), ("p4", "x" * 81)):
        with pytest.raises((IntegrityError, DataError)):
            run(engine, "INSERT INTO pinned_topics (id,term,created_at) VALUES (:i,:t,:n)",
                i=ident, t=term, n=NOW)
    ok(database_url, "check")
    ok(database_url, "downgrade", "0006")
    assert "pinned_topics" not in inspect(engine).get_table_names()
    assert {"feeds", "discovered_items"} <= set(inspect(engine).get_table_names())
    ok(database_url, "upgrade", "head")
    engine.dispose()
    assert run(engine, "SELECT count(*) FROM pinned_topics").scalar() == 0
    ok(database_url, "check")
    engine.dispose()


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
    # PostgreSQL rolls the whole run back (still head); SQLite keeps the completed 0004 downgrade.
    expected = "0007" if database_url.startswith("postgresql") else "0003"
    assert ok(database_url, "current").stdout.split()[0] == expected
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
    names = {index["name"] for index in inspect(engine).get_indexes("jobs")}
    assert {"ix_jobs_status", "ix_jobs_document_id"} <= names
    assert run(engine, "SELECT document_id, replaces_document_id FROM jobs WHERE id = 'jc'").one() == (None, None)
    run(engine, "UPDATE jobs SET document_id = 'doc-1', replaces_document_id = 'doc-0' WHERE id = 'jc'")
    ok(database_url, "check")

    ok(database_url, "downgrade", "0003")
    columns = {column["name"] for column in inspect(engine).get_columns("jobs")}
    assert columns.isdisjoint({"document_id", "replaces_document_id"})
    assert run(engine, "SELECT COUNT(*) FROM jobs WHERE kind = 'note'").scalar() == 3
    names = {index["name"] for index in inspect(engine).get_indexes("jobs")}
    assert "ix_jobs_status" in names and "ix_jobs_document_id" not in names

    ok(database_url, "upgrade", "head")
    assert "ix_jobs_document_id" in {index["name"] for index in inspect(engine).get_indexes("jobs")}
    assert run(engine, "SELECT document_id FROM jobs WHERE id = 'jc'").scalar() is None
    ok(database_url, "check")
    engine.dispose()


def seed_versioned_notes(engine):
    run(engine, "INSERT INTO sources (id, url, title, kind, created_at) VALUES "
        "('sv1', 'note:v1', 'N', 'note', :n), ('sv2', 'note:v2', 'N', 'note', :n)", n=NOW)
    run(engine, "INSERT INTO documents (id, source_id, title, text, content_hash, "
        "generation_coverage, extraction_truncated, created_at) VALUES "
        "('d1', 'sv1', 'N', 'one', 'v1', 'excerpt', false, :n)", n=NOW)


def test_0006_discovery_roundtrip(database_url):
    engine = create_engine(database_url)
    ok(database_url, "upgrade", "head")
    assert {"feeds", "discovered_items"} <= set(inspect(engine).get_table_names())
    indexes = {index["name"]: index["column_names"]
               for index in inspect(engine).get_indexes("discovered_items")}
    assert indexes["ix_discovered_items_status_created_at_id"] == ["status", "created_at", "id"]
    run(engine, "INSERT INTO feeds (id,url,title,created_at,consecutive_failures) VALUES "
        "('f1','https://example.com/feed','T',:n,0)", n=NOW)
    insert = "INSERT INTO discovered_items (id,feed_id,url,title,snippet,status,created_at) "
    run(engine, insert + "VALUES ('i1','f1','https://example.com/a','T','short','new',:n)", n=NOW)
    for values in (
        "('i2','f1','https://example.com/a','T','short','new',:n)",
        "('i3','f1','https://example.com/b','T','short','bogus',:n)",
        "('i4','f1','https://example.com/c','T',:snippet,'new',:n)",
    ):
        with pytest.raises((IntegrityError, DataError)):
            run(engine, insert + "VALUES " + values, n=NOW, snippet="a" * 501)
    ok(database_url, "check")
    # Cascading parent deletion is guaranteed by the DB when FK enforcement is on.
    with engine.begin() as connection:
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.execute(text("DELETE FROM feeds WHERE id='f1'"))
        assert connection.execute(text("SELECT COUNT(*) FROM discovered_items")).scalar() == 0
    ok(database_url, "downgrade", "0005")
    assert {"feeds", "discovered_items"}.isdisjoint(inspect(engine).get_table_names())
    ok(database_url, "upgrade", "head")
    # Refresh SQLite's pooled schema cache after the subprocess recreates tables.
    engine.dispose()
    indexes = {index["name"]: index["column_names"]
               for index in inspect(engine).get_indexes("discovered_items")}
    assert indexes["ix_discovered_items_status_created_at_id"] == ["status", "created_at", "id"]
    ok(database_url, "check")
    engine.dispose()


def test_0005_document_versions_roundtrip(database_url):
    engine = create_engine(database_url)
    ok(database_url, "upgrade", "0004")
    seed_versioned_notes(engine)
    ok(database_url, "upgrade", "head")
    columns = {column["name"] for column in inspect(engine).get_columns("documents")}
    assert {"version", "replaces_document_id", "superseded", "note_id"} <= columns
    row = run(engine, "SELECT version, replaces_document_id, superseded FROM documents WHERE id = 'd1'").one()
    assert (row[0], row[1], bool(row[2])) == (1, None, False)
    assert run(engine, "SELECT note_id FROM documents WHERE id = 'd1'").scalar() == "d1"
    assert "ix_documents_note_id" in {i["name"] for i in inspect(engine).get_indexes("documents")}
    assert "ix_jobs_replaces_document_id" in {i["name"] for i in inspect(engine).get_indexes("jobs")}
    ok(database_url, "check")

    run(engine, "INSERT INTO documents (id, source_id, title, text, content_hash, "
        "generation_coverage, extraction_truncated, created_at, version, replaces_document_id) "
        "VALUES ('d2', 'sv2', 'N', 'two', 'v2', 'excerpt', false, :n, 2, 'd1')", n=NOW)
    run(engine, "UPDATE documents SET superseded = true WHERE id = 'd1'")
    refused = alembic(database_url, "downgrade", "0004")
    assert refused.returncode != 0
    assert "superseded" in refused.stdout + refused.stderr
    expected = "0007" if database_url.startswith("postgresql") else "0005"
    assert ok(database_url, "current").stdout.split()[0] == expected

    run(engine, "DELETE FROM documents WHERE id = 'd1'")
    run(engine, "INSERT INTO jobs (id, kind, url, status, attempts, payload_title, payload_text, "
        "replaces_document_id, created_at) VALUES ('je', 'note', NULL, 'queued', 0, 't', 'b', 'd2', :n)",
        n=NOW)
    refused = alembic(database_url, "downgrade", "0004")
    assert refused.returncode != 0
    assert "edit jobs" in refused.stdout + refused.stderr

    run(engine, "UPDATE jobs SET status = 'failed' WHERE id = 'je'")
    refused = alembic(database_url, "downgrade", "0004")
    assert refused.returncode != 0  # Older code would retry it as a separate note.
    assert "failed note edit jobs" in refused.stdout + refused.stderr

    run(engine, "UPDATE jobs SET payload_title = NULL, payload_text = NULL WHERE id = 'je'")
    ok(database_url, "downgrade", "0004")
    columns = {column["name"] for column in inspect(engine).get_columns("documents")}
    assert columns.isdisjoint({"version", "replaces_document_id", "superseded", "note_id"})
    assert "ix_jobs_replaces_document_id" not in {i["name"] for i in inspect(engine).get_indexes("jobs")}
    assert run(engine, "SELECT COUNT(*) FROM documents").scalar() == 1
    ok(database_url, "upgrade", "head")
    assert run(engine, "SELECT version FROM documents WHERE id = 'd2'").scalar() == 1
    assert run(engine, "SELECT note_id FROM documents WHERE id = 'd2'").scalar() == "d2"
    ok(database_url, "check")
    engine.dispose()
