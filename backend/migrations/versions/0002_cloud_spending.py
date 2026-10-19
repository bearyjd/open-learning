"""durable cloud spending ledger

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    amount = sa.Numeric(30, 12)
    op.create_table(
        "cloud_budget",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lifetime_limit", amount, nullable=True),
        sa.Column("spent", amount, nullable=False),
        sa.Column("held", amount, nullable=False),
        sa.Column("debt", amount, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_cloud_budget_singleton"),
        sa.CheckConstraint(
            "lifetime_limit IS NULL OR (lifetime_limit > 0 AND lifetime_limit < 1000000000000000000)",
            name="ck_cloud_limit",
        ),
        sa.CheckConstraint(
            "spent >= 0 AND spent < 1000000000000000000 AND held >= 0 AND held < 1000000000000000000 AND debt >= 0 AND debt < 1000000000000000000",
            name="ck_cloud_balances",
        ),
    )
    op.execute(
        "INSERT INTO cloud_budget (id, lifetime_limit, spent, held, debt, created_at, updated_at) "
        "VALUES (1, NULL, 0, 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    op.create_table(
        "cloud_attempt",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("budget_id", sa.Integer(), sa.ForeignKey("cloud_budget.id"), nullable=False),
        sa.Column("key_sha256", sa.String(64), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("provider_tag", sa.Text(), nullable=False),
        sa.Column("provider_name", sa.Text(), nullable=False),
        sa.Column("provider_name_unique", sa.Boolean(), nullable=False),
        sa.Column("context_length", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("prompt_rate", amount, nullable=False),
        sa.Column("completion_rate", amount, nullable=False),
        sa.Column("request_fee_cap", amount, nullable=False),
        sa.Column("reserved", amount, nullable=False),
        sa.Column("metadata_sha256", sa.String(64), nullable=False),
        sa.Column("metadata_checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_bytes", sa.Integer(), nullable=False),
        sa.Column("generation_id", sa.Text(), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "reserved >= 0 AND reserved < 1000000000000000000 AND prompt_rate >= 0 AND prompt_rate < 1000000000000000000 AND completion_rate >= 0 AND completion_rate < 1000000000000000000 AND request_fee_cap >= 0 AND request_fee_cap < 1000000000000000000",
            name="ck_cloud_attempt_amounts",
        ),
    )
    op.create_table(
        "cloud_settlement",
        sa.Column(
            "attempt_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("cloud_attempt.id"),
            primary_key=True,
        ),
        sa.Column("actual", amount, nullable=False),
        sa.Column("generation_id", sa.Text(), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("actual >= 0 AND actual < 1000000000000000000", name="ck_cloud_actual"),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
            CREATE FUNCTION cloud_budget_guard() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'cloud budget cannot be deleted';
                END IF;
                IF OLD.lifetime_limit IS NOT NULL AND
                   NEW.lifetime_limit IS DISTINCT FROM OLD.lifetime_limit THEN
                    RAISE EXCEPTION 'cloud lifetime limit is immutable';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """)
        op.execute("""
            CREATE TRIGGER cloud_budget_guard BEFORE UPDATE OR DELETE
            ON cloud_budget FOR EACH ROW EXECUTE FUNCTION cloud_budget_guard()
        """)
        op.execute("""
            CREATE FUNCTION cloud_attempt_immutable() RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'cloud attempt cannot be deleted';
                END IF;
                IF to_jsonb(NEW) - 'generation_id' IS DISTINCT FROM
                   to_jsonb(OLD) - 'generation_id' OR
                   (OLD.generation_id IS NOT NULL AND NEW.generation_id IS DISTINCT FROM OLD.generation_id)
                THEN
                    RAISE EXCEPTION 'cloud attempt is immutable';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """)
        op.execute("""
            CREATE TRIGGER cloud_attempt_no_mutation BEFORE UPDATE OR DELETE
            ON cloud_attempt FOR EACH ROW EXECUTE FUNCTION cloud_attempt_immutable()
        """)
        op.execute("""
            CREATE FUNCTION cloud_settlement_append_only() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'cloud settlement is append only';
            END;
            $$ LANGUAGE plpgsql
        """)
        op.execute("""
            CREATE TRIGGER cloud_settlement_no_mutation BEFORE UPDATE OR DELETE
            ON cloud_settlement FOR EACH ROW EXECUTE FUNCTION cloud_settlement_append_only()
        """)


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS cloud_settlement_no_mutation ON cloud_settlement")
        op.execute("DROP FUNCTION IF EXISTS cloud_settlement_append_only()")
        op.execute("DROP TRIGGER IF EXISTS cloud_attempt_no_mutation ON cloud_attempt")
        op.execute("DROP FUNCTION IF EXISTS cloud_attempt_immutable()")
        op.execute("DROP TRIGGER IF EXISTS cloud_budget_guard ON cloud_budget")
        op.execute("DROP FUNCTION IF EXISTS cloud_budget_guard()")
    op.drop_table("cloud_settlement")
    op.drop_table("cloud_attempt")
    op.drop_table("cloud_budget")
