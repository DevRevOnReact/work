"""Copy the local workspace into an empty PostgreSQL database, with hidden credentials."""
import argparse
import getpass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def migrate(target_url: str, validate_only: bool) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, func, inspect, select, text
    from jobpilot.config import settings
    from jobpilot.db import Base
    from jobpilot import models
    import hashlib
    if not target_url.startswith("postgresql+psycopg://"):
        raise ValueError("A PostgreSQL psycopg URL is required")
    settings().database_url = target_url
    from sqlalchemy.engine import make_url
    parsed = make_url(target_url)
    direct_url = parsed.set(host=(parsed.host or "").replace("-pooler.", ".")).render_as_string(hide_password=False)
    settings().database_url_unpooled = direct_url
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/migrations"))
    command.upgrade(config, "head")
    target = create_engine(target_url)
    try:
        with target.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0007_cloud_storage_notifications"
        if validate_only:
            print("PostgreSQL migrations validated.", flush=True)
            return
        source = create_engine(f"sqlite:///{(ROOT / 'data/jobpilot-local.db').as_posix()}")
        try:
            with source.connect() as original, target.begin() as destination:
                existing = sum(destination.scalar(select(func.count()).select_from(table)) or 0
                               for table in Base.metadata.sorted_tables)
                if existing:
                    raise ValueError("Destination is not empty; refusing to overwrite existing data")
                source_tables = set(inspect(original).get_table_names())
                counts = {}
                for table in Base.metadata.sorted_tables:
                    if table.name not in source_tables:
                        continue
                    rows = [dict(row) for row in original.execute(select(table)).mappings()]
                    if table.name == "saved_searches":
                        for row in rows:
                            if row["status"] in {"queued", "running"}:
                                row["status"] = "idle"
                    if table.name == "control":
                        for row in rows:
                            row["auto_apply"] = False
                    if rows:
                        destination.execute(table.insert(), rows)
                    counts[table.name] = len(rows)
                resumes = list(original.execute(select(models.Resume.__table__)).mappings())
                for resume in resumes:
                    content = (ROOT / "data/resumes" / resume["storage_key"]).read_bytes()
                    if hashlib.sha256(content).hexdigest() != resume["sha256"]:
                        raise ValueError("Resume integrity check failed")
                    if not destination.scalar(select(models.ResumeFile.resume_id).where(
                            models.ResumeFile.resume_id == resume["id"])):
                        destination.execute(models.ResumeFile.__table__.insert(),
                                            {"resume_id": resume["id"], "content": content})
                # Preserve the next audit sequence value after explicit integer IDs.
                destination.execute(text("SELECT setval(pg_get_serial_sequence('audit', 'id'), "
                                         "COALESCE((SELECT MAX(id) FROM audit), 1), "
                                         "EXISTS (SELECT 1 FROM audit))"))
                print(f"Imported row counts: {counts}; durable CV files: {len(resumes)}", flush=True)
        finally:
            source.dispose()
    finally:
        target.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    if not sys.stdin.isatty():
        raise SystemExit("Use an interactive terminal; the database URL is hidden.")
    secret = getpass.getpass("PostgreSQL URL (hidden): ")
    try:
        migrate(secret, args.validate)
    except Exception as exc:
        # Never log driver exceptions containing private rows or credentials.
        print(f"Migration failed: {type(exc).__name__}", flush=True)
        raise SystemExit(1)
