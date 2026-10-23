"""Discovery feed subscriptions and bounded article metadata.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "feeds",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False, unique=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True)),
        sa.Column("etag", sa.Text()),
        sa.Column("last_modified", sa.Text()),
        sa.Column("last_error", sa.String(64)),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "discovered_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "feed_id", sa.String(36), sa.ForeignKey("feeds.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("snippet", sa.String(500), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="new"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("feed_id", "url", name="uq_discovered_feed_url"),
        sa.CheckConstraint(
            "status IN ('new', 'ingested', 'dismissed')", name="ck_discovered_status"
        ),
        sa.CheckConstraint("length(snippet) <= 500", name="ck_discovered_snippet"),
    )
    op.create_index("ix_discovered_items_feed_id", "discovered_items", ["feed_id"])
    op.create_index("ix_discovered_items_status", "discovered_items", ["status"])


def downgrade():
    op.drop_table("discovered_items")
    op.drop_table("feeds")
