"""Validate researched public vacancies and score them locally before Telegram delivery."""
import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import SessionLocal, session
from .domain import JobInput
from .http import authenticate
from .models import LinkedInCandidate, SavedSearch, now
from .searches import SearchFilters, make_results
from .top_vacancies import MIN_SCORE, WINDOW_HOURS, save_top_vacancies

router = APIRouter(dependencies=[Depends(authenticate)])


def canonical_linkedin_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    host = parsed.hostname or ""
    match = re.fullmatch(r"/(?:comm/)?jobs/view/(?:[A-Za-z0-9_%+.,()–—-]+-)?(\d{5,20})/?", parsed.path)
    if (parsed.scheme != "https" or not (host == "linkedin.com" or host.endswith(".linkedin.com"))
            or parsed.username or parsed.password or parsed.port not in {None, 443} or not match):
        raise ValueError("Нужна HTTPS-ссылка LinkedIn на конкретную вакансию /jobs/view/…")
    return f"https://www.linkedin.com/jobs/view/{match.group(1)}"


class LinkedInJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical_url: str = Field(max_length=2000)
    title: str = Field(min_length=2, max_length=300)
    company: str = Field(min_length=1, max_length=200)
    location: str = Field(default="", max_length=400)
    description: str = Field(min_length=100, max_length=20000)
    salary: str | None = Field(default=None, max_length=500)
    published_at: datetime
    date_evidence: str = Field(min_length=10, max_length=500)
    verified_at: datetime

    @field_validator("canonical_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return canonical_linkedin_url(value)

    @field_validator("published_at", "verified_at")
    @classmethod
    def aware_date(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Укажите временную зону даты")
        return value.astimezone(UTC)


def candidate_id(url: str) -> str:
    return hashlib.sha256(canonical_linkedin_url(url).encode()).hexdigest()


def process_candidates(session_factory: Any = SessionLocal) -> None:
    # Each row is committed independently; a restart safely resumes remaining rows.
    with session_factory() as db:
        ids = list(db.scalars(select(LinkedInCandidate.id).where(LinkedInCandidate.status == "pending")
                              .order_by(LinkedInCandidate.created_at).limit(20)))
    for identity in ids:
        with session_factory() as db:
            row = db.scalar(select(LinkedInCandidate).where(LinkedInCandidate.id == identity,
                            LinkedInCandidate.status == "pending").with_for_update(skip_locked=True))
            if row is None:
                continue
            try:
                item = LinkedInJob.model_validate(row.data)
                current = now()
                if not (current - timedelta(hours=WINDOW_HOURS) <= item.published_at <= item.verified_at <= current
                        and item.verified_at >= current - timedelta(hours=24)):
                    row.status, row.result = "excluded", {"reason": "Публикация старше 72 часов, дата будущая или проверка устарела"}
                else:
                    job = JobInput(**item.model_dump(exclude={"verified_at", "date_evidence"}), provider="linkedin",
                                   external_id=item.canonical_url.rsplit("/", 1)[-1])
                    best: dict[str, Any] | None = None
                    best_search: SavedSearch | None = None
                    for search in db.scalars(select(SavedSearch).where(SavedSearch.enabled.is_(True))):
                        filters = SearchFilters.model_validate(search.filters).model_copy(update={
                            "provider": "", "posted_days": 0, "min_score": 0})
                        try:
                            results, _ = make_results(db, search, [job], filters_override=filters)
                        except ValueError:
                            continue
                        if results and (best is None or results[0]["score"] > best["score"]):
                            best, best_search = results[0], search
                    if best is not None and best_search is not None:
                        row.result = {"score": best["score"], "search_id": best_search.id,
                                      "date_evidence": item.date_evidence}
                        if best["score"] >= MIN_SCORE:
                            save_top_vacancies(db, best_search, [best])
                            from .notifications import enqueue_top_telegram
                            enqueue_top_telegram(db, best_search.id, [best])
                            row.status = "accepted"
                        else:
                            row.status = "below_threshold"
                    else:
                        row.status, row.result = "excluded", {"reason": "Нет подходящего активного поиска/резюме"}
            except ValueError:
                row.status, row.result = "invalid", {"reason": "Не хватает проверенных данных вакансии"}
            row.processed_at = now()
            db.commit()


@router.get("/linkedin/jobs/status")
def linkedin_jobs_status(db: Session = Depends(session)) -> dict[str, Any]:
    rows = db.execute(select(LinkedInCandidate.status, func.count()).group_by(LinkedInCandidate.status)).all()
    latest = db.scalar(select(func.max(LinkedInCandidate.processed_at)))
    return {"mode": "public_research", "counts": dict(rows), "last_processed_at": latest,
            "min_score": MIN_SCORE, "window_hours": WINDOW_HOURS}
