from datetime import timedelta
from pathlib import Path
import runpy

from pydantic import SecretStr
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from jobpilot import notifications, searches
from jobpilot.config import settings
from jobpilot.models import SavedSearch, SearchNotification, Source, now
from jobpilot.search_runner import run_saved_search
from test_notifications import enable, search
from test_saved_searches import setup_resumes, vacancy


def reports(db):
    return [row for row in db.scalars(select(SearchNotification)) if "отчёт поиска" in row.message]


def test_report_is_once_per_attempt_including_zero_results(db, monkeypatch):
    enable(monkeypatch)
    value = search(db)
    value.status = "complete"
    value.started_at, value.completed_at = now() - timedelta(minutes=1), now()
    value.progress = {"fetched": 123, "done": 2, "total": 2, "top_matched": 0, "telegram_queued": 0}
    first = notifications.enqueue_search_report(db, value)
    db.commit()
    assert notifications.enqueue_search_report(db, value).id == first.id
    assert "Подходящих вакансий не найдено" in first.message and "123" in first.message
    assert "2/2" in first.message and "МСК" in first.message
    value.started_at = now()
    notifications.enqueue_search_report(db, value)
    db.commit()
    assert len(reports(db)) == 2


def test_cloud_run_queues_top_and_report_without_bot_secrets_and_reports_repeats(db, monkeypatch):
    monkeypatch.setattr(settings(), "telegram_bot_token", SecretStr(""))
    monkeypatch.setattr(settings(), "telegram_chat_id", "")
    first, _ = setup_resumes(db)
    value = SavedSearch(name="Frontend", resume_id=first.id, filters={})
    db.add(value); db.commit()
    value.filters = {"min_score": 95}
    db.add(Source(provider="ashby", company="Acme", board="acme")); db.commit()
    monkeypatch.setattr(searches, "evaluate", lambda *args: {"score": 80, "eligibility": "unknown"})
    class Adapter:
        def __init__(self, client): pass
        async def fetch(self, *args):
            return [vacancy().model_copy(update={"published_at": now() - timedelta(days=1)})]
    factory = sessionmaker(db.bind, expire_on_commit=False)
    for expected_new in (1, 0):
        run_saved_search(value.id, factory, {"ashby": Adapter}, False, enqueue_without_credentials=True)
        db.refresh(value)
        assert value.status == "complete" and value.result_count == 0
        assert value.progress["top_matched"] == 1 and value.progress["telegram_queued"] == expected_new
    messages = reports(db)
    assert len(messages) == 2
    assert any("уже были в очереди" in row.message for row in messages)
    assert all("Подошло в топ за 72 часа (от 60/100): 1" in row.message for row in messages)
    assert len(list(db.scalars(select(SearchNotification)))) == 3


def test_failed_search_reports_failure_instead_of_old_result_count(db, monkeypatch):
    value = search(db)
    value.results, value.result_count = [{"title": "old"}], 99
    db.commit()
    run_saved_search(value.id, sessionmaker(db.bind, expire_on_commit=False), {}, False,
                     enqueue_without_credentials=True)
    db.refresh(value)
    message = reports(db)[0].message
    assert value.status == "failed" and value.result_count == 99
    assert "Ошибка: не удалось" in message and "Предыдущие результаты сохранены" in message
    assert "99" not in message and "Нет включённых источников" in message


def test_partial_source_failure_keeps_matching_count_and_warning(db, monkeypatch):
    first, _ = setup_resumes(db)
    value = SavedSearch(name="Frontend", resume_id=first.id, filters={})
    db.add(value)
    db.add_all([Source(provider="ashby", company="Acme", board="good"),
                Source(provider="ashby", company="Broken", board="bad")]); db.commit()
    class Adapter:
        def __init__(self, client): pass
        async def fetch(self, board, *args):
            if board == "bad":
                raise ValueError("Источник недоступен")
            return []
    run_saved_search(value.id, sessionmaker(db.bind, expire_on_commit=False), {"ashby": Adapter}, False,
                     enqueue_without_credentials=True)
    db.refresh(value)
    message = reports(db)[0].message
    assert value.status == "complete" and "с ошибками отдельных источников" in message
    assert "Подходящих вакансий не найдено" in message and "Broken: Источник недоступен" in message


def test_report_skips_running_attempts_and_redacts_credentials(db, monkeypatch):
    enable(monkeypatch)
    value = search(db)
    value.status, value.started_at = "running", now()
    assert notifications.enqueue_search_report(db, value) is None
    value.status, value.completed_at = "failed", now()
    value.errors = ["secret-bot-token https://example.com/private?token=hidden"]
    item = notifications.enqueue_search_report(db, value)
    assert "secret-bot-token" not in item.message and "hidden" not in item.message


def test_linkedin_report_sql_is_idempotent_bounded_and_valid_for_existing_outbox():
    module = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/prepare_linkedin_report.py"))
    prepare = module["prepare"]
    record = {"run_started_at": now().isoformat(), "checked": 4, "verified": 0,
              "inserted": 0, "accepted": 0, "pending": 0, "error": "source_unavailable"}
    sql = prepare(record)
    assert sql == prepare(record) and "ON CONFLICT (id) DO NOTHING" in sql
    assert "INSERT INTO search_notifications" in sql
    for override in ({"checked": 13}, {"accepted": 1}, {"checked": True},
                     {"error": "arbitrary secret"}, {"run_started_at": "2026-01-01T00:00:00Z"}):
        with pytest.raises(ValueError):
            prepare({**record, **override})
