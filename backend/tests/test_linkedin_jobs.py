from datetime import UTC, datetime, timedelta
import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from jobpilot import linkedin_jobs, searches
from jobpilot.api import app
from jobpilot.config import settings
from jobpilot.db import session
from jobpilot.linkedin_jobs import LinkedInJob, candidate_id, canonical_linkedin_url, process_candidates
from jobpilot.models import LinkedInCandidate, SavedSearch, SearchNotification, TopVacancy, now
from test_notifications import enable
from test_saved_searches import setup_resumes


def record(**updates):
    return {"canonical_url": "https://www.linkedin.com/jobs/view/frontend-engineer-at-example-1234567890?trackingId=ignore",
            "title": "Frontend Engineer", "company": "Example", "location": "Remote Worldwide",
            "description": "React TypeScript frontend engineer. 5 years experience. " * 4,
            "published_at": (now()-timedelta(hours=20)).isoformat(), "date_evidence": "datePosted: absolute ISO date from the public posting",
            "verified_at": now().isoformat(), **updates}


@pytest.mark.parametrize('url', [
    'http://www.linkedin.com/jobs/view/1234567890', 'https://linkedin.com.evil.example/jobs/view/1234567890',
    'https://www.linkedin.com/jobs/search/', 'https://user:password@www.linkedin.com/jobs/view/1234567890',
    'https://www.linkedin.com:444/jobs/view/1234567890',
])
def test_url_validation_rejects_non_job_and_untrusted_urls(url):
    with pytest.raises(ValueError): canonical_linkedin_url(url)


def test_job_links_normalize_across_tracking_and_locale():
    assert canonical_linkedin_url(record()['canonical_url']) == 'https://www.linkedin.com/jobs/view/1234567890'
    assert candidate_id('https://de.linkedin.com/comm/jobs/view/1234567890?foo=bar') == candidate_id(record()['canonical_url'])
    with pytest.raises(ValueError): LinkedInJob.model_validate(record(score=99))
    with pytest.raises(ValueError): LinkedInJob.model_validate(record(published_at='2026-10-31T10:00:00'))


@pytest.mark.parametrize("relative", [False, True])
def test_valid_research_is_scored_and_sent_with_description_once(db, monkeypatch, relative):
    first, second = setup_resumes(db)
    db.add_all([SavedSearch(name='Python', resume_id=first.id, filters={}), SavedSearch(name='React', resume_id=second.id, filters={})])
    data = record(**({"published_at": None, "publication_label": "3 hours ago"} if relative else {}))
    row = LinkedInCandidate(id=candidate_id(data['canonical_url']), data=data)
    db.add(row); db.commit()
    enable(monkeypatch)
    monkeypatch.setattr(searches, 'evaluate', lambda job, profile: {'score': 85 if 'React' in profile.skills else 65, 'eligibility': 'eligible'})
    factory = sessionmaker(db.bind, expire_on_commit=False)
    process_candidates(factory); process_candidates(factory)
    db.refresh(row)
    assert row.status == 'accepted' and row.result['score'] == 85
    top = list(db.scalars(select(TopVacancy)))
    alerts = list(db.scalars(select(SearchNotification)))
    assert len(top) == len(alerts) == 1
    assert top[0].resume_id == second.id and top[0].data['provider'] == 'linkedin'
    if relative:
        assert 'время оценочное' in alerts[0].message
        assert top[0].data['publication_label'] == '3 hours ago'
    assert 'React TypeScript' in alerts[0].message and 'https://www.linkedin.com/jobs/view/1234567890' in alerts[0].message
    app.dependency_overrides[session] = lambda: db
    try:
        client = TestClient(app)
        assert client.get('/top-vacancies?provider=linkedin').json()['total'] == 1
        assert client.get('/linkedin/jobs/status').json()['counts']['accepted'] == 1
        assert client.get('/top-vacancies?provider=unknown').status_code == 422
    finally: app.dependency_overrides.clear()


@pytest.mark.parametrize('change,expected', [
    ({'published_at': (now()-timedelta(days=4)).isoformat()}, 'excluded'),
    ({'published_at': (now()+timedelta(days=1)).isoformat()}, 'excluded'),
    ({'verified_at': (now()-timedelta(days=2)).isoformat()}, 'excluded'),
    ({'published_at': None}, 'invalid'),
    ({'description': 'Title only'}, 'invalid'),
])
def test_unverified_stale_and_incomplete_records_never_notify(db, monkeypatch, change, expected):
    enable(monkeypatch)
    data = record(**change)
    row = LinkedInCandidate(id=candidate_id(data['canonical_url']), data=data)
    db.add(row); db.commit()
    process_candidates(sessionmaker(db.bind, expire_on_commit=False))
    db.refresh(row)
    assert row.status == expected
    assert list(db.scalars(select(SearchNotification))) == []
    assert list(db.scalars(select(TopVacancy))) == []


def test_low_score_does_not_inflate_to_threshold(db, monkeypatch):
    first, _ = setup_resumes(db)
    db.add(SavedSearch(name='Test', resume_id=first.id, filters={}))
    data = record(); row = LinkedInCandidate(id=candidate_id(data['canonical_url']), data=data)
    db.add(row); db.commit()
    monkeypatch.setattr(searches, 'evaluate', lambda *args: {'score': 59, 'eligibility': 'unknown'})
    process_candidates(sessionmaker(db.bind, expire_on_commit=False)); db.refresh(row)
    assert row.status == 'below_threshold' and row.result['score'] == 59
    assert list(db.scalars(select(TopVacancy))) == []


def test_preparation_emits_only_encoded_data_and_rejects_old_dates():
    path = Path(__file__).resolve().parents[2] / 'scripts/prepare_linkedin_candidate.py'
    spec = importlib.util.spec_from_file_location('prepare_linkedin_candidate', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    sql = module.prepare(record(company="Example'); DROP TABLE candidates; --"))
    assert 'DROP TABLE' not in sql and sql.count(';') == 1
    assert 'ON CONFLICT (id) DO NOTHING' in sql
    with pytest.raises(ValueError): module.prepare(record(published_at=(now()-timedelta(days=5)).isoformat()))


def test_research_status_is_private(db, monkeypatch):
    monkeypatch.setattr(settings(), 'auth_required', True)
    app.dependency_overrides[session] = lambda: db
    try: assert TestClient(app).get('/linkedin/jobs/status').status_code == 401
    finally: app.dependency_overrides.clear()


@pytest.mark.parametrize("label,delta", [("3 hours ago", timedelta(hours=4, minutes=5)),
    ("1 day ago", timedelta(days=2, minutes=5)), ("Reposted 5 minutes ago", timedelta(minutes=11))])
def test_live_relative_labels_use_conservative_bound(label, delta):
    data = record(published_at=None, publication_label=label)
    item = LinkedInJob.model_validate(data)
    assert item.published_at == item.verified_at - delta
    assert item.model_dump(mode="json")["publication_label"] == label


@pytest.mark.parametrize("label", ["2 days ago", "3 days ago", "1 week ago", "yesterday", "9999 hours ago"])
def test_ambiguous_or_old_relative_labels_are_excluded(label):
    with pytest.raises(ValueError):
        LinkedInJob.model_validate(record(published_at=None, publication_label=label))


def test_relative_label_cannot_override_timestamp():
    with pytest.raises(ValueError):
        LinkedInJob.model_validate(record(publication_label="3 hours ago"))
