from datetime import UTC, datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy import select
from jobpilot.api import app
from jobpilot.config import settings
from jobpilot.db import session
from jobpilot.domain import JobInput
from jobpilot.models import Candidate, Resume, SavedSearch, Source
from jobpilot.searches import SearchFilters, make_results, matches, search_profile
from jobpilot import workers


def setup_resumes(db):
    first = Resume(variant="general", filename="python.txt", storage_key="p.txt", sha256="a" * 64)
    second = Resume(variant="general", filename="react.txt", storage_key="r.txt", sha256="b" * 64)
    db.add_all([first, second]); db.flush()
    candidate = db.get(Candidate, 1)
    candidate.facts = {**candidate.facts, "location": "Berlin", "resumes": {
        first.id: {"text": "Python backend engineer", "facts": {"skills": ["Python"], "location": ""}},
        second.id: {"text": "React frontend engineer", "facts": {"skills": ["React"], "location": ""}},
    }}
    db.commit()
    return first, second


def vacancy():
    return JobInput(provider="ashby", external_id="1", canonical_url="https://jobs.ashbyhq.com/acme/1", company="Acme", title="Python Engineer", location="Remote Berlin", description="Python and SQL", salary="$100k", published_at=datetime.now(UTC))


def test_independent_profiles_and_filters(db):
    first, second = setup_resumes(db)
    assert search_profile(db, first.id).skills == ["Python"]
    assert search_profile(db, second.id).skills == ["React"]
    assert search_profile(db, first.id).location == "Berlin"
    job = vacancy()
    filters = SearchFilters(title="engineer", skills="Python, SQL", work_mode="remote", salary="yes", posted_days=7)
    assert matches(job, filters, {"score": 1, "eligibility": "unknown"})
    assert not matches(job, filters.model_copy(update={"exclude": "SQL"}), {"score": 1, "eligibility": "unknown"})
    assert not matches(job.model_copy(update={"published_at": None}), filters, {"score": 1, "eligibility": "unknown"})
    assert not matches(job.model_copy(update={"published_at": datetime.now(UTC)-timedelta(days=10)}), filters, {"score": 1, "eligibility": "unknown"})
    s1 = SavedSearch(name="Python", resume_id=first.id, filters={}, enabled=True)
    s2 = SavedSearch(name="React", resume_id=second.id, filters={}, enabled=True)
    one, _ = make_results(db, s1, [job, job]); two, _ = make_results(db, s2, [job])
    assert len(one) == 1 and two == []  # A React-only resume cannot qualify for a Python role.


def test_search_polling_stays_available_during_sqlite_writer(tmp_path):
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    from jobpilot.db import Base
    engine = create_engine(f"sqlite:///{(tmp_path / 'polling.db').as_posix()}",
                           connect_args={"check_same_thread": False, "timeout": 0.1})
    Base.metadata.create_all(engine)
    def reader():
        with Session(engine) as connection:
            yield connection
    app.dependency_overrides[session] = reader
    try:
        with engine.connect() as writer:
            writer.execute(text("INSERT INTO companies (id, name, key, signals, careers_url, created_at) "
                                "VALUES ('test', 'Test', 'test', '[]', '', CURRENT_TIMESTAMP)"))
            # Keep the write transaction open, as search result persistence does.
            response = TestClient(app).get("/searches")
            assert response.status_code == 200
            assert response.json() == []
            writer.rollback()
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_search_crud_and_validation(db):
    first, second = setup_resumes(db)
    app.dependency_overrides[session] = lambda: db
    client = TestClient(app, headers={"Authorization": "Bearer " + settings().api_token.get_secret_value()})
    try:
        assert client.post("/searches", json={"name": "bad", "resume_id": "missing"}).status_code == 422
        response = client.post("/searches", json={"name": "Python", "resume_id": first.id, "filters": {"skills": "Python"}})
        assert response.status_code == 200
        search_id = response.json()["id"]
        s = db.get(SavedSearch, search_id); s.results = [{"title": "old"}]; s.result_count = 1; db.commit()
        response = client.put(f"/searches/{search_id}", json={"name": "React", "resume_id": second.id})
        assert response.status_code == 200 and response.json()["results"] == []
        assert client.delete(f"/searches/{search_id}").status_code == 200
        assert client.get("/searches").json() == []
    finally:
        app.dependency_overrides.clear()


def test_worker_stores_partial_failures_and_independent_results(db, monkeypatch):
    first, second = setup_resumes(db)
    db.add_all([Source(provider="ashby", board="ok", company="Acme"), Source(provider="ashby", board="bad", company="Bad")])
    s1 = SavedSearch(name="Python", resume_id=first.id, filters={"skills": "Python"}, enabled=True)
    s2 = SavedSearch(name="React", resume_id=second.id, filters={"title": "Designer"}, enabled=True)
    db.add_all([s1, s2]); db.commit()
    class FakeAdapter:
        def __init__(self, client): pass
        async def fetch(self, board, company):
            if board == "bad": raise RuntimeError("failure")
            return [vacancy()]
    class Context:
        def __enter__(self): return db
        def __exit__(self, *args): pass
    monkeypatch.setattr(workers, "SessionLocal", lambda: Context())
    monkeypatch.setitem(workers.ADAPTERS, "ashby", FakeAdapter)
    workers.run_saved_search.run(s1.id); workers.run_saved_search.run(s2.id)
    assert s1.status == "complete" and s1.result_count == 1 and "Bad" in s1.errors[0]
    assert s2.status == "complete" and s2.result_count == 0
    assert s1.completed_at and s2.completed_at


