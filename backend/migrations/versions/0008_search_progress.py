"""Visible search progress and durable employer discovery jobs."""
from alembic import op
import sqlalchemy as sa

revision = "0008_search_progress"
down_revision = "0007_cloud_storage_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("saved_searches", sa.Column("progress", sa.JSON(), nullable=False, server_default="{}"))
    op.create_table("company_discoveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("country", sa.String(80), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("progress", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_table("company_discoveries")
    op.drop_column("saved_searches", "progress")
