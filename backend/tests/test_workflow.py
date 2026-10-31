from datetime import timedelta
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from jobpilot.models import Application, Control, Job, Source, now
from jobpilot.services import ingest, queue_ready, recover_stale, transition
from jobpilot.workers import claim
from test_domain import vacancy


def populate(db):
    source = Source(provider="ashby", board="acme", company="Acme")
    db.add(source)
    db.flush()
    ingest(db, source, [vacancy()])
    db.commit()
    return source, db.scalar(select(Application))


def test_ingest_idempotency(db):
    source, app = populate(db)
    assert ingest(db, source, [vacancy()]) == 0
    assert len(db.scalars(select(Job)).all()) == 1
    assert app.state == "MATCHED"


def test_fuzzy_duplicates_are_held(db):
    source, app = populate(db)
    ingest(
        db,
        source,
        [vacancy(external_id="test:2", canonical_url="https://jobs.ashbyhq.com/test/2/application")],
    )
    db.commit()
    jobs = db.scalars(select(Job)).all()
    assert jobs[1].duplicate_of == jobs[0].id


def test_no_name_no_email_no_fake_cv(db):
    _, app = populate(db)
    assert queue_ready(db) == []
    assert db.scalar(select(Job)).score == 0
    assert app.resume_id is None
    assert app.state == "MATCHED"


def test_ready_and_single_claim(db, active_candidate, monkeypatch):
    _, app = populate(db)
    db.commit()
    assert queue_ready(db) == [app.id]
    db.commit()
    monkeypatch.setattr("jobpilot.workers.SessionLocal", sessionmaker(db.bind, expire_on_commit=False))
    assert claim(app.id)
    assert not claim(app.id)


def test_recovery_distinguishes_submit_intent(db):
    _, app = populate(db)
    app.state = "APPLYING"
    app.submit_intent_at = now()
    app.updated_at = now() - timedelta(hours=1)
    db.commit()
    recover_stale(db, 600)
    assert app.state == "SUBMISSION_UNCERTAIN"
    with pytest.raises(ValueError):
        transition(db, app, "READY")


def test_pause_prevents_claim(db, monkeypatch):
    _, app = populate(db)
    app.state = "READY"
    db.get(Control, 1).enabled = False
    db.commit()
    monkeypatch.setattr("jobpilot.workers.SessionLocal", sessionmaker(db.bind, expire_on_commit=False))
    assert not claim(app.id)
