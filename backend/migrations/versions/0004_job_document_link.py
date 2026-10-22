"""link note jobs to the document they produced

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    # Plain nullable ids, deliberately without foreign keys: forgetting a note deletes the
    # document and its linked jobs together, and pre-migration jobs stay unlinked (NULL).
    op.add_column("jobs", sa.Column("document_id", sa.String(36), nullable=True))
    op.add_column("jobs", sa.Column("replaces_document_id", sa.String(36), nullable=True))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Same lock as 0003 so no job write races the column drop.
        bind.execute(sa.text("LOCK TABLE jobs IN SHARE ROW EXCLUSIVE MODE"))
    # Dropping the columns discards the job-to-document links; jobs and notes are unaffected.
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("replaces_document_id")
        batch.drop_column("document_id")
