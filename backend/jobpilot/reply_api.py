from threading import BoundedSemaphore
from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import settings
from .db import session
from .http import authenticate
from .models import Outbox, ReplyDraft, Resume, now
from .reply_assistant import MEDIA_LIMIT, AssistantError, DraftInput, recognize
from .reply_runtime import RETENTION, enqueue
from .resume_api import resume_file
from .resume_storage import read_file
from .telegram_mini import authenticate_telegram

routes = APIRouter()
media_slot = BoundedSemaphore(1)
Database = Annotated[Session, Depends(session)]


@routes.get("")
def overview(db: Database) -> dict:
    rows = db.scalars(select(ReplyDraft).where(ReplyDraft.created_at >= now() - RETENTION)
                      .order_by(ReplyDraft.created_at.desc()).limit(30))
    resumes = db.execute(select(Resume.id, Resume.filename, Resume.variant).order_by(Resume.created_at.desc()))
    return {"resumes": [dict(row._mapping) for row in resumes], "drafts": [
        {"id": row.id, "status": row.status, "question": row.request.get("question"),
         "language": row.request.get("language"), "instruction": row.request.get("instruction", ""),
         "vacancy": row.request.get("vacancy", ""), "resume_id": row.request.get("resume_id"),
         "result": row.result, "created_at": row.created_at}
        for row in rows], "media_enabled": bool(settings().assistant_media_url),
        "notify_enabled": bool(settings().telegram_chat_id.isdecimal() and settings().telegram_bot_token.get_secret_value())}


@routes.post("", status_code=202)
def create(body: DraftInput, db: Database) -> dict:
    try:
        draft = enqueue(db, body, "app")
    except AssistantError as exc:
        raise HTTPException(429 if settings().assistant_model_url else 503, str(exc)) from None
    db.commit()
    return {"id": draft.id, "status": draft.status}


@routes.delete("/{draft_id}")
def remove(draft_id: UUID, db: Database) -> dict:
    row = db.scalar(select(ReplyDraft).where(ReplyDraft.id == str(draft_id)).with_for_update())
    if row:
        db.execute(delete(Outbox).where(Outbox.id == row.id))
        db.delete(row)
        db.commit()
    return {"ok": True}


@routes.post("/media/{kind}")
async def media(kind: Literal["audio", "image"], request: Request) -> dict:
    if not media_slot.acquire(blocking=False):
        raise HTTPException(429, "Идёт распознавание. Попробуйте чуть позже.")
    try:
        content = bytearray()
        async for chunk in request.stream():
            if len(content) + len(chunk) > MEDIA_LIMIT:
                raise HTTPException(413, "Допустимый размер до 4 МБ.")
            content.extend(chunk)
        return {"text": await run_in_threadpool(recognize, kind, bytes(content))}
    except AssistantError as exc:
        raise HTTPException(422, str(exc)) from None
    finally:
        media_slot.release()


def selected_resume(draft_id: UUID, db: Session) -> Resume:
    row = db.get(ReplyDraft, str(draft_id))
    selected = row.result.get("resume", {}).get("id") if row and row.status == "ready" else None
    resume = db.get(Resume, selected) if selected else None
    if not resume:
        raise HTTPException(404, "Резюме для этого ответа недоступно.")
    return resume


@routes.get("/{draft_id}/file")
def document(draft_id: UUID, db: Database) -> Response:
    return resume_file(selected_resume(draft_id, db).id, db)


mini = APIRouter(dependencies=[Depends(authenticate_telegram)])
web = APIRouter(dependencies=[Depends(authenticate)])
mini.include_router(routes, prefix="/telegram-mini/assistant")
web.include_router(routes, prefix="/assistant")


@mini.post("/telegram-mini/assistant/{draft_id}/document")
def send_document(draft_id: UUID, db: Database) -> dict:
    resume = selected_resume(draft_id, db)
    try:
        content = read_file(db, resume)
    except (ValueError, OSError):
        raise HTTPException(404, "Файл недоступен.") from None
    config = settings()
    try:
        response = httpx.post(f"https://api.telegram.org/bot{config.telegram_bot_token.get_secret_value()}/sendDocument",
                              data={"chat_id": config.telegram_chat_id},
                              files={"document": (resume.filename, content, "application/octet-stream")}, timeout=35)
        response.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(502, "Проверьте личный чат с ботом. Не удалось подтвердить доставку.") from None
    return {"ok": True}
