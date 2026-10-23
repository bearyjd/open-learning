# SPDX-License-Identifier: GPL-3.0-only
"""Explicit pinned topics. Derived profiles are never persisted."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pinned_topics",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("term", sa.String(80), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(term) >= 3 AND length(term) <= 80", name="ck_pinned_topic_length"
        ),
    )


def downgrade():
    op.drop_table("pinned_topics")
