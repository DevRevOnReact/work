"""Copy the local workspace into an empty PostgreSQL database, with hidden credentials."""
import argparse
import getpass
import hashlib
import os
import sqlite3
import sys
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def migration_config():
    from alembic.config import Config

    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/migrations"))
    return config


def upgrade_database(url: str) -> None:
    from alembic import command
    from jobpilot.config import settings

    configuration = settings()
    previous = configuration.database_url, configuration.database_url_unpooled
    try:
        configuration.database_url = url
        configuration.database_url_unpooled = url
        command.upgrade(migration_config(), "head")
    finally:
        configuration.database_url, configuration.database_url_unpooled = previous


@contextmanager
def upgraded_snapshot(database: Path):
    """Upgrade a consistent scratch copy, never the user's source database."""
    from sqlalchemy import create_engine

    with TemporaryDirectory(prefix="jobpilot-import-") as directory:
        snapshot = Path(directory) / "source.db"
        with (
            closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as source,
            closing(sqlite3.connect(snapshot)) as destination,
        ):
            source.backup(destination)
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Source database integrity check failed")
        url = f"sqlite:///{snapshot.as_posix()}"
        upgrade_database(url)
        engine = create_engine(url)
        try:
            yield engine
        finally:
            engine.dispose()


def copy_workspace(source, target, resumes_dir: Path) -> dict[str, int]:
    from jobpilot import models
    from jobpilot.db import Base
    from sqlalchemy import func, select, text

    with source.connect() as original, target.begin() as destination:
        existing = sum(destination.scalar(select(func.count()).select_from(table)) or 0
                       for table in Base.metadata.sorted_tables)
        if existing:
            raise ValueError("Destination is not empty; refusing to overwrite existing data")
        counts = {}
        for table in Base.metadata.sorted_tables:
            rows = [dict(row) for row in original.execute(select(table)).mappings()]
            if table.name == "saved_searches":
                for row in rows:
                    if row["status"] in {"queued", "running"}:
                        row["status"] = "idle"
            if table.name == "control":
                for row in rows:
                    row["auto_apply"] = False
                    row["mode"] = "manual"
            if rows:
                destination.execute(table.insert(), rows)
            counts[table.name] = len(rows)
        for resume in original.execute(select(models.Resume.__table__)).mappings():
            content = destination.scalar(select(models.ResumeFile.content).where(
                models.ResumeFile.resume_id == resume["id"]))
            if content is None:
                path = (resumes_dir / resume["storage_key"]).resolve()
                if not path.is_relative_to(resumes_dir.resolve()):
                    raise ValueError("Resume path escapes its directory")
                content = path.read_bytes()
                destination.execute(models.ResumeFile.__table__.insert(),
                                    {"resume_id": resume["id"], "content": content})
            if hashlib.sha256(content).hexdigest() != resume["sha256"]:
                raise ValueError("Resume integrity check failed")
        counts["resume_files"] = counts["resumes"]
        for table in Base.metadata.sorted_tables:
            actual = destination.scalar(select(func.count()).select_from(table))
            if actual != counts[table.name]:
                raise ValueError(f"Imported row count differs: {table.name}")
        if destination.dialect.name == "postgresql":
            destination.execute(text("SELECT setval(pg_get_serial_sequence('audit', 'id'), "
                                     "COALESCE((SELECT MAX(id) FROM audit), 1), "
                                     "EXISTS (SELECT 1 FROM audit))"))
        return counts


def migrate(target_url: str, validate_only: bool, source_path: Path | None = None,
            resumes_dir: Path | None = None) -> None:
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    if not target_url.startswith("postgresql+psycopg://"):
        raise ValueError("A PostgreSQL psycopg URL is required")
    parsed = make_url(target_url)
    direct_url = parsed.set(host=(parsed.host or "").replace("-pooler.", ".")).render_as_string(hide_password=False)
    upgrade_database(direct_url)
    target = create_engine(direct_url)
    try:
        with target.connect() as connection:
            if connection.scalar(text("SELECT version_num FROM alembic_version")) != \
                    ScriptDirectory.from_config(migration_config()).get_current_head():
                raise ValueError("Destination schema is not current")
        if validate_only:
            print("PostgreSQL migrations validated.", flush=True)
            return
        with upgraded_snapshot(source_path or ROOT / "data/jobpilot-local.db") as source:
            counts = copy_workspace(source, target, resumes_dir or ROOT / "data/resumes")
            print(f"Imported and verified row counts: {counts}", flush=True)
    finally:
        target.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--source", type=Path, default=ROOT / "data/jobpilot-local.db")
    parser.add_argument("--resumes-dir", type=Path, default=ROOT / "data/resumes")
    parser.add_argument("--from-env", action="store_true", help="Read DATABASE_URL from the process environment")
    args = parser.parse_args()
    if not args.from_env and not sys.stdin.isatty():
        raise SystemExit("Use an interactive terminal; the database URL is hidden.")
    secret = os.environ.get("DATABASE_URL", "") if args.from_env else getpass.getpass("PostgreSQL URL (hidden): ")
    try:
        migrate(secret, args.validate, args.source, args.resumes_dir)
    except Exception as exc:  # noqa: BLE001 -- driver errors may contain credentials or private rows
        # Never log driver exceptions containing private rows or credentials.
        print(f"Migration failed: {type(exc).__name__}", flush=True)
        raise SystemExit(1)
