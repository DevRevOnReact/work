"""Persist high-scoring results independently from each search's daily snapshot."""
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .db import session
from .http import authenticate
from .models import Resume, SavedSearch, TopVacancy, now

WINDOW_HOURS = 72
MIN_SCORE = 60
router = APIRouter(dependencies=[Depends(authenticate)])


def publication(value: Any) -> datetime | None:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def qualifies(job: dict[str, Any], at: datetime | None = None) -> bool:
    current = at or now()
    published = publication(job.get("published_at"))
    return bool(published and current - timedelta(hours=WINDOW_HOURS) <= published <= current
                and job.get("score", 0) >= MIN_SCORE and job.get("canonical_url", "").startswith("https://"))


def save_top_vacancies(db: Session, search: SavedSearch, results: list[dict[str, Any]]) -> None:
    current = now()
    resume = db.get(Resume, search.resume_id)
    jobs = {hashlib.sha256(f"{search.resume_id}:{job['canonical_url']}".encode()).hexdigest(): job
            for job in results if qualifies(job, current)}
    existing = {item.id: item for item in db.scalars(select(TopVacancy).where(TopVacancy.id.in_(jobs)))}
    for key, job in jobs.items():
        row = existing.get(key)
        if row is None:
            row = TopVacancy(id=key, resume_id=search.resume_id)
            db.add(row)
        published = publication(job["published_at"])
        assert published is not None
        row.data, row.score, row.published_at = job, job["score"], published
        row.search_id, row.search_name = search.id, search.name
        row.resume_filename, row.updated_at = resume.filename if resume else "Резюме", current
    db.execute(delete(TopVacancy).where(TopVacancy.published_at < current - timedelta(hours=WINDOW_HOURS)))


def top_rows(db: Session, provider: str = "") -> list[TopVacancy]:
    current = now()
    query = select(TopVacancy).where(
        TopVacancy.score >= MIN_SCORE, TopVacancy.published_at >= current - timedelta(hours=WINDOW_HOURS),
        TopVacancy.published_at <= current,
    )
    if provider:
        query = query.where(TopVacancy.data["provider"].as_string() == provider)
    rows = db.scalars(query.order_by(TopVacancy.score.desc(), TopVacancy.published_at.desc(), TopVacancy.id))
    unique: dict[str, TopVacancy] = {}
    for row in rows:
        unique.setdefault(row.data["canonical_url"], row)
    return list(unique.values())


@router.get("/top-vacancies")
def top_vacancies(offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100),
                  provider: Literal["", "linkedin"] = "",
                  db: Session = Depends(session)) -> dict[str, Any]:
    rows = top_rows(db, provider)
    fields = ("company", "title", "location", "salary", "provider", "canonical_url", "published_at", "eligibility")
    return {"window_hours": WINDOW_HOURS, "min_score": MIN_SCORE, "total": len(rows),
            "items": [{**{key: row.data.get(key) for key in fields}, "id": row.id, "score": row.score,
                       "resume_filename": row.resume_filename, "search_name": row.search_name}
                      for row in rows[offset:offset + limit]]}
