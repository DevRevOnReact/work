"""Manually curated LinkedIn referral leads."""
from alembic import op
import sqlalchemy as sa

revision = "0005_linkedin_leads"
down_revision = "0004_saved_searches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "referral_leads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("company", sa.String(200), nullable=False),
        sa.Column("location", sa.String(200), nullable=False, server_default=""),
        sa.Column("profile_url", sa.Text(), nullable=False, unique=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("referral_leads")
