"""Application policy and durable commands, shared by HTTP and the browser runtime.

Only a positive browser observation may complete a submission. A committed
submit_intent_at is an irreversible fence against all automatic re-submission.
"""
import hashlib
import json
import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from .config import settings
from .domain import JobInput, Profile, evaluate
from .http import serialize
from .models import (
    Application,
    ApplicationArtifact,
    Candidate,
    Control,
    Job,
    SavedSearch,
    Source,
    TopVacancy,
    now,
    uid,
)
from .progress import advance
from .security import verified_application_url
from .services import audit, job_input
from .vacancy_identity import canonical_url, identity, same_vacancy

ACTIVE_STATES = {"QUEUED", "PREPARING", "RESUME_GENERATED", "ANSWERING_QUESTIONS", "FILLING_FORM"}
WAIT_STATES = {"AWAITING_APPROVAL", "NEEDS_HUMAN_ACTION", "PAUSED"}
FINAL_STATES = {"SUBMITTED", "SUBMISSION_UNCERTAIN", "SUBMITTING", "APPLYING", "INTERVIEW", "OFFER", "REJECTED_BY_EMPLOYER"}
work_available = threading.Event()


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
    return canonical_url(url)


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
    from .vacancy_review import is_rejected
    if is_rejected(db, job_input(job).model_dump(mode="json")):
        return
    search_id = db.scalar(select(SavedSearch.id).where(SavedSearch.resume_id == app.resume_id).limit(1))
    search_id = search_id or db.scalar(select(SavedSearch.id).limit(1))
    if not search_id:
        return
    from .handoff import questions_and_answers, redact
    candidate = db.get(Candidate, 1)
    facts = candidate.facts if candidate else {}
    lines = [f"JobPilot · {redact(message, facts)}", f"{job.title} — {job.company}",
             f"Вакансия: {job.canonical_url}"]
    if app.progress.get("resume_filename"):
        lines.append("Выбрано резюме: " + redact(app.progress["resume_filename"], facts))
    if settings().public_app_url.startswith("https://"):
        lines.append(f"Отклик и ответы: {settings().public_app_url.rstrip('/')}/?application={app.id}")
    if app.state in {"NEEDS_HUMAN_ACTION", "FAILED", "AWAITING_APPROVAL"}:
        url = (app.progress or {}).get("application_url")
        if url and url != job.canonical_url:
            lines.append(f"Форма: {url}")
        handoff = questions_and_answers(app.answers or [], (app.progress or {}).get("fields", []), facts)
        for index, item in enumerate(handoff, 1):
            detail = f"{index}. Вопрос: {item['question']}\nОтвет: {item['answer'] or 'требуется уточнение'}"
            if item["options"]:
                detail += "\nВарианты: " + ", ".join(item["options"])
            lines.append(detail)
        if not handoff and app.state != "AWAITING_APPROVAL":
            lines.append("Дополнительные вопросы пока не получены. Откройте форму и продолжите вручную.")
        if answer_fields(app, facts):
            lines.append("Недостающие ответы можно сохранить в карточке отклика по ссылке выше.")
    # Telegram limits each message to 4096 UTF-16 code units. Split without
    # dropping long answers; a conservative character limit handles emoji too.
    body = "\n\n".join(lines)
    chunks = [body[i:i + 1900] for i in range(0, len(body), 1900)] or [body]
    for index, chunk in enumerate(chunks):
        key = hashlib.sha256(f"application:{app.id}:{app.attempt_id}:{app.state}:{body}:{index}".encode()).hexdigest()
        if db.get(SearchNotification, key) is None:
            prefix = f"JobPilot · часть {index + 1}/{len(chunks)}\n" if len(chunks) > 1 else ""
            db.add(SearchNotification(id=key, search_id=search_id, channel="telegram", message=prefix + chunk))



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
    from .vacancy_review import is_rejected
    data = job_input(job).model_dump(mode="json")
    if app.state in {"REJECTED", "REJECTED_BY_EMPLOYER", "CANCELLED"} or is_rejected(db, data):
        return "Вакансия уже отклонена или отклик отменён; повторная отправка заблокирована"
    if app.submit_intent_at or app.submitted_at:
        return "Отправка уже начата; повторная отправка заблокирована. Проверьте подтверждение."
    if job.duplicate_of:
        return "Вакансия отмечена как возможный дубликат; требуется проверка в истории"
    peers = db.execute(select(Application, Job).join(Job).where(Application.id != app.id,
        (Application.submit_intent_at.is_not(None)) | (Application.submitted_at.is_not(None)) |
        Application.state.in_(ACTIVE_STATES | WAIT_STATES | FINAL_STATES | {"REJECTED", "CANCELLED"}))).all()
    for other, peer in peers:
        if same_vacancy(job_input(peer).model_dump(), data):
            if other.state in {"REJECTED", "REJECTED_BY_EMPLOYER", "CANCELLED"}:
                return f"Вакансия уже отклонена или отклик отменён ({other.id})"
            return f"Отклик на эту вакансию уже отправлен или требует проверки ({other.id})"
    return None


def request_application(db: Session, job: Job, mode: str = "manual") -> Application:
    if db.get_bind().dialect.name == "postgresql":
        # Different URLs for one role must not race into separate browser jobs.
        key = int(identity(job_input(job).model_dump())[:15], 16)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
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
    from .vacancy_review import approved_for_preparation
    confirmed_review = approved_for_preparation(db, job_input(job).model_dump(mode="json"))
    if result["eligibility"] != "eligible" and not (
            result["eligibility"] == "unknown" and confirmed_review and not submitting):
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


def answer_fields(app: Application, facts: dict[str, Any]) -> list[dict[str, Any]]:
    from .answers import normalize
    from .handoff import questions_and_answers
    confirmed = app.progress.get("confirmed_answers", {})
    prepared = {normalize(item.get("question", "")): item.get("answer") for item in app.answers or []}
    result: dict[str, dict[str, Any]] = {}
    for field in app.progress.get("fields", []):
        items = questions_and_answers([], [field], facts)
        if not items or field.get("type") == "file":
            continue
        question = str(field.get("question") or "").strip()
        key = hashlib.sha256(normalize(question).encode()).hexdigest()
        result[key] = {**items[0], "id": key, "required": bool(field.get("required")),
                       "answer": confirmed.get(key, {}).get("answer", prepared.get(normalize(question))) or ""}
    return list(result.values())


def answer_token(app: Application) -> str:
    return hashlib.sha256(json.dumps({"attempt": app.attempt_id, "state": app.state,
        "fields": app.progress.get("fields", []), "confirmed": app.progress.get("confirmed_answers", {}),
        "answers": app.answers}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def lock_application(db: Session, app: Application) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(804222)"))
    else:
        connection = db.connection().connection.driver_connection
        if not getattr(connection, "in_transaction", False):
            db.execute(text("BEGIN IMMEDIATE"))
    db.refresh(app, with_for_update=True)


def save_answers(db: Session, app: Application, token: str, values: list[dict[str, Any]], *, resume: bool) -> None:
    """Atomically accept owner answers for the current form, scoped by default."""
    from .answers import normalize
    from .models import Answer
    lock_application(db, app)
    if app.submit_intent_at or app.submitted_at or app.state not in {"NEEDS_HUMAN_ACTION", "FAILED", "PAUSED"}:
        raise ValueError("Ответы можно изменить только в остановленной подготовке до отправки")
    if token != answer_token(app):
        raise ValueError("Вопросы или ответы изменились; обновите карточку отклика")
    candidate = db.get(Candidate, 1)
    allowed = {item["id"] for item in answer_fields(app, candidate.facts if candidate else {})}
    questions = {hashlib.sha256(normalize(str(item.get("question") or "")).encode()).hexdigest(): item
                 for item in app.progress.get("fields", [])}
    keys = [item["id"] for item in values]
    if not keys or len(set(keys)) != len(keys) or set(keys) - allowed:
        raise ValueError("Сохраняйте ответы только на актуальные вопросы этой формы")
    confirmed = dict(app.progress.get("confirmed_answers", {}))
    library = {normalize(item.question): item for item in db.scalars(select(Answer))}
    for value in values:
        field = questions[value["id"]]
        question, answer = field["question"], value["answer"].strip()
        if not answer:
            raise ValueError("Ответ не должен быть пустым")
        options = field.get("options") or []
        if options and normalize(answer) not in {normalize(str(option)) for option in options}:
            raise ValueError("Выберите один из вариантов работодателя")
        confirmed[value["id"]] = {"question": question, "answer": answer}
        if value.get("remember"):
            saved = library.get(normalize(question))
            if saved:
                saved.answer = answer
            else:
                saved = Answer(question=question, answer=answer)
                db.add(saved)
                library[normalize(question)] = saved
    app.progress = {**app.progress, "confirmed_answers": confirmed}
    app.prepared_fingerprint = app.approved_fingerprint = None
    audit(db, "application_answers_confirmed", {"question_ids": keys,
          "remembered": [item["id"] for item in values if item.get("remember")]}, app)
    if resume:
        db.flush()
        command(db, app, "resume")


def command(db: Session, app: Application, action: str, approval_token: str | None = None) -> None:
    # Serialize commands with the final submit fence, then discard any stale
    # identity-map copy fetched before the other transaction committed.
    lock_application(db, app)
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
    from .handoff import questions_and_answers
    job = db.get(Job, app.job_id)
    result = serialize(app)
    candidate = db.get(Candidate, 1)
    result["handoff"] = questions_and_answers(app.answers or [], (app.progress or {}).get("fields", []),
                                               candidate.facts if candidate else {})
    result["answer_fields"] = answer_fields(app, candidate.facts if candidate else {})
    result["answer_token"] = answer_token(app)
    root = f"/automation/applications/{app.id}/files"
    resume_filename = "resume.pdf" if app.progress.get("uploaded_resume_filename") == "resume.pdf" else "resume.docx"
    result.update(job=serialize(job) if job else {}, approval_token=app.prepared_fingerprint,
                  application_url=app.progress.get("application_url") or (job.canonical_url if job else None),
                  generated_resume_url=f"{root}/{resume_filename}" if app.generated_resume_key else None,
                  screenshot_url=f"{root}/{app.progress['screenshot']}" if app.progress.get("screenshot") else None)
    return result


def claim(db: Session, application_id: str) -> bool:
    claimed = db.scalar(update(Application).where(Application.id == application_id, Application.state == "QUEUED",
        Application.submit_intent_at.is_(None)).values(state="PREPARING", command=None, updated_at=now()).returning(Application.id))
    db.commit()
    return bool(claimed)
