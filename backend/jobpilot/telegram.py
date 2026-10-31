import httpx
from sqlalchemy import func, select, text
from .config import settings
from .db import SessionLocal
from .models import Application, Audit, Control, Job, Outbox, now
from .services import MINIMUM_QUALIFICATION_SCORE, audit


def poll_commands() -> None:
    s = settings()
    if not s.telegram_bot_token.get_secret_value() or not s.telegram_chat_id:
        return
    with SessionLocal() as db:
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            if not db.scalar(text("SELECT pg_try_advisory_xact_lock(804223)")):
                return
        last = db.scalar(
            select(Audit).where(Audit.kind == "telegram_cursor").order_by(Audit.id.desc()).limit(1)
        )
        offset = last.detail["offset"] if last else 0
        response = httpx.get(
            f"https://api.telegram.org/bot{s.telegram_bot_token.get_secret_value()}/getUpdates",
            params={"offset": offset, "timeout": 0, "allowed_updates": '["message"]'},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            return
        for item in data["result"]:
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
        if data["result"]:
            audit(db, "telegram_cursor", {"offset": offset})
        db.commit()
