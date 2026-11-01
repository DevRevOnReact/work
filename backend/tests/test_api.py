import pytest
from fastapi.testclient import TestClient
from jobpilot.api import app
from jobpilot.db import session
from jobpilot.config import settings


@pytest.fixture
def client(db):
    app.dependency_overrides[session] = lambda: db
    # No lifespan: test db is explicitly bootstrapped by fixture.
    yield TestClient(app)
    app.dependency_overrides.clear()


def headers():
    return {"Authorization": "Bearer " + settings().api_token.get_secret_value()}


def test_local_development_is_keyless(client):
    assert client.get("/profile").status_code == 200


def test_authentication_can_be_enabled_explicitly(client, monkeypatch):
    monkeypatch.setattr(settings(), "auth_required", True)
    assert client.get("/profile").status_code == 401
    assert client.get("/profile", headers=headers()).status_code == 200


def test_profile_unknowns_preserved(client):
    result = client.get("/profile", headers=headers()).json()
    assert result["email"] is None
    assert result["work_authorization"] == {}


def test_source_validation_and_duplicate(client):
    assert (
        client.post(
            "/sources", headers=headers(), json={"provider": "ashby", "board": "../admin", "company": "X"}
        ).status_code
        == 422
    )
    payload = {"provider": "ashby", "board": "test", "company": "Test"}
    assert client.post("/sources", headers=headers(), json=payload).status_code == 200
    assert client.post("/sources", headers=headers(), json=payload).status_code == 409


def test_manual_job_creation_and_duplicate_url(client, active_candidate):
    payload = {
        "company": "Example",
        "title": "Frontend Engineer",
        "location": "Remote worldwide",
        "canonical_url": "https://jobs.example.invalid/roles/frontend",
        "description": "Remote worldwide. React TypeScript JavaScript. 5 years experience.",
        "salary": "$100k yearly",
        "published_at": "2026-10-16T00:00:00Z",
    }
    response = client.post("/jobs/manual", headers=headers(), json=payload)
    assert response.status_code == 200
    job = response.json()
    assert job["provider"] == "manual"
    assert job["company"] == payload["company"]
    assert job["application"]["state"] == "MATCHED"
    assert client.post("/jobs/manual", headers=headers(), json=payload).status_code == 409
    assert client.get("/jobs", headers=headers()).json()["total"] == 1


def test_reject_non_pdf(client):
    response = client.post(
        "/resumes",
        headers=headers(),
        data={"variant": "frontend"},
        files={"file": ("bad.pdf", b"not a pdf", "application/pdf")},
    )
    assert response.status_code == 422


def test_analytics_empty_is_real_zero(client):
    data = client.get("/analytics", headers=headers()).json()
    assert data["submitted"] == 0
    assert data["response_rate"] == 0


def test_starter_sources_idempotent_without_legacy_discovery(client, monkeypatch):
    from unittest.mock import MagicMock
    from jobpilot.workers import discover_all

    enqueue = MagicMock()
    monkeypatch.setattr(discover_all, "delay", enqueue)
    response = client.post("/sources/starter", headers=headers(), json={})
    assert response.status_code == 200
    assert response.json()["connected"] >= 40
    assert response.json()["discovery"] == "scheduled"
    assert client.post("/sources/starter", headers=headers(), json={}).json()["connected"] == 0
    assert len(client.get("/sources", headers=headers()).json()) >= 40
    assert enqueue.call_count == 0


def test_apply_click_prepares_for_review_without_unattended_submission(client, db, active_candidate, monkeypatch):
    from unittest.mock import MagicMock
    from sqlalchemy import select
    from jobpilot.models import Application, Control, Source
    from jobpilot.services import ingest
    from jobpilot.workers import apply
    from test_domain import vacancy

    db.get(Control, 1).auto_apply = False
    source = Source(provider="ashby", board="apply-test", company="Example")
    db.add(source)
    db.flush()
    ingest(db, source, [vacancy()])
    db.commit()
    application = db.scalar(select(Application))
    enqueue = MagicMock()
    monkeypatch.setattr(apply, "delay", enqueue)
    response = client.post(f"/jobs/{application.job_id}/action", headers=headers(), json={"action": "apply"})
    assert response.status_code == 200
    assert response.json()["state"] == "QUEUED"
    assert response.json()["mode"] == "manual"
    assert response.json()["submit_intent_at"] is None
    enqueue.assert_not_called()


def test_unattended_autopilot_is_opt_in(client):
    result = client.get("/settings", headers=headers()).json()
    assert result["auto_apply"] is False
    assert result["enabled"] is True


def test_pre_submit_retry_revalidates_edited_profile(client, db, active_candidate, monkeypatch):
    from unittest.mock import MagicMock
    from sqlalchemy import select
    from jobpilot.models import Application, Source
    from jobpilot.services import ingest
    from jobpilot.workers import apply
    from test_domain import vacancy

    source = Source(provider="ashby", board="retry-test", company="Example")
    db.add(source)
    db.flush()
    ingest(db, source, [vacancy()])
    db.flush()
    application = db.scalar(select(Application))
    application.state = "FAILED"
    application.resume_id = active_candidate.facts["active_resume_id"]
    active_candidate.facts = {**active_candidate.facts, "phone": "+995555123456"}
    active_candidate.version += 1
    db.commit()
    monkeypatch.setattr(apply, "delay", MagicMock())
    response = client.post(f"/jobs/{application.job_id}/action", headers=headers(), json={"action": "retry"})
    assert response.status_code == 200
    assert response.json()["state"] == "QUEUED"
    assert response.json()["resume_id"] is None  # Browser worker selects from the current, intact uploads.
    assert response.json()["submit_intent_at"] is None
