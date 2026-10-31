from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
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
