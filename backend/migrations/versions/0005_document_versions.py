"""note document versions

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    # Existing documents become version 1, current. replaces_document_id has no foreign key
    # (like jobs.replaces_document_id): any version can be forgotten on its own.
    op.add_column(
        "documents", sa.Column("version", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column("documents", sa.Column("replaces_document_id", sa.String(36), nullable=True))
    # Persisted, not derived from "has a successor": forgetting the latest version must not
    # make an older one visible again.
    op.add_column(
        "documents",
        sa.Column("superseded", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Block writes so no edit job or superseded version appears between check and drop.
        bind.execute(sa.text("LOCK TABLE documents, jobs IN SHARE ROW EXCLUSIVE MODE"))
    superseded = bind.execute(
        sa.text("SELECT COUNT(*) FROM documents WHERE superseded = :yes"), {"yes": True}
    ).scalar()
    if superseded:
        # Older code would show hidden versions again in the feed, library and search.
        raise RuntimeError("Forget superseded note versions before downgrading below 0005")
    pending = bind.execute(sa.text(
        "SELECT COUNT(*) FROM jobs WHERE kind = 'note' AND replaces_document_id IS NOT NULL "
        "AND status IN ('queued', 'running')"
    )).scalar()
    if pending:
        # Older code would import them as separate notes beside the version they replace.
        raise RuntimeError("Finish or fail queued or running note edit jobs before downgrading below 0005")
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("superseded")
        batch.drop_column("replaces_document_id")
        batch.drop_column("version")
