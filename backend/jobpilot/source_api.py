from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import session
from .http import authenticate, serialize
from .models import Candidate, SavedSearch, Source
from .services import audit

router = APIRouter(dependencies=[Depends(authenticate)])


class SourceInput(BaseModel):
    # Built-in adapters are used for discovery; custom names are also allowed
    # so users can keep any hiring platform in their selected sources list.
    provider: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 _.-]{1,39}$")
    board: str = Field(pattern=r"^[A-Za-z0-9_-]{1,120}$")
    company: str = Field(min_length=1, max_length=200)


@router.get("/sources")
def sources(db: Session = Depends(session)) -> list[dict[str, Any]]:
    return [serialize(s) for s in db.scalars(select(Source).order_by(Source.company))]


@router.post("/sources")
def add_source(body: SourceInput, db: Session = Depends(session)) -> dict[str, Any]:
    existing = db.scalar(select(Source).where(Source.provider == body.provider, Source.board == body.board))
    if existing:
        raise HTTPException(409, "Board already exists")
    source = Source(**body.model_dump())
    db.add(source)
    db.commit()
    return serialize(source)


@router.post("/sources/{source_id}/toggle")
def toggle_source(source_id: str, db: Session = Depends(session)) -> dict[str, Any]:
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(404, "Source not found")
    source.enabled = not source.enabled
    db.commit()
    return serialize(source)


@router.get("/sources/suggestions")
def source_suggestions() -> list[dict[str, str]]:
    from .sources import STARTER_SOURCES

    return STARTER_SOURCES


@router.post("/sources/starter")
def connect_starter_sources(db: Session = Depends(session)) -> dict[str, Any]:
    from .sources import STARTER_SOURCES
    # Serialize setup with uploads/profile saves; repeated clicks are idempotent.
    db.scalar(select(Candidate).where(Candidate.id == 1).with_for_update())
    connected = 0
    for item in STARTER_SOURCES:
        existing = db.scalar(select(Source).where(Source.provider == item["provider"], Source.board == item["board"]))
        if existing is None:
            db.add(Source(**item))
            connected += 1
    audit(db, "starter_sources_connected", {"new_sources": connected})
    db.commit()
    return {"connected": connected, "discovery": "scheduled", "sources": STARTER_SOURCES}


@router.post("/discover")
def discover(request: Request, db: Session = Depends(session)) -> dict[str, Any]:
    if settings().embedded_searches:
        runtime = getattr(request.app.state, "search_runtime", None)
        if runtime is None:
            raise HTTPException(503, "Локальный планировщик ещё не запущен")
        ids = list(db.scalars(select(SavedSearch.id).where(SavedSearch.enabled.is_(True))))
        return {"status": "queued", "searches": sum(runtime.enqueue(search_id) for search_id in ids)}
    from .workers import discover_all

    try:
        result = discover_all.delay()
    except Exception as exc:
        raise HTTPException(503, "Discovery queue unavailable") from exc
    return {"task_id": result.id, "status": "queued"}


