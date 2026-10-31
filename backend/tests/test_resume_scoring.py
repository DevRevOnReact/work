from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker
from test_domain import vacancy

from jobpilot.domain import Profile
from jobpilot.models import Application, Candidate, Control, Job, Outbox, Resume, Source, now
from jobpilot.services import bootstrap, ingest, queue_ready, readiness, rescore
from jobpilot.workers import claim


def install_resume(db, **overrides):
    resume = Resume(variant="arbitrary", filename="java.pdf", storage_key="java.pdf", sha256="a" * 64)
    db.add(resume)
    db.flush()
    facts = {
        "full_name": "Candidate", "email": "candidate@example.invalid", "location": "Germany",
        "active_resume_id": resume.id, "resume_filename": resume.filename,
        "resume_text": "Java Engineer. Java banking bachelor English. 5 years experience.",
        "years_experience": 5, "salary_expectation": "USD 90k/year",
    }
    facts.update(overrides)
    candidate = db.get(Candidate, 1)
    candidate.facts = Profile(**facts).model_dump()
    db.get(Control, 1).auto_apply = True
    db.flush()
    return candidate, resume


def discover(db):
    source = Source(provider="ashby", board="scoring", company="Example")
    db.add(source)
    db.flush()
    ingest(db, source, [vacancy(
        title="Java Engineer", description="Java banking bachelor English. 5 years experience.",
        salary="USD 80k-100k/year",
    )])
    db.flush()
    return db.scalar(select(Job)), db.scalar(select(Application))


def test_exact_active_resume_wins_over_newer_and_variant(db):
    _, active = install_resume(db)
    job, app = discover(db)
    active.created_at = now() - timedelta(days=30)
    db.add(Resume(variant="frontend", filename="newer.pdf", storage_key="newer.pdf", sha256="b" * 64))
    db.flush()
    assert readiness(db, job, app) is None
    assert app.resume_id == active.id
    assert queue_ready(db) == [app.id]
    assert queue_ready(db) == []


def test_bootstrap_skips_unchanged_scores_without_loading_or_rescoring(db, monkeypatch):
    install_resume(db)
    job, application = discover(db)
    db.commit()
    def unexpected_rescore(*args, **kwargs):
        raise AssertionError("Unchanged resume should not be rescored at startup")
    monkeypatch.setattr("jobpilot.services.rescore", unexpected_rescore)
    bootstrap(db)
    assert job.score == 100 and application.state == "MATCHED"


@pytest.mark.parametrize("override,expected", [
    ({"active_resume_id": None}, "active resume"),
    ({"active_resume_id": "missing"}, "missing"),
    ({"resume_text": " \n "}, "parsed text"),
    ({"resume_filename": "different.pdf"}, "filename"),
])
def test_invalid_active_resume_never_falls_back(db, override, expected):
    install_resume(db, **override)
    job, app = discover(db)
    assert expected in readiness(db, job, app)
    assert job.score == 0
    assert app.resume_id is None
    db.get(Control, 1).min_score = 0
    assert queue_ready(db) == []
    assert app.state == "MATCHED"


def test_application_resume_mismatch_is_not_silently_rebound(db):
    install_resume(db)
    job, app = discover(db)
    other = Resume(variant="frontend", filename="old.pdf", storage_key="old.pdf", sha256="b" * 64)
    db.add(other)
    db.flush()
    app.resume_id = other.id
    assert "does not match" in readiness(db, job, app)
    assert app.resume_id == other.id
    assert queue_ready(db) == []


def test_empty_resume_scores_zero_and_cannot_queue_even_at_zero_threshold(db):
    job, app = discover(db)
    control = db.get(Control, 1)
    control.min_score = 0
    control.auto_apply = True
    assert job.score == 0
    assert job.explanation["reasons"]
    assert queue_ready(db) == []
    assert app.state == "MATCHED"


def test_auto_apply_is_opt_in_but_manual_readiness_still_works(db):
    install_resume(db)
    job, app = discover(db)
    control = db.get(Control, 1)
    control.auto_apply = False
    assert job.score == 100
    assert queue_ready(db) == []
    assert app.state == "MATCHED"
    assert readiness(db, job, app) is None
    control.auto_apply = True
    control.enabled = False
    assert queue_ready(db) == []


def test_profile_switch_rescores_below_threshold_jobs_before_queueing(db):
    job, app = discover(db)
    assert job.score == 0
    _, resume = install_resume(db)
    assert "rescore" in readiness(db, job, app)
    assert queue_ready(db) == [app.id]
    assert job.score == 100
    assert app.resume_id == resume.id


def test_ready_profile_switch_holds_and_resets_binding_when_auto_apply_off(db):
    candidate, original = install_resume(db)
    job, app = discover(db)
    assert queue_ready(db) == [app.id]
    candidate.facts = {**candidate.facts, "active_resume_id": None, "resume_text": ""}
    assert "active resume" in readiness(db, job, app)
    assert app.resume_id == original.id
    db.get(Control, 1).auto_apply = False
    assert queue_ready(db) == []
    assert app.state == "MATCHED"
    assert app.resume_id is None
    assert job.score == 0


def test_rescore_uses_new_resume_and_preserves_submission_boundary(db):
    candidate, _ = install_resume(db)
    job, app = discover(db)
    candidate.facts = {**candidate.facts, "resume_text": "Nurse. Healthcare."}
    rescore(db, job, app)
    assert job.explanation["matched_skills"] == []
    assert job.score < 80
    app.submit_intent_at = now()
    with pytest.raises(ValueError, match="Cannot rescore"):
        rescore(db, job, app)
    db.get(Control, 1).min_score = 0
    assert queue_ready(db) == []


def test_bootstrap_replaces_legacy_scores_without_changing_user_facts_or_notifying(db):
    candidate = db.get(Candidate, 1)
    candidate.facts = {"full_name": "Stored User", "email": "user@example.invalid", "custom": "keep",
                       "location": "Georgia", "skills": ["React"], "years_experience": 5}
    job, app = discover(db)
    job.score = 97
    job.explanation = {"breakdown": {"stack": 25}}
    app.state = "READY"
    db.commit()
    before = dict(candidate.facts)
    messages = db.scalar(select(func.count()).select_from(Outbox))
    bootstrap(db)
    assert candidate.facts == before
    assert job.score == 0
    assert len(job.explanation["criteria"]) == 10
    assert app.state == "MATCHED"
    assert app.resume_id is None
    assert db.scalar(select(func.count()).select_from(Outbox)) == messages


@pytest.mark.parametrize("state,intent", [
    ("APPLYING", False), ("FAILED", False), ("SUBMITTED", True),
    ("SUBMISSION_UNCERTAIN", True), ("MATCHED", True), ("READY", True),
])
def test_bootstrap_never_reopens_attempted_or_noneditable_applications(db, state, intent):
    job, app = discover(db)
    app.state = state
    app.submit_intent_at = now() if intent else None
    job.score = 97
    job.explanation = {"legacy": True}
    db.commit()
    bootstrap(db)
    assert app.state == state
    assert job.score == 97
    assert job.explanation == {"legacy": True}


def test_bootstrap_preserves_current_manually_queued_application(db):
    install_resume(db)
    _, app = discover(db)
    assert queue_ready(db) == [app.id]
    selected = app.resume_id
    db.commit()
    bootstrap(db)
    assert app.state == "READY"
    assert app.resume_id == selected


def test_single_claim_and_no_repeat_queue_after_submission_intent(db, monkeypatch):
    install_resume(db)
    _, app = discover(db)
    assert queue_ready(db) == [app.id]
    db.commit()
    monkeypatch.setattr("jobpilot.workers.SessionLocal", sessionmaker(db.bind, expire_on_commit=False))
    assert claim(app.id)
    assert not claim(app.id)
    db.refresh(app)
    app.submit_intent_at = now()
    app.state = "SUBMISSION_UNCERTAIN"
    db.commit()
    assert queue_ready(db) == []
    assert app.state == "SUBMISSION_UNCERTAIN"
