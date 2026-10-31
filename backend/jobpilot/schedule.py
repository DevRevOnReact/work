"""One UTC three-hour slot shared by the API, Celery and cloud runner."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import SavedSearch


def slot_start(at: datetime | None = None) -> datetime:
    value = (at or datetime.now(UTC)).astimezone(UTC)
    return value.replace(hour=value.hour // 3 * 3, minute=0, second=0, microsecond=0)


def due_searches(db: Session) -> list[str]:
    return list(db.scalars(select(SavedSearch.id).where(
        SavedSearch.enabled.is_(True), SavedSearch.status.notin_(["queued", "running"]),
        (SavedSearch.started_at.is_(None)) | (SavedSearch.started_at < slot_start()),
    )))


def next_run(started: datetime | None, offset_minutes: int = 0) -> str:
    current = slot_start()
    normalized = (started.replace(tzinfo=UTC) if started.tzinfo is None else started.astimezone(UTC)) if started else None
    if normalized and normalized >= current:
        current += timedelta(hours=3)
    return (current + timedelta(minutes=offset_minutes)).isoformat()
