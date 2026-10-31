"""Saved, independently scored job searches."""
from alembic import op
import sqlalchemy as sa

revision = "0004_saved_searches"
down_revision = "0003_explicit_autopilot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The streamlined product no longer schedules or submits applications.
    op.execute("UPDATE control SET enabled = false, auto_apply = false")
    op.create_table(
        "saved_searches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("resume_id", sa.String(36), sa.ForeignKey("resumes.id"), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("saved_searches")
