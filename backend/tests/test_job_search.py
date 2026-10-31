from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from jobpilot.api import app
from jobpilot.config import settings
from jobpilot.db import session
from jobpilot.models import Application, Job, Source, now


@pytest.fixture
def search_client(db):
    source = Source(provider="ashby", board="search", company="Search")
    db.add(source)
    db.flush()
    for i, (company, location, salary, score, eligible, state) in enumerate([
        ("Alpha", "Remote worldwide", "$80,000", 90, "eligible", "MATCHED"),
        ("Beta", "Berlin hybrid", None, 65, "unknown", "FAILED"),
        ("100% Labs", "Office onsite", "$60,000", 20, "rejected", "REJECTED"),
    ]):
        job = Job(source_id=source.id, provider="ashby", external_id=f"search-{i}",
                  canonical_url=f"https://jobs.ashbyhq.com/search/{i}", company=company,
                  title="Python Developer", location=location, description="Python and SQL",
                  salary=salary, score=score, eligibility=eligible,
                  discovered_at=now() - timedelta(days=i))
        db.add(job)
        db.flush()
        db.add(Application(job_id=job.id, state=state))
    db.commit()
    app.dependency_overrides[session] = lambda: db
    yield TestClient(app, headers={"Authorization": "Bearer " + settings().api_token.get_secret_value()})
    app.dependency_overrides.clear()


def test_combined_filters_and_total(search_client):
    result = search_client.get("/jobs", params={"work_mode": "remote", "has_salary": True,
                                               "eligibility": "eligible", "min_score": 80}).json()
    assert result["total"] == 1
    assert result["items"][0]["company"] == "Alpha"
    result = search_client.get("/jobs", params={"state": "FAILED", "location": "Berlin", "has_salary": False}).json()
    assert result["total"] == 1
    assert result["items"][0]["company"] == "Beta"


def test_sort_pagination_and_literal_search(search_client):
    result = search_client.get("/jobs?sort=score_asc&offset=1&limit=1").json()
    assert result["total"] == 3
    assert result["items"][0]["company"] == "Beta"
    result = search_client.get("/jobs", params={"q": "100%"}).json()
    assert result["total"] == 1
    assert result["items"][0]["company"] == "100% Labs"
    assert search_client.get("/jobs?q=SQL").json()["total"] == 3


@pytest.mark.parametrize("query", ["sort=invalid", "min_score=101", "offset=-1", "limit=201", "provider=unknown"])
def test_search_validation(search_client, query):
    assert search_client.get("/jobs?" + query).status_code == 422
