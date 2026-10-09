"""Add bounded retry accounting to persisted runs."""

import sqlalchemy as sa
from alembic import op

revision = "002_run_attempts"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("runs", "attempts")
