"""The web owner approves a hash. Only the phone holds its random bearer secret."""
import hashlib
import re
from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from .http import authenticate
from .models import AssistantDevice, Candidate, ReplyDraft, now
from .reply_api import Database, create, document, media, overview

admin = APIRouter(prefix="/mobile-devices", dependencies=[Depends(authenticate)])


class Approval(BaseModel):
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$", min_length=64, max_length=64)


@admin.get("")
def devices(db: Database) -> list[dict]:
    rows = db.scalars(select(AssistantDevice).where(AssistantDevice.expires_at > now())
                      .order_by(AssistantDevice.expires_at.desc()))
    return [{"fingerprint": row.fingerprint, "expires_at": row.expires_at} for row in rows]


@admin.post("")
def approve(body: Approval, db: Database) -> dict:
    db.execute(select(Candidate.id).where(Candidate.id == 1).with_for_update()).one()
    db.execute(delete(AssistantDevice).where(AssistantDevice.expires_at <= now()))
    row = db.get(AssistantDevice, body.fingerprint)
    if row is None:
        if (db.scalar(select(func.count()).select_from(AssistantDevice)) or 0) >= 5:
            raise HTTPException(409, "Сначала отключите одно из устройств.")
        row = AssistantDevice(fingerprint=body.fingerprint)
        db.add(row)
    row.expires_at = now() + timedelta(days=90)
    db.commit()
    return {"ok": True}


@admin.delete("/{fingerprint}")
def revoke(fingerprint: str, db: Database) -> dict:
    db.execute(delete(AssistantDevice).where(AssistantDevice.fingerprint == fingerprint))
    db.commit()
    return {"ok": True}


def device_access(db: Database, authorization: Annotated[str, Header()] = "") -> None:
    match = re.fullmatch(r"Bearer (jp1_[A-Za-z0-9_-]{43})", authorization)
    if match:
        fingerprint = hashlib.sha256(match[1].encode("ascii")).hexdigest()
        allowed = db.scalar(select(AssistantDevice.fingerprint).where(
            AssistantDevice.fingerprint == fingerprint, AssistantDevice.expires_at > now()))
        if allowed:
            return
    raise HTTPException(401, "Подключите телефон в личном кабинете JobPilot.")


mobile = APIRouter(prefix="/mobile-assistant", dependencies=[Depends(device_access)])
mobile.add_api_route("", overview, methods=["GET"])
mobile.add_api_route("", create, methods=["POST"], status_code=202)
mobile.add_api_route("/media/{kind}", media, methods=["POST"])
mobile.add_api_route("/{draft_id}/file", document, methods=["GET"])


@mobile.get("/{draft_id}/status")
def status(draft_id: UUID, db: Database) -> dict:
    result = db.scalar(select(ReplyDraft.status).where(ReplyDraft.id == str(draft_id)))
    if result is None:
        raise HTTPException(404, "Запрос отсутствует.")
    return {"status": result}


@mobile.get("/{draft_id}/answers")
def answers(draft_id: UUID, db: Database) -> dict:
    row = db.execute(select(ReplyDraft.status, ReplyDraft.result).where(ReplyDraft.id == str(draft_id))).one_or_none()
    if row is None:
        raise HTTPException(404, "Черновик уже удалён.")
    result = row.result or {}
    return {"status": row.status, "variants": result.get("variants", {}), "note": result.get("note", ""),
            "error": result.get("error", "")}
