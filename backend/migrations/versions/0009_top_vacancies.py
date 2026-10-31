"""Three-day top vacancy history and delivery receipts."""
from alembic import op
import sqlalchemy as sa

revision = "0009_top_vacancies"
down_revision = "0008_search_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("search_notifications", sa.Column("provider_message_id", sa.String(120)))
    op.add_column("search_notifications", sa.Column("expires_at", sa.DateTime(timezone=True)))
    op.create_table("top_vacancies",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("resume_id", sa.String(36), nullable=False),
        sa.Column("search_id", sa.String(36), nullable=False),
        sa.Column("resume_filename", sa.String(255), nullable=False),
        sa.Column("search_name", sa.String(120), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    for column in ("resume_id", "score", "published_at"):
        op.create_index(f"ix_top_vacancies_{column}", "top_vacancies", [column])
    # Old Telegram summaries can contain low-scoring jobs and truncated lists.
    op.execute(sa.text("UPDATE search_notifications SET status='superseded' "
                       "WHERE channel='telegram' AND status='pending' AND message LIKE '%Новых вакансий:%'"))


def downgrade() -> None:
    op.drop_table("top_vacancies")
    op.drop_column("search_notifications", "provider_message_id")
    op.drop_column("search_notifications", "expires_at")
