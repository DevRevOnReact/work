import asyncio
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
import structlog
from celery import Celery
from celery.schedules import crontab
from playwright.async_api import async_playwright
from sqlalchemy import func, select, text, update

from .answers import AnswerEngine, NeedsAttention
from .ats import ATS_ADAPTERS
from .config import settings
from .db import SessionLocal
from .domain import Profile
from .models import (
    Answer,
    Application,
    Candidate,
    Control,
    Job,
    Outbox,
    Resume,
    Source,
    now,
    uid,
)
from .security import ATS_HOSTS, public_url, validate_application_url
from .services import audit, ingest, readiness, recover_stale, transition
from .sources import ADAPTERS

log = structlog.get_logger()
celery = Celery("jobpilot", broker=settings().redis_url)
celery.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    broker_connection_retry_on_startup=True,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    task_soft_time_limit=540,
    task_time_limit=600,
    beat_schedule={
        "three_hour_searches": {"task": "jobpilot.schedule_searches", "schedule": crontab(hour="*/3", minute=0)},



    },
)


@celery.task(name="jobpilot.discover_all")
def discover_all() -> None:
    with SessionLocal() as db:
        for source in db.scalars(select(Source).where(Source.enabled.is_(True))):
            discover_source.delay(source.id)


@celery.task(name="jobpilot.discover_source", bind=True, max_retries=5)
def discover_source(task: Any, source_id: str) -> None:
    with SessionLocal() as db:
        source = db.get(Source, source_id)
        if not source or not source.enabled:
            return
        try:

            async def fetch() -> Any:
                async with httpx.AsyncClient(follow_redirects=False) as client:
                    return await ADAPTERS[source.provider](client).fetch(source.board, source.company)

            count = ingest(db, source, asyncio.run(fetch()))
            audit(db, "source_synced", {"source_id": source.id, "new_jobs": count})
            db.commit()
        except Exception as exc:
            db.rollback()
            source.error = type(exc).__name__
            status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            if status_code is not None:
                source.error = f"HTTP {status_code}"
            audit(
                db,
                "source_failed",
                {
                    "source_id": source_id,
                    "error": source.error,
                    "attempt": task.request.retries,
                },
            )
            db.commit()
            # A missing/forbidden ATS board will not recover through retries.
            # 429 is handled below with the provider's Retry-After interval.
            if status_code is not None and 400 <= status_code < 500 and status_code != 429:
                return
            if task.request.retries >= 5:
                db.add(
                    Outbox(
                        message=f"❌ Discovery stopped after retries: {source.company} ({type(exc).__name__})"
                    )
                )
                db.commit()
                return
            delay = min(3600, 60 * 2**task.request.retries)
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                try:
                    delay = max(delay, int(exc.response.headers.get("Retry-After", "60")))
                except ValueError:
                    delay = max(delay, 3600)
            raise task.retry(exc=exc, countdown=delay)


@celery.task(name="jobpilot.dispatch")
def dispatch() -> None:
    # Compatibility task: new applications are always handled by the durable
    # prepare/review/submit runtime in the API, never the old immediate sender.
    from .application_runtime import ApplicationRuntime
    with SessionLocal() as db:
        recover_stale(db, settings().application_timeout_seconds + 120)
        db.commit()
    ApplicationRuntime().select_eligible()


def claim(application_id: str) -> bool:
    with SessionLocal() as db:
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(804222)"))
        control = db.get(Control, 1)
        if not control or not control.enabled:
            return False
        today = now().replace(hour=0, minute=0, second=0, microsecond=0)
        count = (
            db.scalar(
                select(func.count())
                .select_from(Application)
                .where((Application.submit_intent_at >= today) | (Application.state == "APPLYING"))
            )
            or 0
        )
        if count >= control.daily_limit:
            return False
        result = db.execute(
            update(Application)
            .where(
                Application.id == application_id,
                Application.state == "READY",
                Application.submit_intent_at.is_(None),
            )
            .values(state="APPLYING", attempt_id=uid(), updated_at=now(), attention=None)
            .returning(Application.id)
        )
        if result.scalar_one_or_none() is None:
            db.rollback()
            return False
        app = db.get(Application, application_id)
        audit(db, "state_changed", {"from": "READY", "to": "APPLYING"}, app)
        db.commit()
        return True


async def execute(application_id: str) -> None:
    with SessionLocal() as db:
        app = db.get(Application, application_id)
        assert app is not None
        job = db.get(Job, app.job_id)
        candidate = db.get(Candidate, 1)
        assert job is not None and candidate is not None
        artifacts = settings().data_dir / "artifacts" / app.id / str(app.attempt_id)
        artifacts.mkdir(parents=True, exist_ok=True)
        page = None
        browser = None
        playwright = None
        try:
            reason = readiness(db, job, app)
            if reason:
                raise NeedsAttention(reason)
            resume = db.get(Resume, app.resume_id)
            assert resume is not None
            resume_path = settings().data_dir / "resumes" / resume.storage_key
            if not resume_path.is_file():
                raise NeedsAttention("Файл выбранного резюме отсутствует в хранилище")
            validate_application_url(job.canonical_url, job.provider)
            engine = AnswerEngine(
                Profile.model_validate(candidate.facts),
                {a.question: a.answer for a in db.scalars(select(Answer))},
            )
            profile_version = candidate.version
            adapter = ATS_ADAPTERS[job.provider]()
            playwright = await async_playwright().start()
            pw = playwright
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1440, "height": 1100}, service_workers="block"
            )
            cache: dict[str, bool] = {}

            async def route_guard(route: Any) -> None:
                url = route.request.url
                host = urlsplit(url).hostname or ""
                origin = f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"
                if origin not in cache:
                    cache[origin] = await public_url(url)
                safe = cache[origin]
                if route.request.is_navigation_request():
                    safe = safe and host in ATS_HOSTS[job.provider]
                if safe:
                    await route.continue_()
                else:
                    await route.abort()

            await context.route("**/*", route_guard)
            page = await context.new_page()
            page.set_default_timeout(settings().browser_timeout_ms)
            await page.goto(job.canonical_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(1200)
            # Existing confirmations must not be mistaken for the result of our attempt.
            if await page.get_by_text(adapter.confirmation_pattern).count():
                raise NeedsAttention("На странице уже есть текст подтверждения; проверьте отклик вручную")
            app.answers = await adapter.prepare(page, resume_path.resolve(), engine, job.description)
            await page.screenshot(path=str(artifacts / "before.png"), full_page=True)
            db.refresh(candidate)
            if candidate.version != profile_version:
                raise NeedsAttention("Профиль изменился при подготовке; повторите с актуальными данными")
            control = db.get(Control, 1, populate_existing=True)
            if not control or not control.enabled:
                raise NeedsAttention("Автоматизация приостановлена до отправки")
            app.submit_intent_at = now()
            audit(
                db,
                "submit_intent",
                {
                    "attempt_id": app.attempt_id,
                    "cv_sha256": resume.sha256,
                    "profile_version": candidate.version,
                    "artifact": f"{app.id}/{app.attempt_id}/before.png",
                    "answers": app.answers,
                },
                app,
            )
            db.commit()  # The uncertainty boundary: NOTHING can retry a submit beyond this line.
            await adapter.submit(page)
            confirmed = await adapter.verify(page)
            await page.screenshot(path=str(artifacts / "after.png"), full_page=True)
            audit(
                db,
                "submission_observed",
                {"confirmed": confirmed, "artifact": f"{app.id}/{app.attempt_id}/after.png", "url": page.url},
                app,
            )
            if confirmed:
                app.submitted_at = now()
                transition(db, app, "SUBMITTED")
                db.add(Outbox(message=f"✅ Отклик отправлен: {job.title} · {job.company}"))
            else:
                transition(
                    db, app, "SUBMISSION_UNCERTAIN", "Нет однозначного подтверждения отправки; автоматический повтор отключён"
                )
                db.add(Outbox(message=f"⚠️ Проверьте отправку отклика: {job.title} · {job.company}"))
            db.commit()
        except Exception as exc:
            db.rollback()
            app = db.get(Application, application_id, populate_existing=True)
            assert app is not None
            reason = str(exc)[:1000] if isinstance(exc, (NeedsAttention, ValueError)) else type(exc).__name__
            if app.state == "APPLYING":
                target = "SUBMISSION_UNCERTAIN" if app.submit_intent_at else "FAILED"
                transition(db, app, target, reason)
                db.add(Outbox(message=f"⚠️ {job.title} · {job.company}: {reason}"))
                db.commit()
            # Playwright may already be closed after async context exit; failure capture is best effort.
            if page:
                try:
                    await page.screenshot(path=str(artifacts / "error.png"), full_page=True)
                    audit(db, "failure_screenshot", {"artifact": f"{app.id}/{app.attempt_id}/error.png"}, app)
                    db.commit()
                except Exception:
                    pass
            log.warning("application_stopped", application_id=application_id, error=reason)
        finally:
            if browser:
                await browser.close()
            if playwright:
                await playwright.stop()


@celery.task(name="jobpilot.apply")
def apply(application_id: str) -> None:
    from .automation import request_application
    with SessionLocal() as db:
        app = db.get(Application, application_id)
        job = db.get(Job, app.job_id) if app else None
        if job:
            request_application(db, job)
            db.commit()


@celery.task(name="jobpilot.deliver_outbox")
def deliver_outbox() -> None:
    s = settings()
    if not s.telegram_bot_token.get_secret_value() or not s.telegram_chat_id:
        return
    with SessionLocal() as db:
        # Hold row locks across delivery: avoids duplicate concurrent senders. At-least-once on crash.
        for item in db.scalars(
            select(Outbox)
            .where(Outbox.status == "pending", Outbox.next_attempt <= now())
            .with_for_update(skip_locked=True)
            .limit(20)
        ):
            try:
                r = httpx.post(
                    f"https://api.telegram.org/bot{s.telegram_bot_token.get_secret_value()}/sendMessage",
                    json={
                        "chat_id": s.telegram_chat_id,
                        "text": item.message[:4000],
                        "disable_web_page_preview": True,
                    },
                    timeout=15,
                )
                r.raise_for_status()
                if not r.json().get("ok"):
                    raise ValueError("Telegram rejected message")
                item.status = "sent"
            except Exception as exc:
                item.attempts += 1
                item.error = type(exc).__name__
                item.next_attempt = now() + timedelta(seconds=min(3600, 30 * 2**item.attempts))
                if item.attempts >= 8:
                    item.status = "dead"
        db.commit()


@celery.task(name="jobpilot.telegram_commands")
def telegram_commands() -> None:
    from .telegram import poll_commands

    try:
        poll_commands()
    except Exception as exc:
        # HTTP exceptions contain the bot token in the request URL.
        log.warning("telegram_poll_failed", error=type(exc).__name__)

# Scheduled searches run separately from the legacy application automation.
@celery.task(name="jobpilot.schedule_searches")
def schedule_searches() -> None:
    from .schedule import due_searches
    with SessionLocal() as db:
        ids = due_searches(db)
    for search_id in ids:
        run_saved_search.delay(search_id)


@celery.task(name="jobpilot.run_saved_search")
def run_saved_search(search_id: str) -> None:
    from .search_runner import run_saved_search as execute_search
    execute_search(search_id, session_factory=SessionLocal, adapters=ADAPTERS, use_cache=False)
