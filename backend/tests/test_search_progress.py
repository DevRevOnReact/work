from datetime import UTC, datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from jobpilot.api import app
from jobpilot import companies, company_api, search_runtime, schedule
from jobpilot.companies import discover_global_catalog
from jobpilot.config import settings
from jobpilot.db import Base, session
from jobpilot.models import Company, CompanyDiscovery, SavedSearch, Source
from jobpilot.search_runner import run_saved_search
from jobpilot.searches import published_today
from jobpilot.services import bootstrap
from test_saved_searches import setup_resumes, vacancy


def test_today_uses_moscow_calendar_and_rejects_unknown_future(monkeypatch):
    monkeypatch.setattr(settings(), "search_timezone", "Europe/Moscow")
    at = datetime(2026, 10, 31, 22, 0, tzinfo=UTC)  # November 1 in Moscow
    job = vacancy()
    assert published_today(job.model_copy(update={"published_at": at - timedelta(minutes=30)}), at)
    assert not published_today(job.model_copy(update={"published_at": at - timedelta(hours=2)}), at)
    assert not published_today(job.model_copy(update={"published_at": at + timedelta(seconds=1)}), at)
    assert not published_today(job.model_copy(update={"published_at": None}), at)
    assert published_today(job.model_copy(update={"published_at": at.replace(tzinfo=None)}), at)


def test_three_hour_slots_and_disabled_searches(db, monkeypatch):
    first, _ = setup_resumes(db)
    boundary = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
    assert schedule.slot_start(boundary + timedelta(hours=2, minutes=59)) == boundary
    assert schedule.slot_start(boundary + timedelta(hours=3)) == boundary + timedelta(hours=3)
    monkeypatch.setattr(schedule, "slot_start", lambda *args: boundary)
    previous = SavedSearch(name="previous", resume_id=first.id, started_at=boundary - timedelta(seconds=1))
    recent = SavedSearch(name="recent", resume_id=first.id, started_at=boundary)
    paused = SavedSearch(name="paused", resume_id=first.id, enabled=False)
    running = SavedSearch(name="running", resume_id=first.id, status="running")
    db.add_all([previous, recent, paused, running]); db.commit()
    assert schedule.due_searches(db) == [previous.id]


def test_queue_clears_previous_errors_and_completion(db, monkeypatch):
    from unittest.mock import Mock
    first, _ = setup_resumes(db)
    value = SavedSearch(name="retry", resume_id=first.id, status="failed", errors=["old error"],
                        completed_at=datetime.now(UTC), results=[{"title": "previous"}], result_count=1)
    db.add(value); db.commit()
    monkeypatch.setattr(search_runtime, "SessionLocal", sessionmaker(db.bind, expire_on_commit=False))
    runtime = search_runtime.SearchRuntime()
    runtime.executor.shutdown()
    runtime.executor = Mock()
    assert runtime.enqueue(value.id)
    db.refresh(value)
    assert value.status == "queued" and value.errors == [] and value.completed_at is None
    assert value.results == [{"title": "previous"}] and value.progress["stage"] == "queued"
    assert not runtime.enqueue(value.id)


def test_progress_is_pollable_during_io_and_fresh_results_only(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'progress.db').as_posix()}",
                           connect_args={"check_same_thread": False, "timeout": 0.2})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        bootstrap(db)
        first, _ = setup_resumes(db)
        value = SavedSearch(name="test", resume_id=first.id, filters={})
        db.add_all([value, Source(provider="ashby", company="Acme", board="acme")]); db.commit()
        search_id = value.id
    observed = []
    class Adapter:
        def __init__(self, client): pass
        async def fetch(self, *args):
            with factory() as reader:
                active = reader.get(SavedSearch, search_id)
                observed.append(active.progress["stage"])
                assert active.status == "running" and active.errors == []
            current = vacancy()
            return [current, current.model_copy(update={"canonical_url": "https://example.com/old", "published_at": datetime.now(UTC)-timedelta(days=1)}),
                    current.model_copy(update={"canonical_url": "https://example.com/unknown", "published_at": None})]
    try:
        run_saved_search(search_id, factory, {"ashby": Adapter}, use_cache=False)
        with factory() as db:
            value = db.get(SavedSearch, search_id)
            assert observed == ["fetching"]
            assert value.status == "complete" and value.result_count == 1
            assert value.progress["done"] == value.progress["total"] == 1
            assert value.progress["unknown_dates"] == 1
            assert value.progress["events"][-1]["message"].startswith("Готово:")
    finally:
        engine.dispose()


def test_invalid_saved_filters_fail_without_sticking_running(db):
    first, _ = setup_resumes(db)
    value = SavedSearch(name="corrupt", resume_id=first.id, filters={"posted_days": -1})
    db.add(value); db.commit()
    run_saved_search(value.id, sessionmaker(db.bind, expire_on_commit=False), {}, False)
    db.refresh(value)
    assert value.status == "failed" and value.completed_at
    assert value.progress["stage"] == "failed"


def test_worldwide_catalog_connects_jobs_and_is_idempotent(db, monkeypatch):
    from jobpilot import sources
    monkeypatch.setattr(sources, "STARTER_SOURCES", [{"company": "Example IT", "provider": "ashby", "board": "example"}])
    events = []
    result = discover_global_catalog(db, lambda *args, **kwargs: events.append(args))
    assert result["added"] == result["found"] == result["connected"] == 1
    assert discover_global_catalog(db, lambda *args, **kwargs: None)["added"] == 0
    assert db.scalar(select(Source)).board == "example"
    assert db.scalar(select(Company)).signals[0]["kind"] == "it_catalog"
    assert events


def test_background_company_discovery_api_progress_survives_reload(db, monkeypatch):
    from jobpilot import sources
    monkeypatch.setattr(sources, "STARTER_SOURCES", [{"company": "Example IT", "provider": "ashby", "board": "example"}])
    monkeypatch.setattr(company_api, "_last_discovery", float("-inf"))
    app.dependency_overrides[session] = lambda: db
    try:
        client = TestClient(app)
        labels = client.get("/companies/regions").json()
        assert {"Worldwide", "United States", "Germany", "Japan", "Australia", "Kazakhstan"} <= labels.keys()
        response = client.post("/companies/discover/start", json={"country": "Worldwide"})
        assert response.status_code == 202 and response.json()["status"] == "queued"
        jobs = client.get("/companies/discoveries").json()
        assert jobs[0]["id"] == response.json()["id"]
        assert jobs[0]["status"] == "complete" and jobs[0]["result"]["found"] == 1
        assert jobs[0]["progress"]["stage"] == "complete"
        assert client.get("/companies?signal=it_catalog").json()[0]["sources"][0]["enabled"]
    finally:
        app.dependency_overrides.clear()


def test_background_rate_limit_and_crash_recovery(db, monkeypatch):
    import httpx
    from jobpilot import company_discovery
    async def limited(*args):
        raise httpx.HTTPStatusError("secret URL", request=httpx.Request("GET", "https://api.github.com"), response=httpx.Response(403))
    monkeypatch.setattr(company_discovery, "discover_employers", limited)
    job = CompanyDiscovery(country="United States")
    db.add(job); db.commit()
    company_discovery.run_discovery(job.id, sessionmaker(db.bind, expire_on_commit=False))
    db.refresh(job)
    assert job.status == "failed" and "Лимит" in job.result["errors"][0]
    assert "secret" not in str(job.progress)
    job.status, job.started_at = "running", datetime.now(UTC)-timedelta(minutes=6)
    db.commit()
    app.dependency_overrides[session] = lambda: db
    try:
        result = TestClient(app).get("/companies/discoveries").json()[0]
        assert result["status"] == "failed" and result["completed_at"]
    finally:
        app.dependency_overrides.clear()


def test_global_country_city_evidence():
    assert companies.matches_country("San Francisco, CA", "United States")
    assert companies.matches_country("London, UK", "United Kingdom")
    assert companies.matches_country("Berlin", "Germany")
    assert companies.matches_country("Tokyo, 日本", "Japan")
    assert not companies.matches_country("Unknown", "Canada")


def test_discovery_auth_and_country_search_by_city_evidence(db, monkeypatch):
    from jobpilot.companies import save_company
    save_company(db, "Berlin IT", signal={"kind": "github_profile", "url": "https://github.com/example",
                                        "location": "Berlin", "country": "Germany"})
    db.commit()
    app.dependency_overrides[session] = lambda: db
    try:
        client = TestClient(app)
        assert client.get("/companies?q=Германия&signal=github_profile").json()[0]["name"] == "Berlin IT"
        monkeypatch.setattr(settings(), "auth_required", True)
        assert client.get("/companies/regions").status_code == 401
        assert client.get("/companies/discoveries").status_code == 401
        assert client.post("/companies/discover/start", json={"country": "Worldwide"}).status_code == 401
    finally:
        app.dependency_overrides.clear()
