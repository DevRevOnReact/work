import hashlib
import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import MetaData, create_engine, func, select, text

from jobpilot.config import settings
from jobpilot.db import Base

spec = importlib.util.spec_from_file_location(
    "workspace_migration", Path(__file__).resolve().parents[2] / "scripts/migrate_cloud_data.py")
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    database = tmp_path / "source.db"
    url = f"sqlite:///{database.as_posix()}"
    monkeypatch.setattr(settings(), "database_url", url)
    monkeypatch.setattr(settings(), "database_url_unpooled", "")
    command.upgrade(migration.migration_config(), "0010_linkedin_candidates")
    resumes = tmp_path / "resumes"
    resumes.mkdir()
    content = b"Test resume for migration"
    (resumes / "cv.txt").write_bytes(content)
    engine = create_engine(url)
    metadata = MetaData()
    metadata.reflect(engine)
    try:
        with engine.begin() as connection:
            connection.execute(metadata.tables["resumes"].insert(), {
                "id": "resume", "filename": "cv.txt", "variant": "general", "storage_key": "cv.txt",
                "sha256": hashlib.sha256(content).hexdigest(), "created_at": datetime.now(UTC),
            })
            connection.execute(metadata.tables["saved_searches"].insert(), [
                {"id": f"search-{index}", "name": f"Saved {index}", "resume_id": "resume", "filters": {},
                 "enabled": True, "status": "running", "results": [], "result_count": 0,
                 "errors": [], "progress": {}} for index in range(2)
            ])
            connection.execute(metadata.tables["control"].insert(), {
                "id": 1, "enabled": True, "auto_apply": True, "min_score": 80, "daily_limit": 20,
            })
    finally:
        engine.dispose()
    target_url = f"sqlite:///{(tmp_path / 'destination.db').as_posix()}"
    migration.upgrade_database(target_url)
    target = create_engine(target_url)
    try:
        yield database, resumes, target, content
    finally:
        target.dispose()


def test_import_upgrades_snapshot_and_preserves_original(workspace):
    database, resumes, target, content = workspace
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    with migration.upgraded_snapshot(database) as source:
        counts = migration.copy_workspace(source, target, resumes)
    assert counts["resumes"] == counts["resume_files"] == 1
    assert counts["saved_searches"] == 2
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    with target.connect() as connection:
        assert connection.scalar(text("SELECT content FROM resume_files")) == content
        assert connection.scalar(text("SELECT count(*) FROM saved_searches WHERE status='idle'")) == 2
        assert not connection.scalar(text("SELECT auto_apply FROM control"))
        assert connection.scalar(text("SELECT mode FROM control")) == "manual"


def test_import_refuses_nonempty_destination(workspace):
    database, resumes, target, _ = workspace
    with migration.upgraded_snapshot(database) as source:
        migration.copy_workspace(source, target, resumes)
        with pytest.raises(ValueError, match="not empty"):
            migration.copy_workspace(source, target, resumes)
    with target.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM saved_searches")) == 2


def test_corrupt_resume_rolls_back_entire_import(workspace):
    database, resumes, target, _ = workspace
    (resumes / "cv.txt").write_bytes(b"Corrupted")
    with migration.upgraded_snapshot(database) as source, pytest.raises(ValueError, match="integrity"):
        migration.copy_workspace(source, target, resumes)
    with target.connect() as connection:
        assert all(connection.scalar(select(func.count()).select_from(table)) == 0
                   for table in Base.metadata.sorted_tables)
