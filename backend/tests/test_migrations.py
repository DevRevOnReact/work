from pathlib import Path
import hashlib
from datetime import UTC, datetime
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, create_engine, select, text
import pytest
from jobpilot.config import settings


def test_migration_roundtrip_and_immutable_audit(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'migration-test.db').as_posix()}"
    monkeypatch.setattr(settings(), "database_url", url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    command.upgrade(config, "head")
    engine = create_engine(url)
    with engine.begin() as c:
        c.execute(text("INSERT INTO audit (kind,detail,created_at) VALUES ('test','{}',CURRENT_TIMESTAMP)"))
    with engine.begin() as c:
        with pytest.raises(Exception, match="immutable"):
            c.execute(text("UPDATE audit SET kind='changed'"))
    with engine.begin() as c:
        with pytest.raises(Exception, match="immutable"):
            c.execute(text("DELETE FROM audit"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine.dispose()


def test_upgrade_preserves_old_delivery_history_and_suppresses_pending_duplicates(tmp_path, monkeypatch):
    from jobpilot.vacancy_identity import delivery_keys, identity
    url = f"sqlite:///{(tmp_path / 'history-upgrade.db').as_posix()}"
    monkeypatch.setattr(settings(), "database_url", url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    command.upgrade(config, "0012_vacancy_reviews")
    engine = create_engine(url)
    metadata = MetaData()
    metadata.reflect(engine)
    stamp = datetime.now(UTC)
    original = {"company": "Example", "title": "Frontend Engineer", "canonical_url": "https://board.example/old"}
    old_id = hashlib.sha256(f"telegram-top-v1:{original['canonical_url']}".encode()).hexdigest()
    duplicate = {**original, "canonical_url": "https://different.example/new"}
    with engine.begin() as c:
        c.execute(metadata.tables["resumes"].insert().values(id="r", variant="general", filename="cv.txt",
                  storage_key="cv.txt", sha256="a" * 64, created_at=stamp))
        c.execute(metadata.tables["saved_searches"].insert().values(id="s", name="Saved", resume_id="r", filters={},
                  enabled=True, status="complete", results=[original], result_count=1, errors=[], progress={}))
        c.execute(metadata.tables["notification_seen"].insert().values(id=old_id))
        c.execute(metadata.tables["vacancy_reviews"].insert().values(id="review", url_hash="c" * 64,
                  context_hash="d" * 64, search_id="s", data=duplicate, status="rejected", updated_at=stamp))
        for index, status in enumerate(("sent", "pending")):
            c.execute(metadata.tables["search_notifications"].insert().values(id=old_id if index == 0 else "b" * 64,
                      search_id="s", channel="telegram", message="JobPilot · топ за 3 дня · 90/100\n\n"
                      f"Example · Frontend Engineer\nRemote\n\n{original['canonical_url']}", status=status,
                      attempts=0, next_attempt=stamp, created_at=stamp))
    command.upgrade(config, "head")
    metadata = MetaData()
    metadata.reflect(engine)
    with engine.connect() as c:
        keys = set(c.scalars(select(metadata.tables["notification_seen"].c.id)))
        assert delivery_keys(original) <= keys
        review = c.execute(select(metadata.tables["vacancy_reviews"])).mappings().one()
        assert review["status"] == "rejected" and review["identity_key"] == identity(duplicate)
        assert c.scalar(select(metadata.tables["search_notifications"].c.status).where(
            metadata.tables["search_notifications"].c.id == "b" * 64)) == "superseded"
    engine.dispose()
