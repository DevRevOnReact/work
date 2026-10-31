import httpx
import pytest
from jobpilot.sources import GreenhouseSource, AshbySource, LeverSource, RemotiveSource, ArbeitnowSource, canonical


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,payload",
    [
        (
            GreenhouseSource,
            {
                "jobs": [
                    {
                        "id": 1,
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                        "title": "Frontend Engineer",
                        "location": {"name": "Remote"},
                        "content": "&lt;p&gt;React&lt;/p&gt;",
                        "updated_at": "2026-10-15T10:00:00Z",
                    }
                ]
            },
        ),
        (
            AshbySource,
            {
                "jobs": [
                    {
                        "jobUrl": "https://jobs.ashbyhq.com/acme/1",
                        "applyUrl": "https://jobs.ashbyhq.com/acme/1/application",
                        "title": "Frontend Engineer",
                        "location": "Worldwide",
                        "isRemote": True,
                        "descriptionPlain": "React",
                        "publishedAt": "2026-10-15T10:00:00Z",
                    }
                ]
            },
        ),
        (
            LeverSource,
            [
                {
                    "id": "1",
                    "applyUrl": "https://jobs.lever.co/acme/1/apply",
                    "text": "Frontend Engineer",
                    "descriptionPlain": "React",
                    "categories": {"location": "Worldwide"},
                    "workplaceType": "remote",
                }
            ],
        ),
    ],
)
async def test_real_feed_shapes(provider, payload):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
    ) as client:
        jobs = await provider(client).fetch("acme", "Acme")
    assert len(jobs) == 1
    assert jobs[0].description.strip() == "React"
    assert jobs[0].company == "Acme"
    if provider is GreenhouseSource:
        assert jobs[0].published_at is None


async def test_rate_limit_is_not_bypassed():
    calls = []

    def respond(req):
        calls.append(req)
        return httpx.Response(429, headers={"Retry-After": "60"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await GreenhouseSource(client).fetch("acme", "Acme")
    assert len(calls) == 1


def test_canonical_strips_tracking_not_identity():
    assert (
        canonical("https://jobs.lever.co/acme/1/?utm_source=foo&gh_jid=3#apply")
        == "https://jobs.lever.co/acme/1?gh_jid=3"
    )


async def test_greenhouse_http_links_fall_back_to_same_job_on_official_https_board():
    payload = {"jobs": [{"id": 123, "absolute_url": "http://www.circleci.com/careers/jobs/123?gh_jid=123",
                         "title": "Engineer", "location": {"name": "Remote"}, "content": "Python"}]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload))) as client:
        jobs = await GreenhouseSource(client).fetch("circleci", "CircleCI")
    assert jobs[0].canonical_url == "https://boards.greenhouse.io/circleci/jobs/123"


async def test_remotive_keeps_geographic_restrictions_attribution_and_disk_cache(tmp_path, monkeypatch):
    from jobpilot.config import settings
    monkeypatch.setattr(settings(), "data_dir", tmp_path)
    calls = []
    def respond(req):
        calls.append(req)
        return httpx.Response(200, json={"jobs": [{
            "id": 9, "url": "https://remotive.com/remote-jobs/software-dev/test-9",
            "company_name": "Example", "title": "Frontend Engineer",
            "candidate_required_location": "United States", "description": "<p>Vue TypeScript</p>",
            "publication_date": "2026-10-30T10:00:00", "job_type": "full_time",
        }]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        first = await RemotiveSource(client).fetch("all", "Remotive")
        second = await RemotiveSource(client).fetch("all", "Remotive")
    assert len(calls) == 1
    assert first == second
    assert first[0].location == "Remote · United States"
    assert first[0].company == "Example"
    assert first[0].provider == "remotive"
    assert "Source: Remotive" in first[0].description
    assert first[0].published_at.year == 2026


async def test_arbeitnow_uses_fixed_host_and_bounded_pages():
    calls = []
    def respond(req):
        calls.append(req)
        return httpx.Response(200, json={"data": [{
            "slug": str(req.url), "company_name": "Example", "title": "Frontend Engineer",
            "url": "https://www.arbeitnow.com/jobs/test", "location": "Berlin",
            "remote": True, "description": "<p>React</p>", "created_at": 1793354400,
        }], "links": {"next": "https://untrusted.example/next"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        jobs = await ArbeitnowSource(client).fetch("all", "Arbeitnow")
    assert len(calls) == len(jobs) == 5
    assert all(req.url.host == "www.arbeitnow.com" for req in calls)
    assert jobs[0].location == "Remote · Berlin"
    assert jobs[0].published_at is not None
