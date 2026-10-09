"""Store the source pull-request number for publisher follow-up actions."""

import sqlalchemy as sa
from alembic import op

revision = "003_source_pr_metadata"
down_revision = "002_run_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("source_pr_number", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "source_pr_number")
