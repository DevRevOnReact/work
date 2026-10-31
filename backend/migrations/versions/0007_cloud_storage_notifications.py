"""Durable CV uploads and search notifications for ephemeral hosting."""
from alembic import op
import sqlalchemy as sa

revision = "0007_cloud_storage_notifications"
down_revision = "0006_companies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("notification_seen", sa.Column("id", sa.String(64), primary_key=True))
    op.create_table("resume_files",
        sa.Column("resume_id", sa.String(36), sa.ForeignKey("resumes.id"), primary_key=True),
        sa.Column("content", sa.LargeBinary(), nullable=False))
    op.create_table("search_notifications",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("search_id", sa.String(36), sa.ForeignKey("saved_searches.id"), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))


def downgrade() -> None:
    op.drop_table("search_notifications")
    op.drop_table("resume_files")
    op.drop_table("notification_seen")
