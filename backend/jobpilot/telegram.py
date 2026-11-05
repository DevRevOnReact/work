import re
from datetime import timedelta

import httpx
from sqlalchemy import func, select, text

from .config import settings
from .db import SessionLocal
from .models import Application, Audit, Control, Job, Outbox, SearchNotification, VacancyReview, now
from .services import MINIMUM_QUALIFICATION_SCORE, audit


def poll_commands() -> None:
    s = settings()
    if not s.telegram_bot_token.get_secret_value() or not s.telegram_chat_id:
        return
    with SessionLocal() as db:
        if (db.bind is not None and db.bind.dialect.name == "postgresql" and
                not db.scalar(text("SELECT pg_try_advisory_xact_lock(804223)"))):
            return
        last = db.scalar(
            select(Audit).where(Audit.kind == "telegram_cursor").order_by(Audit.id.desc()).limit(1)
        )
        offset = last.detail["offset"] if last else 0
        response = httpx.get(
            f"https://api.telegram.org/bot{s.telegram_bot_token.get_secret_value()}/getUpdates",
            params={"offset": offset, "timeout": 0, "allowed_updates": '["message","callback_query"]'},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            return
        responses = []
        for item in data["result"]:
            callback = item.get("callback_query")
            if callback:
                message = callback.get("message", {})
                callback_id = callback.get("id")
                result, notification = "Эта кнопка больше не действует.", None
                match = re.fullmatch(r"vr:([ar]):([0-9a-f-]{36})", str(callback.get("data", "")))
                if str(message.get("chat", {}).get("id")) == s.telegram_chat_id and match:
                    review = db.get(VacancyReview, match.group(2))
                    queued = db.get(SearchNotification, review.notification_id) if review and review.notification_id else None
                    # Delivery commits its receipt after the batch. Do not consume a real click
                    # that arrived while the send is still in that uncommitted transaction.
                    if review and review.status == "pending" and queued and queued.status == "pending" and not queued.provider_message_id:
                        break
                    from .vacancy_review import decide
                    result, notification = decide(db, match.group(2), str(message.get("message_id", "")),
                                                  "approved" if match.group(1) == "a" else "rejected")
                if callback_id:
                    responses.append((callback_id, result, notification))
                offset = max(offset, item["update_id"] + 1)
                continue
            message = item.get("message", {})
            if str(message.get("chat", {}).get("id")) == s.telegram_chat_id:
                command = message.get("text", "").split("@")[0].strip()
                control = db.get(Control, 1)
                output = ""
                if command in {"/pause", "/resume"} and control:
                    control.enabled = command == "/resume"
                    audit(db, "automation_control", {"enabled": control.enabled, "actor": "telegram"})
                    output = (
                        "Automation resumed"
                        if control.enabled
                        else "Automation paused; in-flight submits may finish"
                    )
                elif command in {"/start", "/top"}:
                    output = "Откройте топ вакансий в мини-приложении кнопкой ниже."
                elif command in {"/stats", "/today"}:
                    query = select(Application.state, func.count()).group_by(Application.state)
                    if command == "/today":
                        query = query.where(
                            Application.created_at >= now().replace(hour=0, minute=0, second=0, microsecond=0)
                        )
                    output = (
                        "\n".join(f"{state}: {count}" for state, count in db.execute(query))
                        or "No activity yet"
                    )
                elif command in {"/jobs", "/applied", "/review"}:
                    query_jobs = select(Job, Application).join(Application).where(
                        Job.score >= MINIMUM_QUALIFICATION_SCORE
                    )
                    if command == "/applied":
                        query_jobs = query_jobs.where(Application.submitted_at.is_not(None))
                    elif command == "/review":
                        query_jobs = query_jobs.where(Application.attention.is_not(None))
                    else:
                        query_jobs = query_jobs.where(Job.score >= 80)
                    rows = db.execute(query_jobs.order_by(Job.discovered_at.desc()).limit(10)).all()
                    output = (
                        "\n\n".join(
                            f"{j.score} · {j.company} · {j.title}\n{a.attention or a.state}\n{j.canonical_url}"
                            for j, a in rows
                        )
                        or "No matching records"
                    )
                if output:
                    db.add(Outbox(message=output))
            offset = max(offset, item["update_id"] + 1)
        if offset != (last.detail["offset"] if last else 0):
            audit(db, "telegram_cursor", {"offset": offset})
        db.commit()
        # Database decisions are durable before acknowledging a callback or editing a card.
        for callback_id, result, notification in responses:
            try:
                httpx.post(f"https://api.telegram.org/bot{s.telegram_bot_token.get_secret_value()}/answerCallbackQuery",
                           json={"callback_query_id": callback_id, "text": result}, timeout=15).raise_for_status()
                if notification and notification.provider_message_id and result != "Эта кнопка больше не действует.":
                    httpx.post(f"https://api.telegram.org/bot{s.telegram_bot_token.get_secret_value()}/editMessageText",
                               json={"chat_id": s.telegram_chat_id, "message_id": notification.provider_message_id,
                                     "text": notification.message + "\n\n" + result,
                                     "disable_web_page_preview": True, "reply_markup": {"inline_keyboard": []}},
                               timeout=15).raise_for_status()
            except (httpx.HTTPError, ValueError):
                pass  # The saved decision remains authoritative if Telegram is unavailable.


def deliver_outbox() -> None:
    """Deliver legacy command replies with the same lock for embedded and Celery runtimes."""
    s = settings()
    if not s.telegram_bot_token.get_secret_value() or not s.telegram_chat_id:
        return
    with SessionLocal() as db:
        if (db.bind is not None and db.bind.dialect.name == "postgresql" and
                not db.scalar(text("SELECT pg_try_advisory_xact_lock(804225)"))):
            return
        for item in db.scalars(select(Outbox).where(Outbox.status == "pending", Outbox.next_attempt <= now())
                               .with_for_update(skip_locked=True).limit(20)):
            payload = {"chat_id": s.telegram_chat_id, "text": item.message[:4000],
                       "disable_web_page_preview": True}
            if item.message == "Откройте топ вакансий в мини-приложении кнопкой ниже." and s.public_app_url.startswith("https://"):
                payload["reply_markup"] = {"inline_keyboard": [[{"text": "Открыть топ вакансий", "web_app": {"url": s.public_app_url.rstrip("/") + "/telegram"}}]]}
            try:
                response = httpx.post(
                    f"https://api.telegram.org/bot{s.telegram_bot_token.get_secret_value()}/sendMessage",
                    json=payload, timeout=15)
                response.raise_for_status()
                if not response.json().get("ok"):
                    raise ValueError("Telegram rejected message")
                item.status, item.error = "sent", None
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                item.attempts += 1
                item.error = type(exc).__name__
                item.next_attempt = now() + timedelta(seconds=min(3600, 30 * 2**item.attempts))
                if item.attempts >= 8:
                    item.status = "dead"
        db.commit()
