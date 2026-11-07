"""Persist high-scoring results independently from each search's daily snapshot."""
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Select, case, delete, or_, select
from sqlalchemy.orm import Session

from .db import session
from .http import authenticate
from .models import Resume, SavedSearch, TopVacancy, now
from .vacancy_identity import canonical_url, identity, same_vacancy

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
                and job.get("score", 0) >= MIN_SCORE
                and (job.get("eligibility") == "eligible" or
                     (job.get("eligibility") == "unknown" and job.get("review_status") == "approved"))
                and job.get("explanation", {}).get("recommendable", True)
                and job.get("canonical_url", "").startswith("https://"))


def save_top_vacancies(db: Session, search: SavedSearch, results: list[dict[str, Any]]) -> None:
    from .vacancy_review import apply_decisions
    results = apply_decisions(db, results)
    current = now()
    resume = db.get(Resume, search.resume_id)
    jobs = {hashlib.sha256(f"{search.resume_id}:{job['canonical_url']}".encode()).hexdigest(): job
            for job in results if qualifies(job, current)}
    existing = {item.id: item for item in db.scalars(select(TopVacancy).where(TopVacancy.id.in_(jobs)))}
    saved = list(db.scalars(select(TopVacancy).where(TopVacancy.favorite.is_(True))))
    for key, job in jobs.items():
        row = existing.get(key)
        if row is None:
            row = TopVacancy(id=key, resume_id=search.resume_id)
            db.add(row)
        published = publication(job["published_at"])
        assert published is not None
        row.data, row.score, row.published_at = job, job["score"], published
        if any(same_vacancy(peer.data, job) for peer in saved):
            row.favorite = True
        row.search_id, row.search_name = search.id, search.name
        row.resume_filename, row.updated_at = resume.filename if resume else "Резюме", current
    db.execute(delete(TopVacancy).where(TopVacancy.favorite.is_(False),
                                       TopVacancy.published_at < current - timedelta(hours=WINDOW_HOURS))
               .execution_options(synchronize_session="fetch"))


def normalized_url(value: str) -> tuple[str, str, str]:
    url = urlsplit(canonical_url(value))
    return url.netloc, url.path, url.query


def vacancy_identity(job: dict[str, Any]) -> tuple[str, ...]:
    return (identity(job),)


def top_rows(db: Session, provider: str = "", *, favorites: bool = False, summary: bool = False) -> list[TopVacancy]:
    current = now()
    fields = ("company", "title", "location", "salary", "provider", "canonical_url", "published_at",
              "publication_label", "eligibility", "review_status", "score")
    metadata = [column for column in TopVacancy.__table__.columns if column.name != "data"]
    query: Select[Any] = select(TopVacancy)
    if summary:
        query = select(*metadata, *(TopVacancy.data[key].label("data_" + key) for key in fields),
            TopVacancy.data["explanation"]["recommendable"].label("recommendable"),
            case((TopVacancy.data["eligibility"].as_string() == "unknown",
                  TopVacancy.data["description"].as_string()), else_="").label("description"))
    query = query.where(
        TopVacancy.score >= MIN_SCORE,
        or_(TopVacancy.favorite.is_(True), TopVacancy.published_at >= current - timedelta(hours=WINDOW_HOURS)),
        TopVacancy.published_at <= current,
    )
    if favorites:
        query = query.where(TopVacancy.favorite.is_(True))
    if provider:
        query = query.where(TopVacancy.data["provider"].as_string() == provider)
    query = query.order_by(TopVacancy.score.desc(), TopVacancy.published_at.desc(), TopVacancy.id)
    if summary:
        # Transient view objects preserve the shared dedup/favorite rules. Only
        # uncertain approvals need description bytes for context verification.
        rows = []
        for entry in db.execute(query).mappings():
            data = {key: entry["data_" + key] for key in fields if entry["data_" + key] is not None}
            if entry["recommendable"] is not None:
                data["explanation"] = {"recommendable": entry["recommendable"]}
            if data.get("eligibility") == "unknown":
                data["description"] = entry["description"]
            rows.append(TopVacancy(**{column.name: entry[column.name] for column in metadata}, data=data))
    else:
        rows = list(db.scalars(query))
    unique: dict[tuple[str, ...], TopVacancy] = {}
    seen_urls: set[tuple[str, str, str]] = set()
    for row in rows:
        from .vacancy_review import is_approved, is_rejected
        # A favorite keeps its publication snapshot after the normal window.
        checked_at = publication(row.published_at) if row.favorite else current
        if (qualifies(row.data, checked_at) and not is_rejected(db, row.data) and
                (row.data.get("eligibility") == "eligible" or is_approved(db, row.data))):
            url_key = normalized_url(row.data["canonical_url"])
            identity = vacancy_identity(row.data)
            if url_key not in seen_urls and identity not in unique:
                unique[identity] = row
            elif row.favorite:
                # Return the saved representative so the heart remains stable.
                representative = next((key for key, item in unique.items()
                                       if same_vacancy(item.data, row.data)), None)
                if representative is not None and not unique[representative].favorite:
                    unique[representative] = row
            seen_urls.add(url_key)
    return sorted(unique.values(), key=lambda row: (-row.score, -(publication(row.published_at) or current).timestamp(), row.id))


@router.get("/top-vacancies")
def top_vacancies(offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100),
                  provider: Literal["", "linkedin"] = "",
                  favorites: bool = False,
                  db: Session = Depends(session)) -> dict[str, Any]:
    rows = top_rows(db, provider, favorites=favorites, summary=True)
    fields = ("company", "title", "location", "salary", "provider", "canonical_url", "published_at", "publication_label", "eligibility", "review_status")
    return {"window_hours": WINDOW_HOURS, "min_score": MIN_SCORE, "total": len(rows),
            "items": [{**{key: row.data.get(key) for key in fields}, "id": row.id, "score": row.score,
                       "resume_filename": row.resume_filename, "search_name": row.search_name,
                       "favorite": row.favorite}
                      for row in rows[offset:offset + limit]]}


class FavoriteInput(BaseModel):
    favorite: bool


def set_favorite(db: Session, vacancy_id: str, favorite: bool) -> dict[str, Any]:
    from .vacancy_review import is_rejected
    row = db.get(TopVacancy, vacancy_id)
    if not row or is_rejected(db, row.data):
        raise HTTPException(404, "Вакансия не найдена")
    # Explicit state makes retries idempotent. Mark every stored representative.
    for peer in db.scalars(select(TopVacancy)):
        if same_vacancy(row.data, peer.data):
            peer.favorite = favorite
    db.commit()
    return {"id": vacancy_id, "favorite": favorite}


@router.put("/top-vacancies/{vacancy_id}/favorite")
def favorite_vacancy(vacancy_id: str, body: FavoriteInput, db: Session = Depends(session)) -> dict[str, Any]:
    return set_favorite(db, vacancy_id, body.favorite)
