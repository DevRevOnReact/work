"""Independent saved searches over public employer feeds."""
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .domain import JobInput, Profile, evaluate
from .models import Candidate, Resume, SavedSearch


class SearchFilters(BaseModel):
    title: str = Field(default="", max_length=200)
    keywords: str = Field(default="", max_length=300)
    exclude: str = Field(default="", max_length=300)
    skills: str = Field(default="", max_length=300)
    skills_mode: Literal["all", "any"] = "all"
    keywords_mode: Literal["all", "any"] = "all"
    sort: Literal["score", "newest", "company"] = "score"
    company: str = Field(default="", max_length=200)
    salary_text: str = Field(default="", max_length=200)
    employment_type: str = Field(default="", max_length=100)
    seniority: str = Field(default="", max_length=100)
    location: str = Field(default="", max_length=200)
    work_mode: Literal["", "remote", "hybrid", "onsite"] = ""
    salary: Literal["", "yes", "no"] = ""
    provider: Literal["", "greenhouse", "ashby", "lever", "remotive", "arbeitnow"] = ""
    eligibility: Literal["", "eligible", "unknown", "rejected"] = ""
    min_score: int = Field(default=0, ge=0, le=100)
    posted_days: int = Field(default=0, ge=0, le=3650)


class SearchInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    resume_id: str
    filters: SearchFilters = Field(default_factory=SearchFilters)
    enabled: bool = True


def search_profile(db: Session, resume_id: str) -> Profile:
    candidate = db.get(Candidate, 1)
    resume = db.get(Resume, resume_id)
    if not candidate or not resume:
        raise ValueError("Резюме не найдено")
    record = candidate.facts.get("resumes", {}).get(resume_id)
    if not record or not record.get("text"):
        raise ValueError("Текст резюме недоступен; загрузите файл повторно")
    # Keep separately entered location and authorization, but use only the chosen
    # document's extracted experience and skills, never the active document's.
    from .resumes import extract_facts
    parsed_facts = record.get("facts")
    if not isinstance(parsed_facts, dict):
        parsed_facts, _ = extract_facts(record["text"])
    facts = {**candidate.facts, **parsed_facts,
             "location": candidate.facts.get("location", "") or parsed_facts.get("location", ""),
             "active_resume_id": resume_id,
             "resume_text": record["text"], "resume_filename": resume.filename,
             "resume_parse_warnings": record.get("warnings", []),
             "search_locations": candidate.facts.get("search_locations", [])}
    return Profile.model_validate(facts)


def phrases(value: str) -> list[str]:
    return [part.strip().casefold() for part in value.split(",") if part.strip()]


def skill_present(skill: str, text: str) -> bool:
    return bool(re.search(r"(?<!\w)" + re.escape(skill) + r"(?!\w)", text))


def work_mode(job: JobInput) -> str:
    location = job.location.casefold()
    # Mixed remote/hybrid locations should not silently become fully remote.
    for mode, terms in (("hybrid", ("hybrid", "гибрид")),
                        ("onsite", ("onsite", "on-site", "in office", "офис")),
                        ("remote", ("remote", "удален", "удалён"))):
        if any(term in location for term in terms):
            return mode
    description = job.description.casefold()
    for mode, description_terms in (("hybrid", ("this role is hybrid",)),
                        ("onsite", ("this role is onsite", "this role is on-site")),
                        ("remote", ("fully remote", "100% remote", "this role is remote"))):
        if any(term in description for term in description_terms):
            return mode
    return ""


def matches(job: JobInput, filters: SearchFilters, result: dict[str, Any] | None = None) -> bool:
    title = job.title.casefold()
    text = (job.title + " " + job.description).casefold()
    location = job.location.casefold()
    if phrases(filters.title) and not any(term in title for term in phrases(filters.title)):
        return False
    words = filters.keywords.casefold().split()
    if words and not (all if filters.keywords_mode == "all" else any)(word in text for word in words):
        return False
    if filters.exclude and any(term.strip().casefold() in text for term in filters.exclude.split(",") if term.strip()):
        return False
    skills = phrases(filters.skills)
    if skills and not (all if filters.skills_mode == "all" else any)(skill_present(term, text) for term in skills):
        return False
    if phrases(filters.company) and not any(term in job.company.casefold() for term in phrases(filters.company)):
        return False
    if filters.salary_text and filters.salary_text.strip().casefold() not in (job.salary or "").casefold():
        return False
    if filters.employment_type and filters.employment_type.strip().casefold() not in text:
        return False
    if filters.seniority and filters.seniority.strip().casefold() not in title:
        return False
    if phrases(filters.location) and not any(term in location for term in phrases(filters.location)):
        return False
    if filters.provider and filters.provider != job.provider:
        return False
    if filters.salary and bool(job.salary and job.salary.strip()) != (filters.salary == "yes"):
        return False
    if filters.work_mode:
        if work_mode(job) != filters.work_mode:
            return False
    if filters.posted_days:
        if not job.published_at:
            return False
        published = job.published_at.replace(tzinfo=UTC) if job.published_at.tzinfo is None else job.published_at
        if published < datetime.now(UTC) - timedelta(days=filters.posted_days):
            return False
    return result is None or ((filters.eligibility == "rejected" or
                              (result.get("recommendable", True) and result["eligibility"] != "rejected")) and
                             result["score"] >= filters.min_score and
                             (not filters.eligibility or result["eligibility"] == filters.eligibility))


def published_today(job: JobInput, at: datetime | None = None) -> bool:
    from .config import settings
    if not job.published_at:
        return False
    current = at or datetime.now(UTC)
    published = job.published_at.replace(tzinfo=UTC) if job.published_at.tzinfo is None else job.published_at
    zone = ZoneInfo(settings().search_timezone)
    return published <= current and published.astimezone(zone).date() == current.astimezone(zone).date()


def make_results(db: Session, search: SavedSearch, jobs: list[JobInput], *,
                 today_only: bool = False, at: datetime | None = None, since: datetime | None = None,
                 filters_override: SearchFilters | None = None, limit: int | None = 500) -> tuple[list[dict[str, Any]], int]:
    profile = search_profile(db, search.resume_id)
    from .feedback import adjust, preferences
    feedback_rules = preferences(db)
    filters = filters_override or SearchFilters.model_validate(search.filters)
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for job in jobs:
        if since:
            if not job.published_at:
                continue
            published = job.published_at.replace(tzinfo=UTC) if job.published_at.tzinfo is None else job.published_at
            if not since <= published <= (at or datetime.now(UTC)):
                continue
        if today_only and not published_today(job, at):
            continue
        if job.canonical_url in seen:
            continue
        seen.add(job.canonical_url)
        if not matches(job, filters):
            continue
        result = adjust(job, evaluate(job, profile), feedback_rules)
        if matches(job, filters, result):
            results.append({**job.model_dump(mode="json"), "score": result["score"],
                            "eligibility": result["eligibility"], "explanation": result})
    if filters.sort == "newest":
        results.sort(key=lambda item: (item["published_at"] or "", item["score"]), reverse=True)
    elif filters.sort == "company":
        results.sort(key=lambda item: (item["company"].casefold(), -item["score"], item["canonical_url"]))
    else:
        results.sort(key=lambda item: (-item["score"], item["company"].casefold(), item["title"].casefold()))
    return results[:limit] if limit is not None else results, len(results)
