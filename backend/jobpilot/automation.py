"""Application policy and durable commands, shared by HTTP and the browser runtime.

Only a positive browser observation may complete a submission. A committed
submit_intent_at is an irreversible fence against all automatic re-submission.
"""
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from .config import settings
from .domain import JobInput, Profile, evaluate
from .http import serialize
from .models import Application, ApplicationArtifact, Control, Job, SavedSearch, Source, TopVacancy, now, uid
from .progress import advance
from .security import verified_application_url
from .services import audit, job_input

ACTIVE_STATES = {"QUEUED", "PREPARING", "RESUME_GENERATED", "ANSWERING_QUESTIONS", "FILLING_FORM"}
WAIT_STATES = {"AWAITING_APPROVAL", "NEEDS_HUMAN_ACTION", "PAUSED"}
FINAL_STATES = {"SUBMITTED", "SUBMISSION_UNCERTAIN", "SUBMITTING", "APPLYING", "INTERVIEW", "OFFER", "REJECTED_BY_EMPLOYER"}


def store_artifact(db: Session, app: Application, path: Path) -> None:
    """Use the existing DB-backed upload policy on ephemeral hosted filesystems."""
    if not settings().store_resume_files_in_db:
        return
    key = (app.id, str(app.attempt_id), path.name)
    item = db.get(ApplicationArtifact, key)
    if item:
        item.content = path.read_bytes()
    else:
        db.add(ApplicationArtifact(application_id=app.id, attempt_id=str(app.attempt_id),
                                   filename=path.name, content=path.read_bytes()))


def canonical(url: str) -> str:
    p = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.casefold().startswith("utm_") and k.casefold() not in {"source", "ref", "referrer", "gh_src"}]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), urlencode(sorted(query)), ""))


def set_step(db: Session, app: Application, state: str, message: str, **extra: Any) -> None:
    before = app.state
    app.state, app.updated_at = state, now()
    app.progress = advance(app.progress, state, message, step=state, **extra)
    app.attention = message if state in {"NEEDS_HUMAN_ACTION", "FAILED", "SUBMISSION_UNCERTAIN"} else None
    audit(db, "application_progress", {"from": before, "to": state, "message": message}, app)


def notify(db: Session, app: Application, job: Job, message: str) -> None:
    """Reuse the search notification queue and its rate-limit aware delivery."""
    from .models import SearchNotification
    from .notifications import channels
    if "telegram" not in channels():
        return
    search_id = db.scalar(select(SavedSearch.id).where(SavedSearch.resume_id == app.resume_id).limit(1))
    search_id = search_id or db.scalar(select(SavedSearch.id).limit(1))
    if not search_id:
        return
    key = hashlib.sha256(f"application:{app.id}:{app.attempt_id}:{app.state}:{message}".encode()).hexdigest()
    if db.get(SearchNotification, key) is None:
        db.add(SearchNotification(id=key, search_id=search_id, channel="telegram",
                                 message=f"JobPilot · {message}\n{job.title} — {job.company}\n{job.canonical_url}"[:4096]))


def resolve_job(db: Session, url: str) -> Job:
    """The client supplies a URL, never job facts, a qualification score or a provider."""
    key = canonical(url)
    jobs = list(db.scalars(select(Job)))
    existing = next((j for j in jobs if canonical(j.canonical_url) == key), None)
    if existing:
        return existing
    record: dict[str, Any] | None = None
    for row in db.scalars(select(TopVacancy).order_by(TopVacancy.score.desc())):
        if canonical(row.data.get("canonical_url", "")) == key:
            record = row.data
            break
    if record is None:
        for search in db.scalars(select(SavedSearch)):
            record = next((r for r in search.results if canonical(r.get("canonical_url", "")) == key), None)
            if record:
                break
    if record is None:
        raise ValueError("Вакансия не найдена в сохранённых результатах JobPilot")
    data = JobInput.model_validate({k: record.get(k) for k in JobInput.model_fields if k in record})
    source = db.scalar(select(Source).where(Source.provider == "manual", Source.board == "automation"))
    if source is None:
        source = Source(provider="manual", board="automation", company="Сохранённые результаты", enabled=False)
        db.add(source)
        db.flush()
    # Feed external IDs are not globally unique; the URL is the application key.
    data = data.model_copy(update={"external_id": "apply:" + hashlib.sha256(key.encode()).hexdigest(), "canonical_url": key})
    job = Job(source_id=source.id, **data.model_dump(), score=0, eligibility="unknown", explanation={})
    db.add(job)
    db.flush()
    return job


def duplicate_reason(db: Session, job: Job, app: Application) -> str | None:
    if app.submit_intent_at or app.submitted_at:
        return "Отправка уже начата; повторная отправка заблокирована. Проверьте подтверждение."
    if job.duplicate_of:
        return "Вакансия отмечена как возможный дубликат; требуется проверка в истории"
    peers = db.execute(select(Application, Job).join(Job).where(Application.id != app.id,
        (Application.submit_intent_at.is_not(None)) | (Application.submitted_at.is_not(None)))).all()
    for other, peer in peers:
        if (canonical(peer.canonical_url) == canonical(job.canonical_url) or
                (peer.company.strip().casefold() == job.company.strip().casefold()
                 and peer.title.strip().casefold() == job.title.strip().casefold()
                 and peer.location.strip().casefold() == job.location.strip().casefold())):
            return f"Отклик на эту вакансию уже отправлен или требует проверки ({other.id})"
    return None


def request_application(db: Session, job: Job, mode: str = "manual") -> Application:
    app = db.scalar(select(Application).where(Application.job_id == job.id))
    if app is None:
        app = Application(job_id=job.id, state="MATCHED", mode=mode)
        db.add(app)
        db.flush()
    reason = duplicate_reason(db, job, app)
    if reason:
        raise ValueError(reason)
    if app.state in ACTIVE_STATES | WAIT_STATES:
        return app
    if app.state in FINAL_STATES:
        raise ValueError("Отклик уже обрабатывается или отправлен")
    app.mode, app.attempt_id, app.command = mode, uid(), None
    app.resume_id = None
    app.prepared_fingerprint = app.approved_fingerprint = app.generated_resume_key = None
    app.answers, app.progress = [], {}
    set_step(db, app, "QUEUED", "Отклик ожидает браузерный исполнитель")
    return app


def submission_budget(db: Session, control: Control) -> tuple[int, datetime]:
    zone = ZoneInfo(settings().search_timezone)
    local_start = now().astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    count = db.scalar(select(func.count()).select_from(Application).where(
        Application.submit_intent_at >= local_start.astimezone(UTC))) or 0
    return max(0, control.daily_limit - count), (local_start + timedelta(days=1)).astimezone(UTC)


def policy_check(db: Session, app: Application, job: Job, profile: Profile, *, submitting: bool = False) -> None:
    from .answers import NeedsAttention
    control = db.get(Control, 1, populate_existing=True)
    if not control or not control.enabled:
        raise NeedsAttention("Автоматизация на паузе; включите её в настройках")
    verified_application_url(job.canonical_url, control.authorized_domains)
    if job.company.strip().casefold() in {c.strip().casefold() for c in control.excluded_companies}:
        raise NeedsAttention("Компания исключена в настройках откликов")
    reason = duplicate_reason(db, job, app)
    if reason:
        raise NeedsAttention(reason)
    result = evaluate(job_input(job), profile)
    job.score, job.eligibility, job.explanation = result["score"], result["eligibility"], result
    if result["eligibility"] != "eligible":
        raise NeedsAttention("Подтвердите место проживания и право на работу в профиле; допуск к вакансии не установлен")
    if not result.get("recommendable", True):
        raise NeedsAttention("Не подтверждены обязательные требования: " + "; ".join(result.get("blockers", [])))
    if app.mode != "manual" and result["score"] < control.min_score:
        raise NeedsAttention("Оценка ниже порога автоматического отбора")
    if not profile.full_name or not profile.email:
        raise NeedsAttention("Укажите полное имя и email в профиле")
    if submitting:
        from .answers import RetryLater
        remaining, reset_at = submission_budget(db, control)
        if not remaining:
            raise RetryLater("Достигнут дневной лимит отправок. Продолжим автоматически завтра.", reset_at)


def approval_categories(answers: list[dict[str, Any]]) -> set[str]:
    from .answers import LEGAL
    result: set[str] = set()
    for item in answers:
        q = item.get("question", "")
        if LEGAL.search(q):
            result.add("legal")
        if re.search(r"salary|compensation|pay|зарплат|оклад|вознагражд", q, re.IGNORECASE):
            result.add("salary")
        if re.search(r"gender|ethnic|veteran|disab|birth|nationality|гражданств|возраст|инвалид|пол\b", q, re.IGNORECASE):
            result.add("personal")
    return result


def fingerprint(app: Application, profile_version: int, resume_hash: str, url: str) -> str:
    return hashlib.sha256(json.dumps({"answers": app.answers, "profile_version": profile_version,
        "resume": resume_hash, "url": url, "attempt_id": app.attempt_id,
        "form": app.progress.get("form_fingerprint")}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def command(db: Session, app: Application, action: str, approval_token: str | None = None) -> None:
    # Serialize commands with the final submit fence, then discard any stale
    # identity-map copy fetched before the other transaction committed.
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(804222)"))
    else:
        connection = db.connection().connection.driver_connection
        if not getattr(connection, "in_transaction", False):
            db.execute(text("BEGIN IMMEDIATE"))
    db.refresh(app, with_for_update=True)
    if action == "verify":
        if app.state != "SUBMISSION_UNCERTAIN":
            raise ValueError("Проверка доступна для отправки с неопределённым результатом")
        app.command = "verify"
    elif app.submit_intent_at or app.state in FINAL_STATES:
        raise ValueError("Отправка уже началась: доступны только проверка результата и история")
    elif action == "approve":
        if app.state != "AWAITING_APPROVAL" or not approval_token or approval_token != app.prepared_fingerprint:
            raise ValueError("Предпросмотр изменился; обновите отклик и подтвердите актуальную версию")
        app.approved_fingerprint = app.prepared_fingerprint
        app.command = "approve"
    elif action == "pause":
        app.command = "pause"
        set_step(db, app, "PAUSED", "Отклик приостановлен пользователем")
    elif action == "cancel":
        app.command = "cancel"
        set_step(db, app, "CANCELLED", "Отклик отменён пользователем")
    elif action == "resume":
        if app.state not in WAIT_STATES | {"FAILED", "CANCELLED"}:
            raise ValueError("Отклик уже выполняется")
        app.command = "resume"
        app.approved_fingerprint = None
        app.progress = {**app.progress, "retry_count": 0, "retry_at": None, "error_code": None}
        set_step(db, app, "QUEUED", "Возобновляем подготовку и проверяем актуальность данных")
    else:
        raise ValueError("Неизвестное действие")
    audit(db, "application_command", {"action": action}, app)


def recover(db: Session, *, stale_only: bool = False) -> None:
    query = select(Application).where(Application.state.in_(ACTIVE_STATES | {"SUBMITTING"}))
    if stale_only:
        query = query.where(Application.updated_at < now() - timedelta(seconds=settings().application_timeout_seconds + 60))
    for app in db.scalars(query):
        if app.submit_intent_at:
            set_step(db, app, "SUBMISSION_UNCERTAIN", "Процесс прерван после начала отправки. Повторный Submit заблокирован.")
        else:
            # Persisted queued jobs can be safely reconstructed, without a submit.
            app.approved_fingerprint = None
            set_step(db, app, "QUEUED", "Восстановление подготовки после перезапуска")
    db.commit()


def detail(db: Session, app: Application) -> dict[str, Any]:
    job = db.get(Job, app.job_id)
    result = serialize(app)
    root = f"/automation/applications/{app.id}/files"
    result.update(job=serialize(job) if job else {}, approval_token=app.prepared_fingerprint,
                  generated_resume_url=f"{root}/resume.docx" if app.generated_resume_key else None,
                  screenshot_url=f"{root}/{app.progress['screenshot']}" if app.progress.get("screenshot") else None)
    return result


def claim(db: Session, application_id: str) -> bool:
    claimed = db.scalar(update(Application).where(Application.id == application_id, Application.state == "QUEUED",
        Application.submit_intent_at.is_(None)).values(state="PREPARING", command=None, updated_at=now()).returning(Application.id))
    db.commit()
    return bool(claimed)
