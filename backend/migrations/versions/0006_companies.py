"""Evidence-backed employer directory."""
from alembic import op
import sqlalchemy as sa

revision = "0006_companies"
down_revision = "0005_linkedin_leads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("key", sa.String(200), nullable=False, unique=True),
        sa.Column("careers_url", sa.Text(), nullable=False),
        sa.Column("signals", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("companies")
