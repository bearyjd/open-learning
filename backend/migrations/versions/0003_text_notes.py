"""text note sources and jobs

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("sources", sa.Column("kind", sa.String(16), nullable=False, server_default="url"))
    op.add_column("sources", sa.Column("label", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("kind", sa.String(16), nullable=False, server_default="url"))
    op.add_column("jobs", sa.Column("payload_title", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("payload_text", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("payload_label", sa.Text(), nullable=True))
    with op.batch_alter_table("jobs") as batch:
        batch.alter_column("url", existing_type=sa.Text(), nullable=True)
    # Rows left by a previous 0003 downgrade carry synthetic note keys; restore their kind.
    op.execute("UPDATE sources SET kind = 'note' WHERE url LIKE 'note:%'")
    op.execute("UPDATE jobs SET kind = 'note', url = NULL WHERE url LIKE 'note:%'")
    with op.batch_alter_table("sources") as batch:
        batch.create_check_constraint("ck_sources_kind", "kind IN ('url', 'note')")
        batch.create_check_constraint("ck_sources_note_key", "(kind = 'note') = (url LIKE 'note:%')")
    with op.batch_alter_table("jobs") as batch:
        batch.create_check_constraint(
            "ck_jobs_kind_payload",
            "(kind = 'url' AND url IS NOT NULL AND payload_text IS NULL AND payload_title IS NULL "
            "AND payload_label IS NULL) OR (kind = 'note' AND url IS NULL)",
        )


def downgrade():
    bind = op.get_bind()
    pending = bind.execute(sa.text(
        "SELECT COUNT(*) FROM jobs WHERE kind = 'note' AND status IN ('queued', 'running')"
    )).scalar()
    if pending:
        raise RuntimeError("Finish or fail queued or running note jobs before downgrading below 0003")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("ck_jobs_kind_payload", type_="check")
    op.execute("UPDATE jobs SET url = 'note:' || id, payload_text = NULL, payload_title = NULL, payload_label = NULL WHERE kind = 'note'")
    with op.batch_alter_table("jobs") as batch:
        batch.alter_column("url", existing_type=sa.Text(), nullable=False)
        batch.drop_column("payload_label")
        batch.drop_column("payload_text")
        batch.drop_column("payload_title")
        batch.drop_column("kind")
    with op.batch_alter_table("sources") as batch:
        batch.drop_constraint("ck_sources_note_key", type_="check")
        batch.drop_constraint("ck_sources_kind", type_="check")
        batch.drop_column("label")
        batch.drop_column("kind")
