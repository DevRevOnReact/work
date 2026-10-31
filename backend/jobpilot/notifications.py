"""Persisted, deduplicated search digests. Delivery errors never fail a search."""
import hashlib
from datetime import timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal, session
from .http import authenticate
from .models import NotificationSeen, SavedSearch, SearchNotification, now, uid

router = APIRouter(dependencies=[Depends(authenticate)])


def channels() -> list[str]:
    config = settings()
    result = []
    if config.telegram_bot_token.get_secret_value() and config.telegram_chat_id:
        result.append("telegram")
    if config.resend_api_key.get_secret_value() and config.notification_email:
        result.append("email")
    return result


def enqueue_digest(db: Session, search: SavedSearch, results: list[dict[str, Any]]) -> None:
    for channel in channels():
        fresh = []
        keys = {hashlib.sha256(f"{channel}:{search.id}:{job['canonical_url']}".encode()).hexdigest(): job
                for job in results if job["score"] >= settings().notification_min_score
                and job.get("eligibility") != "rejected"}
        seen = set(db.scalars(select(NotificationSeen.id).where(NotificationSeen.id.in_(keys))))
        for key, job in keys.items():
            if key not in seen:
                db.add(NotificationSeen(id=key))
                fresh.append(job)
        if not fresh:
            continue
        lines = [f"JobPilot · {search.name[:120]}", f"Новых вакансий: {len(fresh)}"]
        for job in fresh[:8]:
            lines.append(f"{job['score']}/100 · {job['company'][:70]} · {job['title'][:100]}\n"
                         f"{job['location'][:120]}\n{job['canonical_url'][:400]}")
        if settings().public_app_url:
            lines.append("Все результаты: " + settings().public_app_url)
        message = "\n\n".join(lines)
        # Telegram caps messages at 4096 characters. Preserve full job links.
        while len(message) > 3900 and len(lines) > 3:
            lines.pop(-2 if settings().public_app_url else -1)
            message = "\n\n".join(lines)
        identity = hashlib.sha256(f"{channel}:{search.id}:{message}".encode()).hexdigest()
        if db.get(SearchNotification, identity) is None:
            db.add(SearchNotification(id=identity, search_id=search.id, channel=channel, message=message))


def deliver_pending() -> None:
    config = settings()
    if not channels():
        return
    with SessionLocal() as db:
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            if not db.scalar(text("SELECT pg_try_advisory_xact_lock(804224)")):
                return
        pending = list(db.scalars(select(SearchNotification).where(
            SearchNotification.status == "pending", SearchNotification.next_attempt <= now(),
        ).order_by(SearchNotification.created_at).limit(4)))
        with httpx.Client(timeout=15) as client:
            for item in pending:
                if item.channel not in channels():
                    continue
                item.attempts += 1
                try:
                    if item.channel == "telegram":
                        response = client.post(
                            f"https://api.telegram.org/bot{config.telegram_bot_token.get_secret_value()}/sendMessage",
                            json={"chat_id": config.telegram_chat_id, "text": item.message, "disable_web_page_preview": True})
                        response.raise_for_status()
                        if not response.json().get("ok"):
                            raise ValueError("Telegram rejected the message")
                    else:
                        response = client.post("https://api.resend.com/emails",
                            headers={"Authorization": "Bearer " + config.resend_api_key.get_secret_value(),
                                     "Idempotency-Key": item.id},
                            json={"from": config.notification_from, "to": [config.notification_email],
                                  "subject": "JobPilot · новые вакансии", "text": item.message})
                        response.raise_for_status()
                    item.status, item.error = "sent", None
                except Exception as exc:
                    # HTTP exception strings contain request URLs, including bot tokens.
                    item.error = (f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError)
                                  else type(exc).__name__)
                    retry = 0
                    if isinstance(exc, httpx.HTTPStatusError):
                        try:
                            retry = int(exc.response.headers.get("Retry-After", "0"))
                        except ValueError:
                            pass
                    item.next_attempt = now() + timedelta(seconds=max(retry, min(3600, 60 * 2 ** min(item.attempts, 6))))
                    if item.attempts >= 8:
                        item.status = "failed"
        db.commit()


@router.get("/notifications/status")
def notification_status(db: Session = Depends(session)) -> dict[str, Any]:
    rows = db.execute(select(SearchNotification.status, func.count()).group_by(SearchNotification.status)).all()
    latest_error = db.scalar(select(SearchNotification.error).where(SearchNotification.error.is_not(None))
                             .order_by(SearchNotification.created_at.desc()).limit(1))
    return {"telegram": "telegram" in channels(), "email": "email" in channels(),
            "recipient": settings().notification_email, "counts": dict(rows), "last_error": latest_error}


@router.post("/notifications/test")
def test_notifications(db: Session = Depends(session)) -> dict[str, int]:
    if not channels():
        raise HTTPException(409, "Добавьте Telegram или Resend в секреты хостинга")
    search_id = db.scalar(select(SavedSearch.id).limit(1))
    if not search_id:
        raise HTTPException(409, "Сначала создайте поиск")
    for channel in channels():
        db.add(SearchNotification(id=hashlib.sha256(uid().encode()).hexdigest(), search_id=search_id,
                                  channel=channel, message="JobPilot · проверка уведомлений. Подключение работает."))
    db.commit()
    return {"queued": len(channels())}


@router.post("/notifications/dispatch")
def dispatch_notifications(db: Session = Depends(session)) -> dict[str, Any]:
    # The daily runner can wake the web service after searching in PostgreSQL.
    # Credentials for delivery remain only on Render.
    for search in db.scalars(select(SavedSearch).where(
            SavedSearch.status == "complete", SavedSearch.enabled.is_(True))):
        enqueue_digest(db, search, search.results)
    db.commit()
    deliver_pending()
    return notification_status(db)
