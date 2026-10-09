"""Create the DocAgent persistence tables."""

import sqlalchemy as sa
from alembic import op

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("runs"):
        op.create_table(
            "runs",
            sa.Column("idempotency_key", sa.String(512), primary_key=True),
            sa.Column("delivery_id", sa.String(512), nullable=False),
            sa.Column("repo", sa.String(512), nullable=False),
            sa.Column("head_sha", sa.String(256), nullable=False),
            sa.Column("event_type", sa.String(128), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("source_url", sa.Text(), nullable=False, server_default=""),
            sa.Column("base_sha", sa.String(256), nullable=False, server_default=""),
            sa.Column("base_branch", sa.String(256), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    if not inspector.has_table("audit_events"):
        op.create_table(
            "audit_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("idempotency_key", sa.String(512), nullable=False),
            sa.Column("event_type", sa.String(128), nullable=False),
            sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    if not inspector.has_table("run_reports"):
        op.create_table(
            "run_reports",
            sa.Column("idempotency_key", sa.String(512), primary_key=True),
            sa.Column("report_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    if not inspector.has_table("dead_letters"):
        op.create_table(
            "dead_letters",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("idempotency_key", sa.String(512), nullable=False),
            sa.Column("error", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    if not inspector.has_table("control_flags"):
        op.create_table(
            "control_flags",
            sa.Column("name", sa.String(128), primary_key=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )


def downgrade() -> None:
    op.drop_table("control_flags")
    op.drop_table("dead_letters")
    op.drop_table("run_reports")
    op.drop_table("audit_events")
    op.drop_table("runs")
