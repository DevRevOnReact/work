"""Separate worker availability from opt-in unattended applications."""

from alembic import op
import sqlalchemy as sa

revision = "0003_explicit_autopilot"
down_revision = "0002_audit_immutable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("control", sa.Column("auto_apply", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("control", "auto_apply")
