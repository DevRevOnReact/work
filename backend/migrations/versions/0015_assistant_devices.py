"""Revocable credentials for personal assistant clients."""
import sqlalchemy as sa
from alembic import op

revision = "0015_assistant_devices"
down_revision = "0014_reply_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("assistant_devices",
                    sa.Column("fingerprint", sa.String(64), primary_key=True),
                    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))


def downgrade() -> None:
    op.drop_table("assistant_devices")
