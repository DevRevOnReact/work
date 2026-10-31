from unittest.mock import Mock

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from jobpilot.api import app
from jobpilot.db import session
from jobpilot.models import Candidate, SavedSearch, Source
from jobpilot.search_setup import ensure_resume_searches
from jobpilot.searches import SearchFilters, matches
from jobpilot import search_runtime, search_runner
from test_saved_searches import setup_resumes, vacancy


def test_setup_keeps_all_existing_parameters_and_is_idempotent(db):
    first, second = setup_resumes(db)
    filters = SearchFilters(title="Java", min_score=73, posted_days=14, work_mode="hybrid",
                            company="Acme", salary="yes", exclude="intern").model_dump()
    original = SavedSearch(name="My custom search", resume_id=first.id, filters=filters, enabled=False)
    db.add(original); db.commit()
    before = dict(db.get(Candidate, 1).facts)
    assert ensure_resume_searches(db) == 1
    assert ensure_resume_searches(db) == 0
    db.commit()
    assert original.filters == filters and not original.enabled and original.name == "My custom search"
    assert db.get(Candidate, 1).facts == before
    created = db.scalar(select(SavedSearch).where(SavedSearch.resume_id == second.id))
    assert created.filters["skills"] == "React" and created.filters["skills_mode"] == "any"


def test_any_all_alternatives_and_work_modes():
    job = vacancy()
    assert matches(job, SearchFilters(title="Designer, Engineer", location="Armenia, Berlin", skills="React, Python", skills_mode="any"))
    assert not matches(job, SearchFilters(skills="React, Python", skills_mode="all"))
    assert not matches(job.model_copy(update={"description": "JavaScript"}), SearchFilters(skills="Java"))
    hybrid = job.model_copy(update={"location": "Hybrid / Remote Berlin"})
    assert not matches(hybrid, SearchFilters(work_mode="remote"))
    assert matches(hybrid, SearchFilters(work_mode="hybrid"))
    unknown = job.model_copy(update={"location": "Berlin"})
    assert not matches(unknown, SearchFilters(work_mode="onsite"))
    assert matches(unknown.model_copy(update={"description": "This role is remote"}), SearchFilters(work_mode="remote"))


def test_chosen_resume_warnings_do_not_inherit_another_resume(db):
    from jobpilot.searches import search_profile
    first, second = setup_resumes(db)
    candidate = db.get(Candidate, 1)
    candidate.facts = {**candidate.facts, "resume_parse_warnings": ["Other resume warning"]}
    db.commit()
    assert search_profile(db, first.id).resume_parse_warnings == []
    assert search_profile(db, second.id).resume_parse_warnings == []


def test_summaries_do_not_download_descriptions(db):
    first, _ = setup_resumes(db)
    search = SavedSearch(name="CV search", resume_id=first.id, filters={}, results=[{"description": "large"}])
    db.add(search); db.commit()
    app.dependency_overrides[session] = lambda: db
    try:
        client = TestClient(app)
        assert "results" not in client.get("/searches").json()[0]
        assert client.get(f"/searches/{search.id}").json()["results"] == [{"description": "large"}]
    finally:
        app.dependency_overrides.clear()


def test_local_queue_claims_once_and_daily_schedule_respects_pause(db, monkeypatch):
    first, second = setup_resumes(db)
    one = SavedSearch(name="One", resume_id=first.id, filters={}, enabled=True)
    two = SavedSearch(name="Two", resume_id=second.id, filters={}, enabled=False)
    db.add_all([one, two]); db.commit()
    monkeypatch.setattr(search_runtime, "SessionLocal", sessionmaker(db.bind, expire_on_commit=False))
    runtime = search_runtime.SearchRuntime()
    runtime.executor.shutdown()
    runtime.executor = Mock()
    assert runtime.enqueue(one.id)
    assert not runtime.enqueue(one.id)
    runtime.executor.submit.assert_called_once()
    assert db.get(SavedSearch, one.id, populate_existing=True).status == "queued"
    runtime.schedule_due()
    assert runtime.executor.submit.call_count == 1
    assert db.get(SavedSearch, two.id, populate_existing=True).status == "idle"


def test_failed_fetch_preserves_previous_results(db):
    first, _ = setup_resumes(db)
    search = SavedSearch(name="Old", resume_id=first.id, filters={}, results=[{"title": "Previous"}], result_count=1)
    db.add_all([search, Source(company="Fail", provider="ashby", board="fail")]); db.commit()
    class Adapter:
        def __init__(self, client): pass
        async def fetch(self, *args): raise RuntimeError("failure")
    factory = sessionmaker(db.bind, expire_on_commit=False)
    search_runner.run_saved_search(search.id, factory, {"ashby": Adapter}, use_cache=False)
    db.refresh(search)
    assert search.status == "failed" and search.results == [{"title": "Previous"}] and search.result_count == 1
