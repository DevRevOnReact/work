"""Opt-in PostgreSQL tests. Use a disposable database, never your application DB."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from jobpilot.config import settings
from jobpilot.models import Application, Candidate, Control, Resume, Source
from jobpilot.services import bootstrap, ingest, queue_ready
from jobpilot.workers import claim
from test_domain import resume_profile, vacancy


@pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="Set TEST_POSTGRES_URL to a disposable PostgreSQL database",
)
def test_pg_migrations_audit_and_concurrent_claim(monkeypatch):
    # A private random schema isolates this test even in a shared test database.
    url = os.environ["TEST_POSTGRES_URL"]
    schema = "test_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    from sqlalchemy.engine import make_url

    scoped = (
        make_url(url)
        .update_query_dict({"options": f"-csearch_path={schema}"})
        .render_as_string(hide_password=False)
    )
    engine = create_engine(scoped)
    try:
        monkeypatch.setattr(settings(), "database_url", scoped)
        cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
        command.upgrade(cfg, "head")
        factory = sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr("jobpilot.workers.SessionLocal", factory)
        with factory() as db:
            bootstrap(db)
            p = db.get(Candidate, 1)
            resume = Resume(variant="general", filename="cv.pdf", storage_key="cv.pdf", sha256="f" * 64)
            db.add(resume)
            db.flush()
            p.facts = resume_profile(
                full_name="Test Candidate", email="test@example.invalid",
                active_resume_id=resume.id, resume_filename=resume.filename,
            ).model_dump()
            db.get(Control, 1).auto_apply = True
            db.get(Control, 1).min_score = 0
            source = Source(provider="ashby", board="test", company="Example")
            db.add(source)
            db.flush()
            ingest(db, source, [vacancy()])
            db.commit()
            ids = queue_ready(db)
            db.commit()
            assert len(ids) == 1
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(claim, [ids[0]] * 4))
        assert results.count(True) == 1
        with engine.begin() as c:
            with pytest.raises(Exception, match="immutable"):
                c.execute(text("UPDATE audit SET kind='tamper'"))
        with factory() as db:
            assert db.scalar(select(Application)).state == "APPLYING"
    finally:
        engine.dispose()
        with admin.begin() as c:
            c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
