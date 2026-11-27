"""Personal assistant requests, results and recoverable worker leases."""
import sqlalchemy as sa
from alembic import op

revision = "0014_reply_drafts"
down_revision = "0013_vacancy_identity_favorites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("reply_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease", sa.String(36)),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_reply_drafts_status", "reply_drafts", ["status"])
    op.create_index("ix_reply_drafts_created_at", "reply_drafts", ["created_at"])


def downgrade() -> None:
    op.drop_table("reply_drafts")
