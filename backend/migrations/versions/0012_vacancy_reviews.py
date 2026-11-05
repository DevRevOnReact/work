"""Persist Telegram decisions on vacancies with uncertain eligibility."""
import sqlalchemy as sa
from alembic import op

revision = "0012_vacancy_reviews"
down_revision = "0011_application_automation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vacancy_reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("url_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("context_hash", sa.String(64), nullable=False),
        sa.Column("search_id", sa.String(36), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("notification_id", sa.String(64)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_vacancy_reviews_notification_id", "vacancy_reviews", ["notification_id"])


def downgrade() -> None:
    op.drop_index("ix_vacancy_reviews_notification_id", table_name="vacancy_reviews")
    op.drop_table("vacancy_reviews")
