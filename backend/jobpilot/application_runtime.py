"""Single-process browser executor with durable commands and approval fencing.

Browser sessions remain alive for local CAPTCHA/login intervention and review.
After expiry or a crash, preparation is reconstructed and approval is invalidated
if the document, answers or candidate version changed. Submit is never retried.
"""
import asyncio
import hashlib
import json
import logging
import re
import shutil
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select, text, update

from . import automation as flow
from .answers import AnswerEngine, NeedsAttention
from .config import settings
from .db import SessionLocal
from .domain import Profile
from .models import Answer, Application, Candidate, Control, Job, SavedSearch, now
from .security import (
    ATS_API_HOSTS,
    automation_provider,
    is_submission_request,
    public_url,
    verified_application_url,
)
from .services import audit

log = logging.getLogger(__name__)


@dataclass
class BrowserSession:
    playwright: Any
    browser: Any
    context: Any
    page: Any
    adapter: Any
    profile: Profile
    profile_version: int
    document_hash: str
    attempt_id: str
    expires: float
    answers_version: str
    form_snapshot: Any = None
    submission_write_seen: bool = False


def approved_version(db: Any) -> str:
    return hashlib.sha256(str(sorted((a.question, a.answer) for a in db.scalars(select(Answer)))).encode()).hexdigest()


class ApplicationRuntime:
    def __init__(self, session_factory: Any = None) -> None:
        self.session_factory = session_factory or SessionLocal
        self.sessions: dict[str, BrowserSession] = {}
        self.thread: threading.Thread | None = None
        self.stopping = threading.Event()
        self.last_selection = 0.0

    def start(self) -> None:
        # Headed Chromium requires a desktop; containers explicitly use headless.
        self.thread = threading.Thread(target=self.run, name="application-browser", daemon=True)
        self.thread.start()

    def run(self) -> None:
        try:
            asyncio.run(self.loop())
        except Exception:
            log.exception("Application runtime stopped")

    def stop(self) -> None:
        self.stopping.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=3)

    async def loop(self) -> None:
        with self.session_factory() as db:
            flow.recover(db, stale_only=settings().hosted_mode)
        try:
            while not self.stopping.is_set():
                try:
                    await self.tick()
                except Exception:
                    log.exception("Application dispatch failed")
                await asyncio.sleep(2)
        finally:
            for application_id in list(self.sessions):
                await self.close(application_id)

    async def close(self, application_id: str) -> None:
        browser = self.sessions.pop(application_id, None)
        if browser:
            try:
                await browser.context.close()
                await browser.browser.close()
            except Exception:
                pass
            finally:
                await browser.playwright.stop()

    async def tick(self) -> None:
        # Commands are persisted and observed even when the browser process died.
        with self.session_factory() as db:
            commands = [(a.id, a.command) for a in db.scalars(select(Application).where(Application.command.is_not(None)))]
        for application_id, command in commands:
            if command in {"cancel", "pause"}:
                if command == "cancel":
                    await self.close(application_id)
                with self.session_factory() as db:
                    db.execute(update(Application).where(Application.id == application_id,
                        Application.command == command).values(command=None))
                    db.commit()
            elif command == "verify":
                await self.verify(application_id)
            elif command == "approve":
                if application_id in self.sessions:
                    await self.submit(application_id)
                else:
                    with self.session_factory() as db:
                        app = db.get(Application, application_id)
                        if app and app.state == "AWAITING_APPROVAL" and not app.submit_intent_at:
                            flow.set_step(db, app, "QUEUED", "Восстанавливаем браузер и проверяем подтверждённую версию")
                            app.command = None
                            db.commit()
            elif command == "resume":
                # Keep a live page after manual login/CAPTCHA; re-analyse its form.
                with self.session_factory() as db:
                    db.execute(update(Application).where(Application.id == application_id,
                        Application.command == "resume").values(command=None))
                    db.commit()
        for application_id, browser in list(self.sessions.items()):
            if time.monotonic() > browser.expires:
                await self.close(application_id)
                with self.session_factory() as db:
                    app = db.get(Application, application_id)
                    if app and app.state in flow.WAIT_STATES:
                        app.progress = {**app.progress, "browser_status": "closed", "message": "Сессия браузера истекла; возобновление восстановит форму"}
                        db.commit()
        if time.monotonic() - self.last_selection >= 30:
            with self.session_factory() as db:
                flow.recover(db, stale_only=True)
            self.select_eligible()
            self.last_selection = time.monotonic()
        with self.session_factory() as db:
            queued = list(db.scalars(select(Application.id).where(Application.state == "QUEUED")
                                     .order_by(Application.created_at).limit(20)))
        for application_id in queued:
            if self.stopping.is_set():
                return
            if application_id not in self.sessions and len(self.sessions) >= settings().application_session_limit:
                break
            with self.session_factory() as db:
                if not flow.claim(db, application_id):
                    continue
            try:
                await asyncio.wait_for(self.prepare(application_id), timeout=settings().application_timeout_seconds)
            except Exception as exc:
                await self.failure(application_id, exc)

    def select_eligible(self) -> None:
        from .top_vacancies import top_rows
        from .tailoring import select_resume
        from .domain import evaluate
        from .services import job_input
        with self.session_factory() as db:
            control = db.get(Control, 1)
            if not control or not control.enabled or control.mode == "manual":
                return
            if control.mode == "autonomous" and not control.auto_apply:
                return
            candidate = db.get(Candidate, 1)
            if not candidate:
                return
            profile = Profile.model_validate(candidate.facts)
            records = [row.data for row in top_rows(db)]
            for search in db.scalars(select(SavedSearch).where(SavedSearch.enabled.is_(True))):
                records.extend(search.results)
            seen: set[str] = set()
            queued = 0
            for record in sorted(records, key=lambda r: -r.get("score", 0)):
                url = record.get("canonical_url", "")
                if (not url or url in seen
                        or record.get("company", "").strip().casefold() in {c.strip().casefold() for c in control.excluded_companies}):
                    continue
                seen.add(url)
                try:
                    verified_application_url(url, control.authorized_domains)
                    job = flow.resolve_job(db, url)
                    existing = db.scalar(select(Application).where(Application.job_id == job.id))
                    if existing and (existing.attempt_id or existing.submit_intent_at):
                        continue
                    _, selected_profile = select_resume(db, job, profile)
                    match = evaluate(job_input(job), selected_profile)
                    if (match["score"] < control.min_score or match["eligibility"] != "eligible"
                            or not match.get("recommendable", True)):
                        continue
                    flow.request_application(db, job, control.mode)
                    queued += 1
                    if queued >= min(control.daily_limit, 10):
                        break
                except (ValueError, NeedsAttention):
                    continue
            db.commit()

    async def checkpoint(self, application_id: str) -> None:
        with self.session_factory() as db:
            app = db.get(Application, application_id)
            control = db.get(Control, 1)
            if self.stopping.is_set():
                raise NeedsAttention("Исполнитель останавливается; подготовку можно возобновить после запуска")
            if not app or app.state in {"CANCELLED", "PAUSED"} or app.command in {"pause", "cancel"}:
                raise NeedsAttention("Отклик остановлен пользователем")
            if not control or not control.enabled:
                raise NeedsAttention("Автоматизация приостановлена в настройках")

    async def record(self, application_id: str, step: str, message: str) -> None:
        with self.session_factory() as db:
            app = db.get(Application, application_id)
            fenced = bool(app and app.submit_intent_at)
        if not fenced:
            await self.checkpoint(application_id)
        with self.session_factory() as db:
            app = db.get(Application, application_id)
            assert app is not None
            state = app.state if app.submit_intent_at else "FILLING_FORM"
            flow.set_step(db, app, state, message, browser_action=step, browser_status="active")
            db.commit()

    async def open_browser(self, application_id: str, profile: Profile, version: int, document_hash: str,
                           attempt_id: str, answers_version: str) -> BrowserSession:
        from playwright.async_api import async_playwright

        from .ats import ATS_ADAPTERS, GenericFormAdapter
        with self.session_factory() as db:
            app = db.get(Application, application_id)
            assert app is not None
            job = db.get(Job, app.job_id)
            control = db.get(Control, 1)
            assert job and control
            url, domains = job.canonical_url, list(control.authorized_domains)
        host = verified_application_url(url, domains)
        provider = automation_provider(url)
        adapter = ATS_ADAPTERS.get(provider, GenericFormAdapter)(progress=lambda step, detail: self.record(application_id, step, detail))
        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.launch(headless=settings().application_browser_headless)
            context = await browser.new_context(viewport={"width": 1440, "height": 1000}, service_workers="block",
                                                accept_downloads=False)
        except Exception:
            await pw.stop()
            raise
        browser_session = BrowserSession(pw, browser, context, None, adapter, profile, version, document_hash,
                                         attempt_id, time.monotonic() + settings().application_session_seconds, answers_version)
        self.sessions[application_id] = browser_session
        safe_cache: dict[str, bool] = {}

        async def guard(route: Any) -> None:
            request = route.request
            p = urlsplit(request.url)
            origin = f"{p.scheme}://{p.netloc}"
            if origin not in safe_cache:
                safe_cache[origin] = await public_url(request.url)
            safe = safe_cache[origin] and not p.username and not p.password
            # User edits to policy take effect before the next network action.
            with self.session_factory() as db:
                control = db.get(Control, 1)
                current = db.get(Application, application_id)
                allowed = set(control.authorized_domains) if control else set()
                enabled = bool(control and control.enabled and current and current.state not in {"CANCELLED", "PAUSED"})
                has_intent = bool(current and current.submit_intent_at)
                human_action = bool(current and current.state == "NEEDS_HUMAN_ACTION")
            trusted = allowed | (ATS_API_HOSTS.get(provider, set()) if host in allowed else set())
            if request.is_navigation_request() or request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
                safe = safe and p.hostname in trusted and p.scheme == "https" and enabled
            body = request.post_data if "json" in request.headers.get("content-type", "") else None
            final_write = is_submission_request(provider, request.url, request.method, body)
            native_write = request.is_navigation_request() and request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
            authentication = human_action and not final_write and bool(re.search(
                r"(?:^|/)(?:log-?in|sign-?in|sign_in|authenticate|authentication|oauth2?|sessions?|captcha|challenge|verify)(?:/|$)",
                p.path, re.IGNORECASE))
            if final_write or (native_write and not authentication):
                # Block recognized application writes even if an ATS script tries
                # to issue them during preparation or retries after a timeout.
                safe = safe and has_intent and not browser_session.submission_write_seen
                if safe:
                    browser_session.submission_write_seen = True
            if safe:
                await route.continue_()
            else:
                with self.session_factory() as db:
                    app = db.get(Application, application_id)
                    if app:
                        audit(db, "browser_request_blocked", {"host": p.hostname, "method": request.method}, app)
                        app.progress = {**app.progress, "blocked_host": p.hostname}
                        db.commit()
                await route.abort()

        await context.route("**/*", guard)
        # WebSockets bypass HTTP route interception; ATS application forms do
        # not need them. Block rather than permit an unverified data channel.
        await context.route_web_socket("**", lambda socket: socket.close())
        page = await context.new_page()
        browser_session.page = page
        page.set_default_timeout(settings().browser_timeout_ms)
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(700)
        return browser_session

    async def prepare(self, application_id: str) -> None:
        from .tailoring import generate_resume, select_resume
        await self.checkpoint(application_id)
        with self.session_factory() as db:
            app = db.get(Application, application_id)
            assert app is not None
            job, candidate = db.get(Job, app.job_id), db.get(Candidate, 1)
            assert job and candidate
            flow.set_step(db, app, "PREPARING", "Выбираем подходящее загруженное резюме")
            db.commit()
            resume, profile = select_resume(db, job, Profile.model_validate(candidate.facts))
            flow.policy_check(db, app, job, profile)
            app.resume_id = resume.id
            root = settings().data_dir / "artifacts" / app.id / str(app.attempt_id)
            root.mkdir(parents=True, exist_ok=True)
            generated = generate_resume(resume, profile, job, root)
            path = root / "resume.docx"
            if generated != path:
                shutil.copyfile(generated, path)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            app.generated_resume_key = str(path.relative_to(settings().data_dir))
            flow.store_artifact(db, app, path)
            version, attempt_id, aversion = candidate.version, str(app.attempt_id), approved_version(db)
            flow.set_step(db, app, "RESUME_GENERATED", "Подготовлено адаптированное DOCX с подтверждёнными фактами",
                          resume_filename=resume.filename, generated_sha256=digest)
            db.commit()
        browser = self.sessions.get(application_id)
        if browser and (browser.profile_version != version or browser.document_hash != digest or browser.attempt_id != attempt_id):
            await self.close(application_id)
            browser = None
        if browser is None:
            browser = await self.open_browser(application_id, profile, version, digest, attempt_id, aversion)
        browser.expires = time.monotonic() + settings().application_session_seconds
        browser.answers_version = aversion
        await self.checkpoint(application_id)
        with self.session_factory() as db:
            app = db.get(Application, application_id)
            assert app is not None
            flow.set_step(db, app, "ANSWERING_QUESTIONS", "Анализируем форму и готовим подтверждённые ответы", browser_status="active")
            engine = AnswerEngine(profile, {a.question: a.answer for a in db.scalars(select(Answer))})
            job = db.get(Job, app.job_id)
            assert job
            description = job.description
            db.commit()
        answers = await browser.adapter.prepare(browser.page, path.resolve(), engine, description)
        browser.form_snapshot = await browser.adapter.snapshot(browser.page)
        await browser.page.screenshot(path=str(root / "before.png"), full_page=True)
        await self.checkpoint(application_id)
        with self.session_factory() as db:
            app = db.get(Application, application_id)
            candidate, control = db.get(Candidate, 1), db.get(Control, 1)
            assert app and candidate and control
            if candidate.version != version or approved_version(db) != aversion:
                raise NeedsAttention("Профиль или утверждённые ответы изменились; возобновите подготовку")
            app.answers = answers
            flow.store_artifact(db, app, root / "before.png")
            app.progress = {**app.progress, "form_fingerprint": hashlib.sha256(
                json.dumps(browser.form_snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
            app.prepared_fingerprint = flow.fingerprint(app, version, digest, browser.page.url)
            categories = flow.approval_categories(answers)
            autonomous = (app.mode == "autonomous" and control.mode == "autonomous" and control.auto_apply
                          and not (categories & set(control.required_approval_categories))
                          and "all" not in control.required_approval_categories)
            approved = app.approved_fingerprint == app.prepared_fingerprint
            flow.set_step(db, app, "AWAITING_APPROVAL", "Форма заполнена. Проверьте резюме и ответы перед отправкой",
                          questions=[], fields=[], screenshot="before.png", browser_status="waiting",
                          approval_categories=sorted(categories))
            job = db.get(Job, app.job_id)
            assert job
            if not autonomous and not approved:
                flow.notify(db, app, job, "Требуется подтверждение отправки")
            db.commit()
        if autonomous or approved:
            await self.submit(application_id, autonomous=autonomous)

    async def submit(self, application_id: str, *, autonomous: bool = False) -> None:
        browser = self.sessions.get(application_id)
        if browser is None:
            return
        try:
            await self.checkpoint(application_id)
            await browser.adapter.guard(browser.page)
            await browser.adapter.validate_submission(browser.page)
            # Revalidate page before crossing the durable submission fence.
            if await browser.adapter.snapshot(browser.page) != browser.form_snapshot:
                raise NeedsAttention("Содержимое формы изменилось после предпросмотра; возобновите подготовку")
            with self.session_factory() as db:
                if db.get_bind().dialect.name == "postgresql":
                    db.execute(text("SELECT pg_advisory_xact_lock(804222)"))
                else:
                    db.execute(text("BEGIN IMMEDIATE"))
                app = db.get(Application, application_id, populate_existing=True)
                assert app is not None
                job, candidate, control = db.get(Job, app.job_id), db.get(Candidate, 1, with_for_update=True), db.get(Control, 1)
                assert app and job and candidate and control
                if app.state != "AWAITING_APPROVAL":
                    return
                if candidate.version != browser.profile_version or approved_version(db) != browser.answers_version:
                    raise NeedsAttention("Данные изменились после предпросмотра; требуется повторная подготовка")
                root = settings().data_dir / "artifacts" / app.id / str(app.attempt_id)
                if hashlib.sha256((root / "resume.docx").read_bytes()).hexdigest() != browser.document_hash:
                    raise NeedsAttention("Сгенерированное резюме изменилось; требуется повторная подготовка")
                if flow.fingerprint(app, candidate.version, browser.document_hash, browser.page.url) != app.prepared_fingerprint:
                    raise NeedsAttention("Страница или ответы изменились; требуется новый предпросмотр")
                if autonomous:
                    if (control.mode != "autonomous" or not control.auto_apply or
                            flow.approval_categories(app.answers) & set(control.required_approval_categories)
                            or "all" in control.required_approval_categories):
                        return
                elif app.approved_fingerprint != app.prepared_fingerprint:
                    return
                flow.policy_check(db, app, job, browser.profile, submitting=True)
                verified_application_url(browser.page.url, control.authorized_domains)
                changed = db.scalar(update(Application).where(Application.id == app.id,
                    Application.state == "AWAITING_APPROVAL", Application.submit_intent_at.is_(None))
                    .values(submit_intent_at=now(), state="SUBMITTING", command=None).returning(Application.id))
                if not changed:
                    db.rollback()
                    return
                flow.set_step(db, app, "SUBMITTING", "Отправляем подтверждённую заявку; повторная отправка заблокирована")
                audit(db, "submit_intent", {"fingerprint": app.prepared_fingerprint, "attempt_id": app.attempt_id}, app)
                db.commit()
            await browser.adapter.submit(browser.page)
            confirmed = await browser.adapter.verify(browser.page)
            await self.finish(application_id, confirmed)
        except Exception as exc:
            await self.failure(application_id, exc)

    async def finish(self, application_id: str, confirmed: bool) -> None:
        browser = self.sessions.get(application_id)
        with self.session_factory() as db:
            app = db.get(Application, application_id)
            assert app is not None
            job = db.get(Job, app.job_id)
            assert job
            if browser:
                root = settings().data_dir / "artifacts" / app.id / str(app.attempt_id)
                try:
                    await browser.page.screenshot(path=str(root / "after.png"), full_page=True)
                    flow.store_artifact(db, app, root / "after.png")
                    app.progress = {**app.progress, "screenshot": "after.png"}
                except Exception:
                    pass
            if confirmed:
                app.submitted_at = now()
                flow.set_step(db, app, "SUBMITTED", "Сайт подтвердил получение заявки", browser_status="closed",
                              confirmation_url=browser.page.url if browser else None)
                flow.notify(db, app, job, "Отклик отправлен: подтверждено сайтом")
            else:
                flow.set_step(db, app, "SUBMISSION_UNCERTAIN", "Нет однозначного подтверждения сайта. Повторная отправка заблокирована.", browser_status="waiting")
                flow.notify(db, app, job, "Проверьте результат отправки")
            app.command = None
            db.commit()
        if confirmed:
            await self.close(application_id)

    async def verify(self, application_id: str) -> None:
        browser = self.sessions.get(application_id)
        with self.session_factory() as db:
            app = db.get(Application, application_id)
            if not app or app.state != "SUBMISSION_UNCERTAIN":
                return
            app.command = None
            db.commit()
        if browser:
            try:
                await self.finish(application_id, await browser.adapter.verify(browser.page))
                return
            except Exception:
                pass
        with self.session_factory() as db:
            app = db.get(Application, application_id)
            if app:
                flow.set_step(db, app, "SUBMISSION_UNCERTAIN", "Исходная сессия недоступна. Проверьте письмо или статус на сайте; автоматический повтор отключён.")
                db.commit()

    async def failure(self, application_id: str, exc: Exception) -> None:
        browser = self.sessions.get(application_id)
        with self.session_factory() as db:
            app = db.get(Application, application_id)
            if not app:
                return
            if app.state in {"CANCELLED", "PAUSED"}:
                return
            message = str(exc)[:1500] if isinstance(exc, (NeedsAttention, ValueError)) else f"Ошибка браузера: {type(exc).__name__}. Возобновите отклик после проверки."
            state = "SUBMISSION_UNCERTAIN" if app.submit_intent_at else "NEEDS_HUMAN_ACTION" if isinstance(exc, (NeedsAttention, ValueError)) else "FAILED"
            questions = getattr(exc, "questions", [])
            app.answers = getattr(exc, "answers", app.answers)
            if browser and browser.page:
                root = settings().data_dir / "artifacts" / app.id / str(app.attempt_id)
                try:
                    await browser.page.screenshot(path=str(root / "error.png"), full_page=True)
                    flow.store_artifact(db, app, root / "error.png")
                    app.progress = {**app.progress, "screenshot": "error.png"}
                except Exception:
                    pass
            flow.set_step(db, app, state, message, fields=questions,
                          questions=[q.get("question", "") for q in questions],
                          browser_status="waiting" if browser else "closed")
            app.command = None
            job = db.get(Job, app.job_id)
            if job:
                flow.notify(db, app, job, "Требуется действие: " + message[:500])
            db.commit()
        if state == "FAILED":
            await self.close(application_id)
