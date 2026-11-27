from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from jobpilot import search_runner, sources
from jobpilot.domain import Profile, evaluate
from jobpilot.models import Resume, SavedSearch, Source
from jobpilot.resumes import extract_facts
from jobpilot.search_setup import resume_filters
from jobpilot.searches import SearchFilters, matches, work_mode
from test_saved_searches import setup_resumes, vacancy


@pytest.mark.parametrize("failure", [502, 503, 504, "timeout"])
async def test_temporary_feed_failure_recovers_without_failing_source(monkeypatch, failure):
    attempts = []
    delays = []
    async def sleep(delay):
        delays.append(delay)
    monkeypatch.setattr(sources.asyncio, "sleep", sleep)
    def respond(request):
        attempts.append(request)
        if len(attempts) == 1:
            if failure == "timeout":
                raise httpx.ReadTimeout("timeout", request=request)
            return httpx.Response(failure)
        return httpx.Response(200, json={"jobs": []})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        assert await sources.GreenhouseSource(client).fetch("acme", "Acme") == []
    assert len(attempts) == 2 and delays == [1]


@pytest.mark.parametrize("status,expected_calls", [(404, 1), (403, 1), (429, 1), (502, 3)])
async def test_source_failure_retries_are_bounded(monkeypatch, status, expected_calls):
    calls = []
    async def sleep(delay):
        pass
    monkeypatch.setattr(sources.asyncio, "sleep", sleep)
    def respond(request):
        calls.append(request)
        return httpx.Response(status)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await sources.GreenhouseSource(client).fetch("acme", "Acme")
    assert len(calls) == expected_calls


@pytest.mark.parametrize("location,description,title,mode", [
    ("OnSite · Berlin", "We also have remote teams.", "Engineer", "onsite"),
    ("Berlin", "This is not a remote role. We offer remote benefits.", "Engineer", "onsite"),
    ("Berlin", "Fully remote, with 2 days a week in the office.", "Engineer", "hybrid"),
    ("Berlin", "We are a remote-first company.", "Engineer", "remote"),
    ("Berlin", "Work from home.", "Engineer", "remote"),
    ("Berlin", "Offices in Berlin. Some remote teams exist.", "Engineer", ""),
    ("Remote EMEA", "Our offices offer free meals.", "Engineer", "remote"),
    ("Remote / Hybrid", "", "Engineer", "hybrid"),
    ("Berlin", "", "Remote Frontend Engineer", "remote"),
])
def test_remote_filter_uses_role_evidence(location, description, title, mode):
    job = vacancy().model_copy(update={"location": location, "description": description, "title": title})
    assert work_mode(job) == mode
    assert matches(job, SearchFilters(work_mode="remote")) == (mode == "remote")


def test_selected_period_is_reproducible_and_excludes_unknown_future_dates():
    at = datetime(2026, 11, 26, 15, tzinfo=UTC)
    filters = SearchFilters(posted_days=7)
    job = vacancy()
    assert matches(job.model_copy(update={"published_at": at - timedelta(days=6)}), filters, at=at)
    for published in (None, at - timedelta(days=8), at + timedelta(seconds=1)):
        assert not matches(job.model_copy(update={"published_at": published}), filters, at=at)
    assert not matches(job.model_copy(update={"published_at": at + timedelta(seconds=1)}), SearchFilters(), at=at)


@pytest.mark.parametrize("headline,variant", [
    ("Senior Frontend Engineer", "frontend"),
    ("Senior Full-Stack Engineer", "full-stack"),
    ("AI/ML Engineer | Applied AI, Python, LLM Systems", "react-python-ai"),
])
def test_real_resume_wording_preserves_experience_skills_and_role(headline, variant):
    text = (headline + "\nBatumi, Georgia | Remote EMEA\nSummary\n"
            "I have 5+ years in software\nengineering. Python FastAPI React TypeScript React Query.\n"
            "Pydantic pgvector RAG CatBoost OCR Elasticsearch BM25.\n"
            "Conflicting records go to a dispatcher. English (B2).")
    facts, warnings = extract_facts(text)
    assert facts["years_experience"] == 5
    assert not any("Стаж не указан" in warning for warning in warnings)
    assert "Go" not in facts["skills"]
    assert {"TanStack Query", "Pydantic", "pgvector", "RAG", "CatBoost", "OCR"} <= set(facts["skills"])
    filters = resume_filters(Resume(variant=variant), {"text": text, "facts": facts})
    assert filters.work_mode == "remote" and filters.posted_days == 7
    if variant == "react-python-ai":
        assert "AI" in filters.title and "LLM" in filters.title
        profile = Profile(active_resume_id="test", resume_text=text, **facts)
        for title in ("Applied AI Engineer", "LLM Engineer", "Backend Engineer", "Full Stack Engineer"):
            job = vacancy().model_copy(update={"title": title, "location": "Remote EMEA",
                                               "description": "Python FastAPI React. Build LLM APIs."})
            result = evaluate(job, profile)
            assert result["recommendable"], result["blockers"]


def test_go_language_is_recognized_without_confusing_english_verbs():
    assert "Go" in extract_facts("Backend Engineer\nPython, Go, SQL")[0]["skills"]
    profile = Profile(active_resume_id="test", resume_text="Backend Engineer\nPython APIs. Records go to a dispatcher.")
    result = evaluate(vacancy().model_copy(update={"title": "Go Backend Engineer", "description": "Go required."}), profile)
    assert not result["recommendable"] and "Go" in result["missing_skills"]


def test_short_ai_title_filters_match_whole_words():
    filters = SearchFilters(title="AI, ML, LLM")
    for title in ("Applied AI Engineer", "AI/ML Engineer", "LLM Engineer"):
        assert matches(vacancy().model_copy(update={"title": title}), filters)
    for title in ("Maintenance Engineer", "Retail Manager", "Email Developer"):
        assert not matches(vacancy().model_copy(update={"title": title}), filters)


@pytest.mark.parametrize("description,expected", [
    ("Location: EU (Remote). Python APIs. Work from anywhere in the EU.", "rejected"),
    ("Python APIs. Work from anywhere in the EU.", "rejected"),
    ("Python APIs. Applicants are required to be based in Germany.", "rejected"),
    ("Python APIs. Applicants are required to be based in Berlin, Munich, London or Porto. Work from anywhere.", "unknown"),
    ("Python APIs. Location: EMEA (Remote).", "eligible"),
    ("Python APIs. Our offices include Germany. Work from anywhere.", "eligible"),
    ("Location: Remote About us: A global company. Python APIs. Work from anywhere.", "eligible"),
])
def test_description_geography_cannot_be_lost_by_an_aggregator(description, expected):
    profile = Profile(active_resume_id="test", resume_text="Backend Engineer\nPython APIs.", location="Georgia")
    job = vacancy().model_copy(update={"location": "Remote", "description": description})
    result = evaluate(job, profile)
    assert result["eligibility"] == expected


def test_feed_cache_is_shared_by_all_resume_searches_with_more_than_64_sources(db):
    first, _ = setup_resumes(db)
    searches = [SavedSearch(name=f"Search {i}", resume_id=first.id, filters={"work_mode": "remote", "posted_days": 7})
                for i in range(2)]
    db.add_all(searches + [Source(provider="ashby", board=f"cache-{i}", company=f"Company {i}") for i in range(65)])
    db.commit()
    calls = []
    class Adapter:
        def __init__(self, client):
            pass
        async def fetch(self, board, company):
            calls.append(board)
            return []
    search_runner._cache.clear()
    try:
        for search in searches:
            search_runner.run_saved_search(search.id, sessionmaker(db.bind, expire_on_commit=False), {"ashby": Adapter})
            db.refresh(search)
            assert search.status == "complete"
        assert len(calls) == 65
    finally:
        search_runner._cache.clear()
