import html
import json
from pathlib import Path
import re
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import httpx
from bs4 import BeautifulSoup
from .domain import JobInput


def plain(value: str) -> str:
    return BeautifulSoup(html.unescape(value), "html.parser").get_text(" ", strip=True)


def canonical(url: str) -> str:
    p = urlsplit(url)
    query = [
        (k, v)
        for k, v in parse_qsl(p.query)
        if not k.startswith("utm_") and k not in {"source", "ref", "referrer"}
    ]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), urlencode(sorted(query)), ""))


def date(value: str | int | None) -> datetime | None:
    if not value:
        return None
    if isinstance(value, int):
        return datetime.fromtimestamp(value / 1000, timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class SourceAdapter(Protocol):
    async def fetch(self, board: str, company: str) -> list[JobInput]: ...


class PublicFeed:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def get(self, url: str) -> Any:
        response = await self.client.get(url, timeout=30)
        # Respect 429: Celery backoff handles retry; no rapid retries or proxy rotation.
        response.raise_for_status()
        return response.json()

    def validate(self, board: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", board):
            raise ValueError("Use a board slug, not a URL")


class GreenhouseSource(PublicFeed):
    async def fetch(self, board: str, company: str) -> list[JobInput]:
        self.validate(board)
        data = await self.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
        return [
            JobInput(
                provider="greenhouse",
                external_id=f"{board}:{j['id']}",
                canonical_url=canonical(j["absolute_url"]),
                company=company,
                title=j["title"],
                location=j.get("location", {}).get("name", ""),
                description=plain(j.get("content", "")),
                # updated_at is NOT publication time. Never pretend old jobs are fresh.
                published_at=date(j.get("first_published")),
            )
            for j in data["jobs"]
        ]


class AshbySource(PublicFeed):
    async def fetch(self, board: str, company: str) -> list[JobInput]:
        self.validate(board)
        data = await self.get(
            f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"
        )
        return [
            JobInput(
                provider="ashby",
                external_id=f"{board}:{j['jobUrl'].rstrip('/').split('/')[-1]}",
                canonical_url=canonical(j["applyUrl"]),
                company=company,
                title=j["title"],
                location=("Remote · " if j.get("isRemote") else (j.get("workplaceType") or "") + " · ")
                + (j.get("location") or ""),
                description=j.get("descriptionPlain") or plain(j.get("descriptionHtml") or ""),
                salary=(j.get("compensation") or {}).get("compensationTierSummary"),
                published_at=date(j.get("publishedAt")),
            )
            for j in data["jobs"]
            if j.get("isListed", True)
        ]


class LeverSource(PublicFeed):
    async def fetch(self, board: str, company: str) -> list[JobInput]:
        self.validate(board)
        output: list[JobInput] = []
        skip = 0
        while True:
            data = await self.get(f"https://api.lever.co/v0/postings/{board}?mode=json&limit=100&skip={skip}")
            for j in data:
                description = j.get("descriptionPlain") or plain(j.get("description", ""))
                description += " " + " ".join(
                    plain(x.get("text", "") + " " + x.get("content", "")) for x in j.get("lists", [])
                )
                description += " " + plain(j.get("additional", ""))
                salary = j.get("salaryRange") or {}
                output.append(
                    JobInput(
                        provider="lever",
                        external_id=f"{board}:{j['id']}",
                        canonical_url=canonical(j["applyUrl"]),
                        company=company,
                        title=j["text"],
                        location=f"{j.get('workplaceType', '')} · {j.get('categories', {}).get('location', '')}",
                        description=description,
                        salary=f"{salary.get('min')}–{salary.get('max')} {salary.get('currency')} / {salary.get('interval')}"
                        if salary
                        else None,
                        published_at=date(j.get("createdAt")),
                    )
                )
            if len(data) < 100:
                return output
            skip += 100
            if skip > 10000:
                raise ValueError("Feed pagination exceeded safety bound")


class RemotiveSource(PublicFeed):
    async def fetch(self, board: str, company: str) -> list[JobInput]:
        # One shared request across searches; six-hour disk cache survives restarts.
        from .config import settings
        from time import time
        cache = settings().data_dir / "remotive-feed.json"
        data = None
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if time() - cached["fetched_at"] < 6 * 3600:
                data = cached["data"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if data is None:
            data = await self.get("https://remotive.com/api/remote-jobs")
            cache.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache.with_suffix(".tmp")
            temporary.write_text(json.dumps({"fetched_at": time(), "data": data}), encoding="utf-8")
            temporary.replace(cache)
        return [
            JobInput(provider="remotive", external_id=str(j["id"]), canonical_url=canonical(j["url"]),
                     company=j["company_name"], title=j["title"],
                     location="Remote · " + (j.get("candidate_required_location") or "Location unspecified"),
                     description="Source: Remotive. " + plain(j.get("description", "")) + " " + (j.get("job_type") or ""),
                     salary=j.get("salary") or None, published_at=date(j.get("publication_date")))
            for j in data["jobs"]
        ]


class ArbeitnowSource(PublicFeed):
    async def fetch(self, board: str, company: str) -> list[JobInput]:
        output = []
        # A bounded set of pages from a fixed host; never follow untrusted next URLs.
        for page in range(1, 6):
            data = await self.get(f"https://www.arbeitnow.com/api/job-board-api?page={page}")
            for j in data["data"]:
                output.append(JobInput(
                    provider="arbeitnow", external_id=j["slug"], canonical_url=canonical(j["url"]),
                    company=j["company_name"], title=j["title"],
                    location=("Remote · " if j.get("remote") else "") + (j.get("location") or ""),
                    description=plain(j.get("description", "")) + " " + " ".join(j.get("job_types") or []),
                    published_at=datetime.fromtimestamp(j["created_at"], timezone.utc) if j.get("created_at") else None,
                ))
            if not data.get("links", {}).get("next"):
                break
        return output


ADAPTERS: dict[str, type[GreenhouseSource | AshbySource | LeverSource | RemotiveSource | ArbeitnowSource]] = {
    "greenhouse": GreenhouseSource,
    "ashby": AshbySource,
    "lever": LeverSource,
    "remotive": RemotiveSource,
    "arbeitnow": ArbeitnowSource,
}

# Public employer boards, not an internet-wide search index. These are high-
# signal technology employers with public feeds on supported ATS platforms.
# Users can add any supported employer board and disable individual suggestions.
STARTER_SOURCES = [
    # Greenhouse
    {"provider": "greenhouse", "board": "stripe", "company": "Stripe"},
    {"provider": "greenhouse", "board": "figma", "company": "Figma"},
    {"provider": "greenhouse", "board": "datadog", "company": "Datadog"},
    {"provider": "greenhouse", "board": "airbnb", "company": "Airbnb"},
    {"provider": "greenhouse", "board": "cloudflare", "company": "Cloudflare"},
    {"provider": "greenhouse", "board": "reddit", "company": "Reddit"},
    {"provider": "greenhouse", "board": "discord", "company": "Discord"},
    {"provider": "greenhouse", "board": "coinbase", "company": "Coinbase"},
    {"provider": "greenhouse", "board": "duolingo", "company": "Duolingo"},
    {"provider": "greenhouse", "board": "dropbox", "company": "Dropbox"},
    {"provider": "greenhouse", "board": "twilio", "company": "Twilio"},
    {"provider": "greenhouse", "board": "instacart", "company": "Instacart"},
    {"provider": "greenhouse", "board": "lyft", "company": "Lyft"},
    {"provider": "greenhouse", "board": "pinterest", "company": "Pinterest"},
    {"provider": "greenhouse", "board": "okta", "company": "Okta"},
    {"provider": "greenhouse", "board": "mongodb", "company": "MongoDB"},
    {"provider": "greenhouse", "board": "elastic", "company": "Elastic"},
    {"provider": "greenhouse", "board": "brex", "company": "Brex"},
    {"provider": "greenhouse", "board": "vercel", "company": "Vercel"},
    {"provider": "greenhouse", "board": "scaleai", "company": "Scale AI"},
    {"provider": "greenhouse", "board": "waymo", "company": "Waymo"},
    {"provider": "greenhouse", "board": "doordashusa", "company": "DoorDash"},
    {"provider": "greenhouse", "board": "asana", "company": "Asana"},
    # Ashby
    {"provider": "ashby", "board": "openai", "company": "OpenAI"},
    {"provider": "ashby", "board": "anthropic", "company": "Anthropic"},
    {"provider": "ashby", "board": "ramp", "company": "Ramp"},
    {"provider": "ashby", "board": "notion", "company": "Notion"},
    {"provider": "ashby", "board": "linear", "company": "Linear"},
    {"provider": "ashby", "board": "perplexity", "company": "Perplexity"},
    {"provider": "ashby", "board": "cursor", "company": "Cursor"},
    {"provider": "ashby", "board": "replit", "company": "Replit"},
    {"provider": "ashby", "board": "cerebras", "company": "Cerebras"},
    {"provider": "ashby", "board": "deel", "company": "Deel"},
    {"provider": "ashby", "board": "mercury", "company": "Mercury"},
    {"provider": "ashby", "board": "vanta", "company": "Vanta"},
    {"provider": "ashby", "board": "modal", "company": "Modal"},
    {"provider": "ashby", "board": "elevenlabs", "company": "ElevenLabs"},
    {"provider": "ashby", "board": "suno", "company": "Suno"},
    # Lever
    {"provider": "greenhouse", "board": "gitlab", "company": "GitLab"},
    {"provider": "ashby", "board": "ashby", "company": "Ashby"},
    {"provider": "lever", "board": "palantir", "company": "Palantir"},
    {"provider": "lever", "board": "flexport", "company": "Flexport"},
    {"provider": "lever", "board": "robinhood", "company": "Robinhood"},
    {"provider": "lever", "board": "foursquare", "company": "Foursquare"},
    {"provider": "lever", "board": "mixpanel", "company": "Mixpanel"},
]

# The verified catalog is generated by scripts/load_global_sources.py.
_catalog = Path(__file__).with_name("starter_catalog.json")
if _catalog.exists():
    STARTER_SOURCES = json.loads(_catalog.read_text(encoding="utf-8"))
STARTER_SOURCES += [
    {"provider": "remotive", "board": "all", "company": "Remotive · международные удалённые вакансии"},
    {"provider": "arbeitnow", "board": "all", "company": "Arbeitnow · компании Европы"},
]
