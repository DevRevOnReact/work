from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .config import settings
from .db import session
from .http import authenticate, serialize
from .models import SavedSearch, SearchNotification, now
from .search_setup import ensure_resume_searches
from .searches import SearchInput, search_profile

router = APIRouter(dependencies=[Depends(authenticate)])


def validate(db: Session, body: SearchInput) -> None:
    if not body.name.strip():
        raise HTTPException(422, "Укажите название поиска")
    try:
        search_profile(db, body.resume_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/searches")
def list_searches(db: Session = Depends(session)) -> list[dict[str, Any]]:
    # Recover the visible state after a killed worker; the hard task limit is 10 minutes.
    stale = list(db.scalars(select(SavedSearch.id).where(
        SavedSearch.status == "running", SavedSearch.started_at < now() - timedelta(minutes=20))))
    if stale:
        db.execute(update(SavedSearch).where(
            SavedSearch.id.in_(stale), SavedSearch.status == "running",
            SavedSearch.started_at < now() - timedelta(minutes=20),
        ).values(status="failed", errors=["Поиск прерван; запустите его снова"]))
        db.commit()
    # Summary polling must not repeatedly transfer up to 500 full descriptions per CV.
    columns = [column for column in SavedSearch.__table__.columns if column.name != "results"]
    return [dict(row) for row in db.execute(select(*columns).order_by(SavedSearch.name)).mappings()]


@router.post("/searches/setup")
def setup_searches(db: Session = Depends(session)) -> dict[str, int]:
    created = ensure_resume_searches(db)
    db.commit()
    return {"created": created}


@router.get("/searches/{search_id}")
def search_detail(search_id: str, db: Session = Depends(session)) -> dict[str, Any]:
    search = db.get(SavedSearch, search_id)
    if not search:
        raise HTTPException(404, "Поиск не найден")
    return serialize(search)


@router.post("/searches")
def create_search(body: SearchInput, db: Session = Depends(session)) -> dict[str, Any]:
    validate(db, body)
    s = SavedSearch(name=body.name.strip(), resume_id=body.resume_id,
                    filters=body.filters.model_dump(), enabled=body.enabled)
    db.add(s)
    db.commit()
    return serialize(s)


@router.put("/searches/{search_id}")
def update_search(search_id: str, body: SearchInput, db: Session = Depends(session)) -> dict[str, Any]:
    s = db.get(SavedSearch, search_id)
    if not s:
        raise HTTPException(404, "Поиск не найден")
    if s.status in {"queued", "running"}:
        raise HTTPException(409, "Дождитесь завершения поиска")
    validate(db, body)
    changed = s.resume_id != body.resume_id or s.filters != body.filters.model_dump()
    s.name, s.resume_id, s.filters, s.enabled = body.name.strip(), body.resume_id, body.filters.model_dump(), body.enabled
    if changed:
        s.results, s.result_count, s.completed_at, s.errors, s.status = [], 0, None, [], "idle"
    db.commit()
    return serialize(s)


@router.delete("/searches/{search_id}")
def delete_search(search_id: str, db: Session = Depends(session)) -> dict[str, bool]:
    s = db.get(SavedSearch, search_id)
    if not s:
        raise HTTPException(404, "Поиск не найден")
    if s.status in {"queued", "running"}:
        raise HTTPException(409, "Дождитесь завершения поиска")
    db.execute(delete(SearchNotification).where(SearchNotification.search_id == search_id))
    db.delete(s)
    db.commit()
    return {"deleted": True}


@router.post("/searches/{search_id}/run")
def run_search(search_id: str, request: Request, db: Session = Depends(session)) -> dict[str, bool]:
    s = db.get(SavedSearch, search_id)
    if not s:
        raise HTTPException(404, "Поиск не найден")
    if s.status in {"queued", "running"}:
        raise HTTPException(409, "Поиск уже выполняется")
    if settings().embedded_searches:
        runtime = getattr(request.app.state, "search_runtime", None)
        if runtime is None:
            raise HTTPException(503, "Локальный планировщик ещё не запущен")
        if not runtime.enqueue(search_id):
            raise HTTPException(409, "Поиск уже в очереди")
        return {"queued": True}
    from .workers import run_saved_search
    try:
        run_saved_search.delay(search_id)
    except Exception as exc:
        raise HTTPException(503, "Очередь недоступна; проверьте Redis и worker") from exc
    return {"queued": True}

