"""Database-enforced append-only audit events."""

from alembic import op

revision = "0002_audit_immutable"
down_revision = "dbd0616a6bd1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("""CREATE FUNCTION prevent_audit_mutation() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'Audit records are immutable'; END; $$ LANGUAGE plpgsql""")
        op.execute(
            "CREATE TRIGGER audit_no_mutation BEFORE UPDATE OR DELETE ON audit FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation()"
        )
    elif dialect == "sqlite":
        op.execute(
            "CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT, 'Audit records are immutable'); END"
        )
        op.execute(
            "CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT, 'Audit records are immutable'); END"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER audit_no_mutation ON audit")
        op.execute("DROP FUNCTION prevent_audit_mutation()")
    else:
        op.execute("DROP TRIGGER IF EXISTS audit_no_update")
        op.execute("DROP TRIGGER IF EXISTS audit_no_delete")
