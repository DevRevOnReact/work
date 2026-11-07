from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
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
             dict(job("https://example.com/90"), title="Backend Engineer", score=90, published_at=(at-timedelta(hours=24)).isoformat())]
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


def test_top_summary_projects_only_needed_database_fields_and_keeps_approval_rules(db):
    import json
    from sqlalchemy import event
    from jobpilot.models import Candidate, VacancyReview
    from jobpilot.vacancy_identity import identity
    from jobpilot.vacancy_review import context_hash, url_hash
    value = search(db)
    candidate = db.get(Candidate, 1)
    candidate.facts = {**candidate.facts, "resume_text": "large private CV " * 10000}
    known = dict(job("https://example.com/known"), description="Long employer description " * 10000)
    uncertain = dict(job("https://example.com/uncertain"), title="Backend Engineer",
                     eligibility="unknown", review_status="approved", description="Verified current conditions")
    db.add(VacancyReview(url_hash=url_hash(uncertain["canonical_url"]), identity_key=identity(uncertain),
        context_hash=context_hash(db, uncertain), search_id=value.id, data=uncertain, status="approved"))
    top_vacancies.save_top_vacancies(db, value, [known, uncertain])
    db.commit()
    full = top_vacancies.top_rows(db)
    statements = []

    def capture(state):
        statements.append(state.statement)

    event.listen(db, "do_orm_execute", capture)
    try:
        preview = top_vacancies.top_rows(db, summary=True)
    finally:
        event.remove(db, "do_orm_execute", capture)
    assert [row.id for row in full] == [row.id for row in preview]
    projected = next(row for row in preview if row.data["canonical_url"] == known["canonical_url"])
    assert "description" not in projected.data
    assert len(json.dumps(projected.data)) < len(json.dumps(known)) // 20
    assert not any(getattr(column, "name", "") in {"data", "facts"}
                   and getattr(getattr(column, "table", None), "name", "") in {"top_vacancies", "candidates", "vacancy_reviews"}
                   for statement in statements for column in getattr(statement, "selected_columns", []))
    # The thin view must still revoke an approval immediately after profile edits.
    candidate.facts = {**candidate.facts, "location": "Different location"}
    db.commit()
    assert [row.data["canonical_url"] for row in top_vacancies.top_rows(db, summary=True)] == [known["canonical_url"]]


def test_each_qualifying_job_is_queued_once_globally_without_truncating_urls(db, monkeypatch):
    enable(monkeypatch)
    first, second = search(db), search(db)
    jobs = [dict(job(f"https://example.com/{index}"), title=f"Frontend Engineer {index}") for index in range(510)]
    long_url = "https://example.com/" + "x" * 1200
    jobs.append(dict(job(long_url), score=60, title="Backend Engineer"))
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
    monkeypatch.setattr(searches, "evaluate", lambda *args: {"score": 80, "eligibility": "eligible"})
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


def test_top_collapses_cross_source_duplicates_and_keeps_best_score(db):
    first, second = search(db), search(db)
    original = job("https://example.com/first")
    duplicate = dict(original, canonical_url="https://elsewhere.example/second", score=95,
                     company="EXAMPLE", title="Frontend engineer")
    top_vacancies.save_top_vacancies(db, first, [original])
    top_vacancies.save_top_vacancies(db, second, [duplicate])
    db.commit()
    rows = top_vacancies.top_rows(db)
    assert len(rows) == 1 and rows[0].score == 95


def test_mini_app_requires_valid_telegram_signature_and_owner(db, monkeypatch):
    import hashlib
    import hmac
    import json
    import time
    from urllib.parse import urlencode
    enable(monkeypatch)
    app.dependency_overrides[session] = lambda: db
    try:
        client = TestClient(app)
        assert client.get("/telegram-mini/top-vacancies").status_code == 401
        values = {"auth_date": str(int(time.time())), "user": json.dumps({"id": 123})}
        check = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
        secret = hmac.new(b"WebAppData", b"secret-bot-token", hashlib.sha256).digest()
        values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        signed = urlencode(values)
        assert client.get("/telegram-mini/top-vacancies", headers={"X-Telegram-Init-Data": signed}).status_code == 200
        value = search(db)
        top_vacancies.save_top_vacancies(db, value, [job()])
        db.commit()
        row = db.scalar(select(TopVacancy))
        assert client.put(f"/telegram-mini/top-vacancies/{row.id}/favorite", json={"favorite": True},
                          headers={"X-Telegram-Init-Data": signed}).status_code == 200
        assert client.get("/telegram-mini/top-vacancies?favorites=true",
                          headers={"X-Telegram-Init-Data": signed}).json()["total"] == 1
        values["user"] = json.dumps({"id": 124})
        assert client.get("/telegram-mini/top-vacancies", headers={"X-Telegram-Init-Data": urlencode(values)}).status_code == 401
        assert client.put(f"/telegram-mini/top-vacancies/{row.id}/favorite", json={"favorite": False},
                          headers={"X-Telegram-Init-Data": urlencode(values)}).status_code == 401
        assert row.favorite
    finally:
        app.dependency_overrides.clear()


def test_notification_identity_uses_role_company_and_url_across_sources(db, monkeypatch):
    enable(monkeypatch)
    first, second = search(db), search(db)
    original = job()
    duplicate = dict(original, company="  EXAMPLE ", title="Frontend  Engineer!", location="Anywhere",
                     canonical_url="https://other.example/123")
    assert notifications.enqueue_top_telegram(db, first.id, [original, duplicate]) == 1
    db.commit()
    assert notifications.enqueue_top_telegram(db, second.id, [duplicate]) == 0
    assert notifications.enqueue_top_telegram(db, second.id, [dict(original, title="Different title",
        canonical_url=original["canonical_url"] + "?utm_source=other#apply")]) == 0
    assert notifications.enqueue_top_telegram(db, second.id, [dict(duplicate, company="Different Employer",
        canonical_url="https://different-employer.example/role")]) == 1
    assert notifications.enqueue_top_telegram(db, second.id, [dict(original, title="Backend Engineer",
        canonical_url="https://other.example/backend")]) == 1


def test_notification_remembers_new_aliases_of_previously_seen_vacancy(db, monkeypatch):
    enable(monkeypatch)
    value = search(db)
    original = job()
    assert notifications.enqueue_top_telegram(db, value.id, [original]) == 1
    db.commit()
    changed_title = dict(original, title="Frontend Developer")
    assert notifications.enqueue_top_telegram(db, value.id, [changed_title]) == 0
    db.commit()
    assert notifications.enqueue_top_telegram(db, value.id, [dict(changed_title,
        canonical_url="https://another.example/role")]) == 0


def test_technology_punctuation_does_not_merge_different_roles():
    from jobpilot.vacancy_identity import identity
    assert identity(dict(job(), title="C++ Developer")) != identity(dict(job(), title="C# Developer"))


def test_favorite_survives_expiry_cleanup_and_can_be_removed(db, monkeypatch):
    value = search(db)
    original = job()
    top_vacancies.save_top_vacancies(db, value, [original])
    db.commit()
    row = db.scalar(select(TopVacancy))
    app.dependency_overrides[session] = lambda: db
    try:
        client = TestClient(app)
        assert client.put(f"/top-vacancies/{row.id}/favorite", json={"favorite": True}).status_code == 200
        assert client.put(f"/top-vacancies/{row.id}/favorite", json={"favorite": True}).json()["favorite"]
        monkeypatch.setattr(top_vacancies, "now", lambda: now() + timedelta(days=4))
        top_vacancies.save_top_vacancies(db, value, [])
        db.commit()
        result = client.get("/top-vacancies?favorites=true").json()
        assert result["total"] == 1 and result["items"][0]["favorite"]
        assert client.get("/top-vacancies").json()["total"] == 1
        assert client.put(f"/top-vacancies/{row.id}/favorite", json={"favorite": False}).status_code == 200
        assert client.get("/top-vacancies?favorites=true").json()["total"] == 0
        top_vacancies.save_top_vacancies(db, value, [])
        db.commit()
        assert db.get(TopVacancy, row.id) is None
    finally:
        app.dependency_overrides.clear()


def test_favorite_follows_better_representative_and_filters_before_pagination(db):
    first, second = search(db), search(db)
    original = job()
    top_vacancies.save_top_vacancies(db, first, [original])
    db.flush()
    row = db.scalar(select(TopVacancy))
    top_vacancies.set_favorite(db, row.id, True)
    duplicate = dict(original, score=99, canonical_url="https://elsewhere.example/same-role")
    top_vacancies.save_top_vacancies(db, second, [duplicate, dict(job("https://example.com/other"), title="Backend Engineer")])
    db.commit()
    rows = top_vacancies.top_rows(db, favorites=True)
    assert len(rows) == 1 and rows[0].score == 99 and rows[0].favorite
    top_vacancies.set_favorite(db, rows[0].id, False)
    assert top_vacancies.top_rows(db, favorites=True) == []


@pytest.mark.parametrize("path", ["/top-vacancies", "/telegram-mini/top-vacancies"])
def test_favorite_updates_require_authenticated_owner(db, monkeypatch, path):
    value = search(db)
    top_vacancies.save_top_vacancies(db, value, [job()])
    db.commit()
    row = db.scalar(select(TopVacancy))
    monkeypatch.setattr(settings(), "auth_required", True)
    app.dependency_overrides[session] = lambda: db
    try:
        assert TestClient(app).put(f"{path}/{row.id}/favorite", json={"favorite": True}).status_code == 401
        assert not row.favorite
    finally:
        app.dependency_overrides.clear()
