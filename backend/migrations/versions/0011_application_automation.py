"""Durable browser preparation, approvals and per-domain submission policy."""
from alembic import op
import sqlalchemy as sa

revision = "0011_application_automation"
down_revision = "0010_linkedin_candidates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table, columns in {
        "applications": [
            sa.Column("mode", sa.String(20), nullable=False, server_default="manual"),
            sa.Column("progress", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("generated_resume_key", sa.String(255)),
            sa.Column("prepared_fingerprint", sa.String(64)),
            sa.Column("approved_fingerprint", sa.String(64)),
            sa.Column("command", sa.String(20)),
        ],
        "control": [
            sa.Column("mode", sa.String(20), nullable=False, server_default="manual"),
            sa.Column("authorized_domains", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("excluded_companies", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("required_approval_categories", sa.JSON(), nullable=False,
                      server_default='["legal", "salary", "personal"]'),
        ],
    }.items():
        for column in columns:
            op.add_column(table, column)
    # Old auto_apply was a separate opt-in with different behavior. The new
    # autonomous policy always requires fresh explicit activation and domains.
    op.execute("UPDATE control SET auto_apply = false")
    op.create_table("application_artifacts",
        sa.Column("application_id", sa.String(36), sa.ForeignKey("applications.id"), primary_key=True),
        sa.Column("attempt_id", sa.String(36), primary_key=True),
        sa.Column("filename", sa.String(40), primary_key=True),
        sa.Column("content", sa.LargeBinary(), nullable=False))


def downgrade() -> None:
    op.drop_table("application_artifacts")
    for table, names in {
        "applications": ["command", "approved_fingerprint", "prepared_fingerprint", "generated_resume_key", "progress", "mode"],
        "control": ["required_approval_categories", "excluded_companies", "authorized_domains", "mode"],
    }.items():
        for name in names:
            op.drop_column(table, name)
