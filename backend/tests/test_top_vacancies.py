from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from jobpilot import notifications, top_vacancies, searches
from jobpilot.api import app
from jobpilot.config import settings
from jobpilot.db import session
from jobpilot.models import SavedSearch, SearchNotification, Source, TopVacancy, now
from jobpilot.search_runner import run_saved_search
from test_notifications import enable, job, search
from test_saved_searches import setup_resumes, vacancy


def test_top_window_threshold_order_and_best_resume_dedup(db, monkeypatch):
    at = datetime(2026, 10, 31, 10, tzinfo=UTC)
    monkeypatch.setattr(top_vacancies, "now", lambda: at)
    value = search(db)
    valid = [dict(job("https://example.com/60"), score=60, published_at=(at-timedelta(hours=71)).isoformat()),
             dict(job("https://example.com/90"), score=90, published_at=(at-timedelta(hours=24)).isoformat())]
    invalid = [dict(job("https://example.com/low"), score=59, published_at=at.isoformat()),
               dict(job("https://example.com/old"), published_at=(at-timedelta(hours=72, seconds=1)).isoformat()),
               dict(job("https://example.com/unknown"), published_at=None),
               dict(job("https://example.com/future"), published_at=(at+timedelta(seconds=1)).isoformat())]
    top_vacancies.save_top_vacancies(db, value, valid + invalid)
    top_vacancies.save_top_vacancies(db, value, valid)
    other = search(db)
    top_vacancies.save_top_vacancies(db, other, [dict(valid[0], score=95)])
    db.commit()
    app.dependency_overrides[session] = lambda: db
    try:
        response = TestClient(app).get("/top-vacancies?limit=1")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2 and data["window_hours"] == 72 and data["min_score"] == 60
        assert len(data["items"]) == 1 and data["items"][0]["score"] == 95
        assert "description" not in data["items"][0]
        second = TestClient(app).get("/top-vacancies?offset=1&limit=1").json()
        assert second["items"][0]["score"] == 90
        assert TestClient(app).get("/top-vacancies?limit=0").status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_each_qualifying_job_is_queued_once_globally_without_truncating_urls(db, monkeypatch):
    enable(monkeypatch)
    first, second = search(db), search(db)
    jobs = [job(f"https://example.com/{index}") for index in range(510)]
    long_url = "https://example.com/" + "x" * 1200
    jobs.append(dict(job(long_url), score=60))
    jobs.append(dict(job("https://example.com/rejected"), score=95, eligibility="rejected"))
    jobs.extend([dict(job("https://example.com/low"), score=59), dict(job("https://example.com/old"),
                 published_at=(now()-timedelta(days=4)).isoformat()), dict(job("https://example.com/undated"), published_at=None)])
    assert notifications.enqueue_top_telegram(db, first.id, jobs) == 511
    db.commit()
    assert notifications.enqueue_top_telegram(db, second.id, jobs) == 0
    db.commit()
    queued = list(db.scalars(select(SearchNotification)))
    assert len(queued) == 511 and all(item.channel == "telegram" for item in queued)
    assert any(long_url in item.message for item in queued)
    assert all(item.message.count("https://") == 1 for item in queued)


def test_search_stores_three_day_top_while_daily_results_stay_today(db, monkeypatch):
    first, _ = setup_resumes(db)
    value = SavedSearch(name="Test", resume_id=first.id, filters={"posted_days": 1, "min_score": 95})
    db.add_all([value, Source(provider="ashby", board="acme", company="Acme")]); db.commit()
    monkeypatch.setattr(searches, "evaluate", lambda *args: {"score": 80, "eligibility": "unknown"})
    class Adapter:
        def __init__(self, client): pass
        async def fetch(self, *args):
            current = vacancy()
            return [current, current.model_copy(update={"canonical_url": "https://example.com/yesterday", "published_at": now()-timedelta(days=1)}),
                    current.model_copy(update={"canonical_url": "https://example.com/old", "published_at": now()-timedelta(days=4)})]
    run_saved_search(value.id, sessionmaker(db.bind, expire_on_commit=False), {"ashby": Adapter}, False)
    db.refresh(value)
    assert value.status == "complete" and value.result_count == 0
    assert len(list(db.scalars(select(TopVacancy)))) == 2


def test_deploy_test_records_telegram_receipt_and_does_not_send_twice(db, monkeypatch):
    enable(monkeypatch)
    search(db)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    @contextmanager
    def factory(): yield db
    monkeypatch.setattr(notifications, "SessionLocal", factory)
    original = httpx.Client
    messages = []
    def respond(request):
        messages.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 123}})
    monkeypatch.setattr(notifications.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    first = notifications.deployment_test(db)
    second = notifications.deployment_test(db)
    assert first["status"] == "sent" and first["provider_message_id"] == "123"
    assert second == first and len(messages) == 1
    assert "/top" in messages[0].content.decode()


def test_telegram_retry_after_from_body_defers_entire_chat_batch(db, monkeypatch):
    enable(monkeypatch)
    value = search(db)
    notifications.enqueue_top_telegram(db, value.id, [job(), job("https://example.com/second")])
    db.commit()
    @contextmanager
    def factory(): yield db
    monkeypatch.setattr(notifications, "SessionLocal", factory)
    original = httpx.Client
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 500}})
    monkeypatch.setattr(notifications.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    notifications.deliver_pending()
    rows = list(db.scalars(select(SearchNotification)))
    assert len(calls) == 1 and sum(item.attempts for item in rows) == 1
    assert all(item.next_attempt.replace(tzinfo=UTC) > now()+timedelta(seconds=490) for item in rows)
    assert all(item.status == "pending" for item in rows)


def test_top_and_deployment_test_require_authentication(db, monkeypatch):
    monkeypatch.setattr(settings(), "auth_required", True)
    app.dependency_overrides[session] = lambda: db
    try:
        client = TestClient(app)
        assert client.get("/top-vacancies").status_code == 401
        assert client.get("/release").status_code == 401
        assert client.post("/notifications/deployment-test").status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_expired_top_messages_are_not_sent_even_during_retry_cooldown(db, monkeypatch):
    enable(monkeypatch)
    value = search(db)
    notifications.enqueue_top_telegram(db, value.id, [job()])
    db.commit()
    item = db.scalar(select(SearchNotification))
    item.expires_at, item.next_attempt = now()-timedelta(seconds=1), now()+timedelta(hours=1)
    db.commit()
    @contextmanager
    def factory(): yield db
    monkeypatch.setattr(notifications, "SessionLocal", factory)
    original = httpx.Client
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200, json={"ok": True})
    monkeypatch.setattr(notifications.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    notifications.deliver_pending()
    assert item.status == "expired" and not calls


def test_cloud_can_queue_without_copying_bot_credentials_or_app_password(db, monkeypatch):
    from pydantic import SecretStr
    monkeypatch.setattr(settings(), "telegram_bot_token", SecretStr(""))
    monkeypatch.setattr(settings(), "telegram_chat_id", "")
    value = search(db)
    assert notifications.enqueue_top_telegram(db, value.id, [job()]) == 0
    assert notifications.enqueue_top_telegram(db, value.id, [job()], enqueue_without_credentials=True) == 1
    first = notifications.queue_deployment_test(db, "b" * 40)
    db.commit()
    second = notifications.queue_deployment_test(db, "b" * 40)
    assert first.id == second.id and first.status == "pending"
    assert len(list(db.scalars(select(SearchNotification)))) == 2


def test_telegram_forbidden_identifies_bot_without_exposing_token(db, monkeypatch):
    enable(monkeypatch)
    value = search(db)
    notifications.enqueue_top_telegram(db, value.id, [job()])
    db.commit()
    @contextmanager
    def factory(): yield db
    monkeypatch.setattr(notifications, "SessionLocal", factory)
    original = httpx.Client
    def respond(request):
        if request.url.path.endswith("/getMe"):
            return httpx.Response(200, json={"ok": True, "result": {"username": "ExampleJobPilotBot"}})
        return httpx.Response(403, json={"description": "Forbidden: bots can't send messages to bots https://api.telegram.org/botsecret-bot-token/sendMessage"})
    monkeypatch.setattr(notifications.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    notifications.deliver_pending()
    item = db.scalar(select(SearchNotification))
    assert "bots can't send messages to bots" in item.error
    assert "@ExampleJobPilotBot" in item.error and "secret-bot-token" not in item.error
