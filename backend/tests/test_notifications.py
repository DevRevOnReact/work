from contextlib import contextmanager
from datetime import timedelta

import httpx
from pydantic import SecretStr
from sqlalchemy import select

from jobpilot import notifications
from jobpilot.config import settings
from jobpilot.models import Resume, SavedSearch, SearchNotification, now


def search(db):
    resume = Resume(variant="general", filename="test.txt", storage_key="test.txt", sha256="a" * 64)
    db.add(resume)
    db.flush()
    value = SavedSearch(name="Frontend", resume_id=resume.id, filters={})
    db.add(value)
    db.commit()
    return value


def job(url="https://jobs.example/1"):
    return {"score": 80, "company": "Example", "title": "Frontend Engineer", "location": "Remote",
            "canonical_url": url, "eligibility": "eligible", "published_at": now().isoformat()}


def enable(monkeypatch):
    monkeypatch.setattr(settings(), "telegram_bot_token", SecretStr("secret-bot-token"))
    monkeypatch.setattr(settings(), "telegram_chat_id", "123")
    monkeypatch.setattr(settings(), "resend_api_key", SecretStr("secret-email-token"))
    monkeypatch.setattr(settings(), "notification_email", "owner@example.com")


def test_email_digests_are_deduplicated_and_telegram_uses_individual_top_jobs(db, monkeypatch):
    enable(monkeypatch)
    value = search(db)
    notifications.enqueue_digest(db, value, [job(), job(), {**job("https://jobs.example/no"), "eligibility": "rejected"}])
    db.commit()
    assert len(list(db.scalars(select(SearchNotification)))) == 1
    notifications.enqueue_digest(db, value, [job()])
    db.commit()
    assert len(list(db.scalars(select(SearchNotification)))) == 1
    notifications.enqueue_digest(db, value, [job(), dict(job("https://jobs.example/new"), title="Backend Engineer")])
    db.commit()
    assert len(list(db.scalars(select(SearchNotification)))) == 2
    assert all(row.channel == "email" for row in db.scalars(select(SearchNotification)))


def test_email_deduplicates_role_across_searches_and_changed_urls(db, monkeypatch):
    enable(monkeypatch)
    first, second = search(db), search(db)
    notifications.enqueue_digest(db, first, [job()])
    db.commit()
    notifications.enqueue_digest(db, second, [dict(job("https://other.example/role"), title="FRONTEND  ENGINEER!", location="Anywhere")])
    db.commit()
    assert len(list(db.scalars(select(SearchNotification)))) == 1


def test_delivery_retry_redacts_secrets_and_preserves_email_idempotency(db, monkeypatch):
    enable(monkeypatch)
    value = search(db)
    notifications.enqueue_digest(db, value, [job()])
    notifications.enqueue_top_telegram(db, value.id, [job()])
    db.commit()
    @contextmanager
    def factory():
        yield db
    monkeypatch.setattr(notifications, "SessionLocal", factory)
    original = httpx.Client
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "500"}) if request.url.host == "api.telegram.org" else httpx.Response(200, json={"id": "sent"})
    monkeypatch.setattr(notifications.httpx, "Client",
                        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    notifications.deliver_pending()
    rows = list(db.scalars(select(SearchNotification)))
    telegram = next(r for r in rows if r.channel == "telegram")
    email = next(r for r in rows if r.channel == "email")
    assert telegram.status == "pending" and telegram.error == "HTTP 429"
    assert "secret" not in telegram.error
    deadline = telegram.next_attempt.replace(tzinfo=now().tzinfo)
    assert deadline > now() + timedelta(seconds=450)
    assert email.status == "sent"
    assert next(r for r in calls if r.url.host == "api.resend.com").headers["Idempotency-Key"] == email.id
    assert len(calls) == 2
    notifications.deliver_pending()
    assert len(calls) == 2


def test_dispatch_queues_render_channels_and_deduplicates(db, monkeypatch):
    from fastapi.testclient import TestClient
    from jobpilot.api import app
    from jobpilot.db import session
    enable(monkeypatch)
    value = search(db)
    value.status = "complete"
    value.results = [job()]
    from jobpilot.top_vacancies import save_top_vacancies
    save_top_vacancies(db, value, value.results)
    db.commit()
    deliveries = []
    monkeypatch.setattr(notifications, "deliver_pending", lambda: deliveries.append(True))
    app.dependency_overrides[session] = lambda: db
    try:
        client = TestClient(app)
        response = client.post("/notifications/dispatch")
        assert response.status_code == 200
        assert response.json()["counts"]["pending"] == 2
        assert client.post("/notifications/dispatch").json()["counts"]["pending"] == 2
        assert len(deliveries) == 2
    finally:
        app.dependency_overrides.clear()
