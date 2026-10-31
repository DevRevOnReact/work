"""Public LinkedIn research inbox; no account credentials or mail access."""
from alembic import op
import sqlalchemy as sa

revision = "0010_linkedin_candidates"
down_revision = "0009_top_vacancies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("linkedin_candidates",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)))
    op.create_index("ix_linkedin_candidates_status", "linkedin_candidates", ["status"])


def downgrade() -> None:
    op.drop_table("linkedin_candidates")
