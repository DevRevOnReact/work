import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import sessionmaker
from test_saved_searches import setup_resumes

from jobpilot import search_runtime
from jobpilot.config import settings
from jobpilot.models import SavedSearch


@pytest.mark.asyncio
async def test_single_host_restart_resumes_recent_queued_and_running_searches(db, monkeypatch):
    resume, _ = setup_resumes(db)
    searches = [SavedSearch(name=state, resume_id=resume.id, status=state,
                           started_at=datetime.now(UTC), results=[{"title": "previous"}], result_count=1)
                for state in ("queued", "running")]
    db.add_all(searches)
    db.commit()
    monkeypatch.setattr(settings(), "hosted_mode", True)
    monkeypatch.setattr(settings(), "automatic_search_schedule", True)
    monkeypatch.setattr(search_runtime, "SessionLocal", sessionmaker(db.bind, expire_on_commit=False))
    runtime = search_runtime.SearchRuntime()
    runtime.executor.shutdown()
    runtime.executor = Mock()
    try:
        runtime.start()
        assert runtime.executor.submit.call_count == 2
        for search in searches:
            db.refresh(search)
            assert search.status == "queued" and search.errors == []
            assert search.results == [{"title": "previous"}]
    finally:
        runtime.stop()
        await asyncio.gather(runtime.task, runtime.telegram_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_external_scheduler_keeps_recent_remote_work_and_only_recovers_stale(db, monkeypatch):
    resume, _ = setup_resumes(db)
    recent = SavedSearch(name="remote", resume_id=resume.id, status="running", started_at=datetime.now(UTC))
    stale = SavedSearch(name="stale", resume_id=resume.id, status="queued",
                        started_at=datetime.now(UTC) - timedelta(minutes=21))
    db.add_all([recent, stale])
    db.commit()
    monkeypatch.setattr(settings(), "hosted_mode", True)
    monkeypatch.setattr(settings(), "automatic_search_schedule", False)
    monkeypatch.setattr(search_runtime, "SessionLocal", sessionmaker(db.bind, expire_on_commit=False))
    runtime = search_runtime.SearchRuntime()
    runtime.executor.shutdown()
    runtime.executor = Mock()
    try:
        runtime.start()
        runtime.executor.submit.assert_not_called()
        db.refresh(recent)
        db.refresh(stale)
        assert recent.status == "running"
        assert stale.status == "failed"
    finally:
        runtime.stop()
        await asyncio.gather(runtime.task, runtime.telegram_task, return_exceptions=True)
