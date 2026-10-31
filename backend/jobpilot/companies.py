"""Employer directory with dated evidence, kept separate from job-board connections."""
import asyncio
import re
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Company, Source, now

COUNTRIES = {
    "Armenia", "Azerbaijan", "Belarus", "Kazakhstan", "Kyrgyzstan", "Moldova",
    "Russia", "Tajikistan", "Turkmenistan", "Uzbekistan", "Georgia", "Ukraine",
}

GEORGIAN_CITIES = r"\b(tbilisi|batumi|kutaisi|rustavi|zugdidi|თბილისი|ბათუმი|ქუთაისი|тбилиси|батуми|кутаиси)\b"


def matches_country(location: str, country: str) -> bool:
    """GitHub location search is fuzzy; require evidence in the actual profile."""
    value = location.casefold().strip()
    if country == "Georgia":
        if re.search(r"\b(usa|us|united states|atlanta|savannah|augusta|athens|ga)\b", value):
            return False
        return bool(re.search(GEORGIAN_CITIES, value) or
                    re.search(r"\b(грузия|sakartvelo|country of georgia)\b|საქართველო", value))
    return bool(re.search(r"(?<!\w)" + re.escape(country.casefold()) + r"(?!\w)", value))


def employer_name(value: str) -> str:
    name = " ".join(value.strip().lstrip("@").split())
    if name.casefold() in {"none", "null", "n/a", "self", "self-employed", "freelance", "freelancer", "student", "unemployed"}:
        return ""
    if len(name) > 200 or re.search(r"https?://|[,;|/\n]", name):
        return ""
    return name

STARTERS = [
    ("GitLab", "https://about.gitlab.com/jobs/", "referrals",
     "https://handbook.gitlab.com/handbook/hiring/referral-process/",
     "Официальная программа рекомендаций сотрудников; условия зависят от роли."),
    ("JetBrains", "https://www.jetbrains.com/careers/", "regional_presence",
     "https://www.jetbrains.com/careers/",
     "Официальная страница карьеры указывает офис в Армении. Это региональное присутствие, не проверка происхождения сотрудников."),
    ("Wise", "https://wise.jobs/", "referrals", "https://wise.jobs/hyderabad-office",
     "Employee Referral Scheme указана на странице офиса в Хайдарабаде; доступность программы в других офисах нужно уточнять."),
    ("Atlassian", "https://www.atlassian.com/company/careers", "referrals",
     "https://join.atlassian.com/usreferralearlycareer/talentcommunity/form",
     "Официальная форма рекомендаций для US Early Career; это ограниченная программа, не обещание реферала для любой роли."),
]


def company_key(name: str) -> str:
    return " ".join(name.lstrip("@").split()).casefold()


def save_company(db: Session, name: str, careers_url: str = "", signal: dict[str, Any] | None = None) -> Company:
    name = " ".join(name.lstrip("@").split())[:200]
    key = company_key(name)
    company = db.scalar(select(Company).where(Company.key == key))
    if company is None:
        company = Company(name=name, key=key, careers_url=careers_url, signals=[])
        db.add(company)
        db.flush()
    elif careers_url and not company.careers_url:
        company.careers_url = careers_url
    if signal:
        signals = list(company.signals)
        existing = next((i for i, item in enumerate(signals)
                         if item["kind"] == signal["kind"] and item["url"] == signal["url"]), None)
        if existing is None:
            signals.append(signal)
        else:
            signals[existing] = signal
        company.signals = signals
    return company


def seed_companies(db: Session) -> int:
    added = 0
    for name, careers, kind, url, note in STARTERS:
        if not db.scalar(select(Company.id).where(Company.key == company_key(name))):
            added += 1
            save_company(db, name, careers, {"kind": kind, "url": url, "note": note,
                                           "location": "Armenia" if kind == "regional_presence" else "",
                                           "checked_at": "2026-10-31", "confidence": "official"})
    for source in db.scalars(select(Source)):
        save_company(db, source.company)
    return added


async def discover_employers(country: str, page: int = 1) -> tuple[list[dict[str, Any]], list[str]]:
    """Use self-reported public GitHub company/location fields; no nationality inference."""
    found: list[dict[str, Any]] = []
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=12, follow_redirects=False,
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "JobPilot-local"}) as client:
        response = await client.get("https://api.github.com/search/users", params={
            "q": ('location:Georgia type:user -location:USA -location:"United States" -location:Atlanta' if country == "Georgia"
                  else f'location:"{country}" type:user'), "per_page": 12, "page": page,
        })
        response.raise_for_status()
        # Only construct endpoints from validated public logins, never follow profile URLs.
        semaphore = asyncio.Semaphore(3)
        async def check(item: dict[str, Any]) -> None:
            login = item.get("login", "")
            if not login or len(login) > 39 or not all(c.isascii() and (c.isalnum() or c == "-") for c in login):
                return
            try:
                async with semaphore:
                    profile = await client.get(f"https://api.github.com/users/{login}")
                profile.raise_for_status()
                data = profile.json()
                location = str(data.get("location") or "")
                if not matches_country(location, country):
                    return
                name = employer_name(str(data.get("company") or ""))
                if name and company_key(name):
                    found.append({"name": name[:200], "signal": {
                        "kind": "cis_profile", "confidence": "self_reported",
                        "url": f"https://github.com/{login}", "location": location[:200], "country": country,
                        "note": "Компания и местоположение указаны владельцем публичного профиля. Занятость, гражданство и готовность дать реферал не проверены.",
                        "checked_at": now().date().isoformat(),
                    }})
            except httpx.HTTPError:
                errors.append(f"{login}: профиль недоступен или достигнут лимит GitHub")
        payload = response.json()
        if payload.get("incomplete_results"):
            errors.append("GitHub вернул неполные результаты; повторите запрос позже")
        await asyncio.gather(*(check(item) for item in payload.get("items", [])))
    return found, errors
