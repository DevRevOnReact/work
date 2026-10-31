"""Employer directory with dated evidence, kept separate from job-board connections."""
import asyncio
import re
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Company, Source, now

COUNTRY_LABELS = {
    "Worldwide": "Весь мир · IT-компании",
    "United States": "США", "Canada": "Канада", "United Kingdom": "Великобритания",
    "Germany": "Германия", "France": "Франция", "Netherlands": "Нидерланды",
    "Ireland": "Ирландия", "Switzerland": "Швейцария", "Austria": "Австрия",
    "Belgium": "Бельгия", "Luxembourg": "Люксембург", "Sweden": "Швеция",
    "Norway": "Норвегия", "Denmark": "Дания", "Finland": "Финляндия", "Iceland": "Исландия",
    "Spain": "Испания", "Portugal": "Португалия", "Italy": "Италия", "Greece": "Греция",
    "Poland": "Польша", "Czechia": "Чехия", "Estonia": "Эстония", "Latvia": "Латвия",
    "Lithuania": "Литва", "Romania": "Румыния", "Bulgaria": "Болгария",
    "Croatia": "Хорватия", "Slovenia": "Словения", "Slovakia": "Словакия", "Serbia": "Сербия",
    "Malta": "Мальта", "Cyprus": "Кипр", "Australia": "Австралия", "New Zealand": "Новая Зеландия",
    "Singapore": "Сингапур", "Japan": "Япония", "South Korea": "Южная Корея",
    "Taiwan": "Тайвань", "Hong Kong": "Гонконг", "Israel": "Израиль", "India": "Индия",
    "China": "Китай", "United Arab Emirates": "ОАЭ", "Turkey": "Турция",
    "Brazil": "Бразилия", "Mexico": "Мексика", "Argentina": "Аргентина",
    "Chile": "Чили", "South Africa": "ЮАР",
    "Armenia": "Армения", "Azerbaijan": "Азербайджан", "Belarus": "Беларусь",
    "Kazakhstan": "Казахстан", "Kyrgyzstan": "Кыргызстан", "Moldova": "Молдова",
    "Russia": "Россия", "Tajikistan": "Таджикистан", "Turkmenistan": "Туркменистан",
    "Uzbekistan": "Узбекистан", "Georgia": "Грузия", "Ukraine": "Украина",
}
COUNTRIES = set(COUNTRY_LABELS)
CIS_COUNTRIES = {
    "Armenia", "Azerbaijan", "Belarus", "Kazakhstan", "Kyrgyzstan", "Moldova",
    "Russia", "Tajikistan", "Turkmenistan", "Uzbekistan", "Georgia", "Ukraine",
}

LOCATION_ALIASES = {
    "United States": ["USA", "US", "New York", "San Francisco", "Seattle", "Boston", "Austin"],
    "United Kingdom": ["UK", "London", "Cambridge", "Manchester", "Edinburgh"],
    "Germany": ["Deutschland", "Berlin", "Munich", "München", "Hamburg"],
    "Canada": ["Toronto", "Vancouver", "Montreal", "Montréal", "Ottawa"],
    "France": ["Paris", "Lyon"], "Netherlands": ["Amsterdam", "Rotterdam"],
    "Australia": ["Sydney", "Melbourne", "Brisbane"], "Japan": ["Tokyo", "Osaka", "日本"],
    "Czechia": ["Czech Republic", "Prague", "Praha"], "South Korea": ["Korea", "Seoul", "한국"],
    "United Arab Emirates": ["UAE", "Dubai", "Abu Dhabi"],
    "India": ["Bengaluru", "Bangalore", "Hyderabad", "Mumbai", "New Delhi"],
    "Poland": ["Warsaw", "Warszawa", "Krakow", "Kraków"], "Israel": ["Tel Aviv"],
    "Sweden": ["Stockholm"], "Ireland": ["Dublin"], "Spain": ["Madrid", "Barcelona"],
    "Portugal": ["Lisbon", "Lisboa", "Porto"], "Finland": ["Helsinki"],
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
    names = [country, COUNTRY_LABELS.get(country, country), *LOCATION_ALIASES.get(country, [])]
    return any(re.search(r"(?<!\w)" + re.escape(name.casefold()) + r"(?!\w)", value) for name in names)


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


async def discover_employers(country: str, page: int = 1,
                             progress: Callable[..., None] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Use self-reported public GitHub company/location fields; no nationality inference."""
    found: list[dict[str, Any]] = []
    errors: list[str] = []
    report = progress or (lambda *args, **kwargs: None)
    report("profiles", f"Запрашиваем публичные профили GitHub: {COUNTRY_LABELS.get(country, country)}")
    async with httpx.AsyncClient(timeout=12, follow_redirects=False,
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "JobPilot-local"}) as client:
        response = await client.get("https://api.github.com/search/users", params={
            "q": ('location:Georgia type:user -location:USA -location:"United States" -location:Atlanta' if country == "Georgia"
                  else f'location:"{country}" type:user'), "per_page": 12, "page": page,
        })
        response.raise_for_status()
        # Only construct endpoints from validated public logins, never follow profile URLs.
        semaphore = asyncio.Semaphore(3)
        done = 0
        async def check(item: dict[str, Any]) -> None:
            nonlocal done
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
                        "kind": "cis_profile" if country in CIS_COUNTRIES else "github_profile", "confidence": "self_reported",
                        "url": f"https://github.com/{login}", "location": location[:200], "country": country,
                        "note": "Компания и местоположение указаны владельцем публичного профиля. Занятость, гражданство и готовность дать реферал не проверены.",
                        "checked_at": now().date().isoformat(),
                    }})
            except httpx.HTTPError:
                errors.append(f"{login}: профиль недоступен или достигнут лимит GitHub")
                report("profiles", errors[-1], level="error")
            finally:
                done += 1
                report("profiles", f"Проверено профилей: {done}; найдено работодателей: {len(found)}",
                       done=done, found=len(found))
        payload = response.json()
        if payload.get("incomplete_results"):
            errors.append("GitHub вернул неполные результаты; повторите запрос позже")
        report("profiles", "Проверяем компании и местоположение в профилях", total=len(payload.get("items", [])), done=0)
        await asyncio.gather(*(check(item) for item in payload.get("items", [])))
    return found, errors


def board_url(provider: str, board: str) -> str:
    return {"ashby": f"https://jobs.ashbyhq.com/{board}",
            "greenhouse": f"https://boards.greenhouse.io/{board}",
            "lever": f"https://jobs.lever.co/{board}"}.get(provider, "")


def discover_global_catalog(db: Session, progress: Callable[..., None]) -> dict[str, Any]:
    from .sources import STARTER_SOURCES
    catalog = [item for item in STARTER_SOURCES if item["provider"] in {"ashby", "greenhouse", "lever"}]
    added = connected = 0
    progress("catalog", f"Подключаем мировой каталог IT-компаний: {len(catalog)}", total=len(catalog), done=0)
    for index, item in enumerate(catalog):
        if not db.scalar(select(Company.id).where(Company.key == company_key(item["company"]))):
            added += 1
        save_company(db, item["company"], board_url(item["provider"], item["board"]), {
            "kind": "it_catalog", "url": board_url(item["provider"], item["board"]),
            "note": "IT-работодатель из каталога публичных карьерных страниц. Наличие программы рефералов требует проверки.",
            "location": "Весь мир", "checked_at": now().date().isoformat(), "confidence": "catalog",
        })
        if not db.scalar(select(Source.id).where(Source.provider == item["provider"], Source.board == item["board"])):
            db.add(Source(**item))
            connected += 1
        # Commit before progress writes to keep SQLite pollable.
        if (index + 1) % 10 == 0 or index + 1 == len(catalog):
            db.commit()
            progress("catalog", f"Обработано IT-компаний: {index + 1}; новых: {added}", done=index + 1)
    return {"added": added, "found": len(catalog), "connected": connected, "errors": [], "next_page": None}
