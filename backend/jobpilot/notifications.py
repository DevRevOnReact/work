"""Persisted, deduplicated search digests. Delivery errors never fail a search."""
import hashlib
import os
import re
import time
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
from .top_vacancies import publication, qualifies, top_rows

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
        if channel == "telegram":
            continue  # Telegram receives every top vacancy individually, through the durable outbox below.
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


def enqueue_top_telegram(db: Session, search_id: str, jobs: list[dict[str, Any]], *,
                         enqueue_without_credentials: bool = False) -> int:
    if not enqueue_without_credentials and "telegram" not in channels():
        return 0
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    queued = 0
    current = now()
    for job in jobs:
        if not qualifies(job, current):
            continue
        published = publication(job["published_at"])
        assert published is not None
        key = hashlib.sha256(f"telegram-top-v1:{job['canonical_url']}".encode()).hexdigest()
        claimed = db.scalar(insert(NotificationSeen).values(id=key).on_conflict_do_nothing().returning(NotificationSeen.id))
        if not claimed:
            continue
        message = (f"JobPilot · топ за 3 дня · {job['score']}/100\n\n"
                   f"{job['company'][:120]} · {job['title'][:240]}\n{job.get('location', '')[:200]}\n"
                   f"Опубликовано: {job['published_at']}\n\n{job['canonical_url']}")
        if len(message) > 4096:
            # Preserve the complete URL; truncate only descriptive text.
            message = f"JobPilot · {job['score']}/100\n{job['canonical_url']}"
        db.add(SearchNotification(id=key, search_id=search_id, channel="telegram", message=message,
                                  status="pending" if len(message) <= 4096 else "failed",
                                  expires_at=published + timedelta(hours=72),
                                  error=None if len(message) <= 4096 else "Ссылка превышает лимит сообщения Telegram"))
        queued += 1
    return queued


def deliver_pending(*, only_ids: list[str] | None = None, batch_size: int = 20) -> None:
    config = settings()
    if not channels():
        return
    with SessionLocal() as db:
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            if not db.scalar(text("SELECT pg_try_advisory_xact_lock(804224)")):
                return
        query = select(SearchNotification).where(
            SearchNotification.status == "pending",
            (SearchNotification.next_attempt <= now()) | (SearchNotification.expires_at <= now()),
            SearchNotification.channel.in_(channels()),
        ).order_by(SearchNotification.created_at).limit(batch_size)
        if only_ids is not None:
            query = query.where(SearchNotification.id.in_(only_ids))
        pending = list(db.scalars(query))
        last_telegram = 0.0
        batch_started = time.monotonic()
        with httpx.Client(timeout=15) as client:
            for item in pending:
                if time.monotonic() - batch_started > 75:
                    break
                expiry = publication(item.expires_at) if item.expires_at else None
                if expiry is not None and expiry <= now():
                    item.status = "expired"
                    continue
                if item.channel not in channels():
                    continue
                item.attempts += 1
                try:
                    if item.channel == "telegram":
                        spacing = 3.1 if config.telegram_chat_id.startswith("-") else 1.1
                        delay = spacing - (time.monotonic() - last_telegram)
                        if delay > 0:
                            time.sleep(delay)
                        response = client.post(
                            f"https://api.telegram.org/bot{config.telegram_bot_token.get_secret_value()}/sendMessage",
                            json={"chat_id": config.telegram_chat_id, "text": item.message, "disable_web_page_preview": True})
                        response.raise_for_status()
                        last_telegram = time.monotonic()
                        if not response.json().get("ok"):
                            raise ValueError("Telegram rejected the message")
                        message_id = response.json().get("result", {}).get("message_id")
                        item.provider_message_id = str(message_id) if message_id is not None else None
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
                    if item.channel == "telegram" and isinstance(exc, httpx.HTTPStatusError):
                        try:
                            description = str(exc.response.json().get("description", ""))
                            description = description.replace(config.telegram_bot_token.get_secret_value(), "[TOKEN]")
                            description = re.sub(r"https?://\S+", "[URL]", description)
                            item.error += ": " + description[:220]
                            if exc.response.status_code == 403:
                                me = client.get(f"https://api.telegram.org/bot{config.telegram_bot_token.get_secret_value()}/getMe")
                                username = str(me.json().get("result", {}).get("username", ""))
                                if re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
                                    item.error += f" (бот @{username})"
                        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                            pass
                    retry = 0
                    if isinstance(exc, httpx.HTTPStatusError):
                        try:
                            retry = int(exc.response.headers.get("Retry-After", "0"))
                        except ValueError:
                            pass
                        try:
                            retry = max(retry, int(exc.response.json().get("parameters", {}).get("retry_after", 0)))
                        except (ValueError, TypeError, AttributeError):
                            pass
                    item.next_attempt = now() + timedelta(seconds=max(retry, min(3600, 60 * 2 ** min(item.attempts, 6))))
                    if item.attempts >= 8:
                        item.status = "failed"
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                        # Honour the chat-wide cooldown for the rest of this batch too.
                        for remaining in pending[pending.index(item) + 1:]:
                            if remaining.channel == item.channel:
                                remaining.next_attempt = item.next_attempt
                        break
        db.commit()


@router.get("/notifications/status")
def notification_status(db: Session = Depends(session)) -> dict[str, Any]:
    rows = db.execute(select(SearchNotification.status, func.count()).group_by(SearchNotification.status)).all()
    latest_error = db.scalar(select(SearchNotification.error).where(SearchNotification.error.is_not(None))
                             .order_by(SearchNotification.created_at.desc()).limit(1))
    telegram_rows = db.execute(select(SearchNotification.status, func.count()).where(
        SearchNotification.channel == "telegram").group_by(SearchNotification.status)).all()
    return {"telegram": "telegram" in channels(), "email": "email" in channels(), "telegram_counts": dict(telegram_rows),
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
    # Cloud search jobs have database credentials; the bot token stays on Render.
    for row in top_rows(db):
        if db.get(SavedSearch, row.search_id) is not None:
            enqueue_top_telegram(db, row.search_id, [row.data])
    db.commit()
    deliver_pending()
    return notification_status(db)


@router.post("/notifications/deployment-test")
def deployment_test(db: Session = Depends(session)) -> dict[str, Any]:
    if "telegram" not in channels():
        raise HTTPException(409, "Telegram не настроен: нужны TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID на Render")
    commit = os.environ.get("RENDER_GIT_COMMIT", "local")
    item = queue_deployment_test(db, commit)
    db.commit()
    deliver_pending(only_ids=[item.id], batch_size=1)
    db.refresh(item)
    return {"id": item.id, "commit": commit, "status": item.status,
            "provider_message_id": item.provider_message_id, "error": item.error}


def queue_deployment_test(db: Session, commit: str) -> SearchNotification:
    identity = hashlib.sha256(f"telegram-deployment-test:{commit}".encode()).hexdigest()
    item = db.get(SearchNotification, identity)
    if item is None:
        search_id = db.scalar(select(SavedSearch.id).limit(1))
        if not search_id:
            raise HTTPException(409, "Сначала создайте поиск")
        item = SearchNotification(id=identity, search_id=search_id, channel="telegram",
                                  message=f"JobPilot · новый деплой {commit[:12]}\n\n"
                                          "Тест Telegram: подключение работает.\n"
                                          "Топ вакансий за последние 3 дня: оценка от 60/100.\n"
                                          f"{settings().public_app_url.rstrip('/')}/top")
        db.add(item)
        db.flush()
    return item
