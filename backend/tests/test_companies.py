import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from jobpilot.api import app
from jobpilot import companies, company_api
from jobpilot.companies import seed_companies
from jobpilot.db import session
from jobpilot.models import Company, ReferralLead, Source


@pytest.mark.parametrize("location,expected", [
    ("Atlanta, Georgia, US", False), ("Georgia, USA", False),
    ("Savannah, GA", False), ("Georgia", False), ("", False),
    ("Tbilisi, Georgia", True), ("Batumi", True), ("Грузия", True),
    ("საქართველო", True), ("Tbilisi, USA", False),
])
def test_georgia_requires_country_evidence(location, expected):
    assert companies.matches_country(location, "Georgia") is expected


@pytest.mark.parametrize("value", ["freelancer", "@self-employed", "https://example.com", "Acme, Other"])
def test_discovery_rejects_non_employers(value):
    assert companies.employer_name(value) == ""


@pytest.fixture
def client(db, monkeypatch):
    app.dependency_overrides[session] = lambda: db
    monkeypatch.setattr(company_api, "_last_discovery", -1000000)
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_catalog_has_scoped_evidence_and_keeps_legacy_contacts(db, client):
    db.add(Source(company="Acme", provider="ashby", board="acme"))
    db.add(ReferralLead(name="Contact", company="ACME", location="Armenia",
                        profile_url="https://www.linkedin.com/in/contact", notes="Existing"))
    db.commit()
    assert seed_companies(db) == 4
    db.commit()
    assert seed_companies(db) == 0
    items = client.get("/companies", params={"q": "acme"}).json()
    assert len(items) == 1 and len(items[0]["sources"]) == len(items[0]["contacts"]) == 1
    referrals = client.get("/companies?signal=referrals").json()
    assert len(referrals) == 3
    assert all(item["signals"][0]["confidence"] == "official" for item in referrals)
    assert client.get("/companies?signal=regional_presence").json()[0]["name"] == "JetBrains"


def test_discovery_saves_deduplicated_self_reported_evidence(db, client, monkeypatch):
    calls = []
    class Response:
        def __init__(self, data): self.data = data
        def raise_for_status(self): pass
        def json(self): return self.data
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, **kwargs):
            calls.append(url)
            if url.endswith("/search/users"):
                assert kwargs["params"]["q"] == 'location:"Kazakhstan" type:user'
                assert kwargs["params"]["page"] == 2
                return Response({"items": [{"login": "person"}, {"login": "second"}, {"login": "../bad"}]})
            return Response({"company": "@Example", "location": "Kazakhstan"})
    monkeypatch.setattr(companies.httpx, "AsyncClient", Client)
    result = client.post("/companies/discover", json={"country": "Kazakhstan", "page": 2})
    assert result.status_code == 200
    assert result.json()["added"] == result.json()["found"] == 1
    company = db.scalar(select(Company))
    assert company.name == "Example" and len(company.signals) == 2
    assert all(s["confidence"] == "self_reported" for s in company.signals)
    assert len(calls) == 3
    assert client.post("/companies/discover", json={"country": "Kazakhstan"}).status_code == 429
    assert client.post("/companies/discover", json={"country": '" invalid'}).status_code == 422


def test_discovery_reports_rate_limits_without_creating_fake_companies(db, client, monkeypatch):
    async def limited(*args):
        raise httpx.HTTPStatusError("rate limited", request=httpx.Request("GET", "https://api.github.com"),
                                    response=httpx.Response(403))
    monkeypatch.setattr(company_api, "discover_employers", limited)
    response = client.post("/companies/discover", json={"country": "Armenia"})
    assert response.status_code == 429
    assert response.json()["detail"] == "Лимит публичного API GitHub; повторите позже"
    assert list(db.scalars(select(Company))) == []


def test_georgia_discovery_does_not_save_us_or_ambiguous_profiles(db, client, monkeypatch):
    profiles = {
        "atlanta": {"company": "US employer", "location": "Atlanta, Georgia, US"},
        "ambiguous": {"company": "Unknown employer", "location": "Georgia"},
        "missing": {"company": "Missing location"},
        "tbilisi": {"company": "@Local employer", "location": "Tbilisi, Georgia"},
        "freelance": {"company": "freelancer", "location": "Tbilisi, Georgia"},
    }
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, **kwargs):
            data = ({"items": [{"login": login} for login in profiles]}
                    if url.endswith("/search/users") else profiles[url.rsplit("/", 1)[1]])
            return httpx.Response(200, json=data, request=httpx.Request("GET", url))
    monkeypatch.setattr(companies.httpx, "AsyncClient", Client)
    response = client.post("/companies/discover", json={"country": "Georgia"})
    assert response.status_code == 200
    assert response.json()["found"] == 1
    company = db.scalar(select(Company))
    assert company.name == "Local employer"
    assert company.signals[0]["country"] == "Georgia"
    assert company.signals[0]["note"].startswith("Компания и местоположение")


def test_manual_companies_validate_urls_and_normalize_duplicates(client):
    assert client.post("/companies", json={"name": "Acme", "careers_url": "javascript:alert(1)"}).status_code == 422
    assert client.post("/companies", json={"name": "   "}).status_code == 422
    first = client.post("/companies", json={"name": "Acme", "careers_url": "https://acme.example/jobs"}).json()
    second = client.post("/companies", json={"name": " ACME "}).json()
    assert first["id"] == second["id"]
