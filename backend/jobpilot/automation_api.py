"""Authenticated automation controls; no browser/queue dependency at import time."""
import importlib.util
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import automation
from .config import settings
from .db import session
from .http import authenticate, serialize
from .models import Application, ApplicationArtifact, Control, Job
from .security import ATS_HOSTS
from .services import audit

router = APIRouter(prefix="/automation", dependencies=[Depends(authenticate)])


@lru_cache
def browser_available() -> bool:
    if importlib.util.find_spec("playwright") is None:
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            return Path(pw.chromium.executable_path).is_file()
    except Exception:
        return False


class AutomationSettings(BaseModel):
    enabled: bool = True
    mode: Literal["manual", "assisted", "autonomous"] = "manual"
    auto_apply: bool = False
    min_score: int = Field(default=80, ge=0, le=100)
    daily_limit: int = Field(default=20, ge=1, le=100)
    authorized_domains: list[str] = Field(default_factory=list, max_length=100)
    use_supported_sites: bool = False
    excluded_companies: list[str] = Field(default_factory=list, max_length=200)
    required_approval_categories: list[str] = Field(default_factory=lambda: ["legal", "salary", "personal"])

    @field_validator("required_approval_categories")
    @classmethod
    def categories(cls, values: list[str]) -> list[str]:
        if set(values) - {"legal", "salary", "personal", "all"}:
            raise ValueError("Неизвестная категория обязательного подтверждения")
        return sorted(set(values))

    @field_validator("authorized_domains")
    @classmethod
    def domains(cls, values: list[str]) -> list[str]:
        result = []
        for value in values:
            host = value.strip().lower()
            if not re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", host):
                raise ValueError("Укажите точные домены без URL, порта или *")
            result.append(host)
        return sorted(set(result))

    @field_validator("excluded_companies")
    @classmethod
    def companies(cls, values: list[str]) -> list[str]:
        if any(len(value) > 200 for value in values):
            raise ValueError("Название компании слишком длинное")
        return sorted({value.strip() for value in values if value.strip()})


@router.get("/settings")
def get_settings(db: Session = Depends(session)) -> dict[str, Any]:
    control = db.get(Control, 1)
    assert control is not None
    return {**serialize(control), "browser_available": browser_available(),
            "supported_domains": sorted(set().union(*ATS_HOSTS.values())),
            "browser_headless": settings().application_browser_headless}


@router.put("/settings")
def put_settings(body: AutomationSettings, db: Session = Depends(session)) -> dict[str, Any]:
    if body.use_supported_sites:
        body.authorized_domains = sorted(set(body.authorized_domains).union(*ATS_HOSTS.values()))
    if body.mode == "autonomous" and (not body.auto_apply or not body.authorized_domains):
        raise HTTPException(422, "Включите автоотправку и выберите поддерживаемые сайты или добавьте сайт работодателя")
    if body.mode != "autonomous" and body.auto_apply:
        raise HTTPException(422, "auto_apply разрешён только в автономном режиме")
    control = db.get(Control, 1)
    assert control is not None
    for key, value in body.model_dump(exclude={"use_supported_sites"}).items():
        setattr(control, key, value)
    audit(db, "application_policy_updated", body.model_dump())
    db.commit()
    automation.work_available.set()
    return get_settings(db)


class ApplicationInput(BaseModel):
    canonical_url: str = Field(min_length=8, max_length=2000)


@router.post("/applications")
def prepare(body: ApplicationInput, db: Session = Depends(session)) -> dict[str, Any]:
    try:
        job = automation.resolve_job(db, body.canonical_url)
        existing = db.scalar(select(Application).where(Application.job_id == job.id))
        if existing and existing.attempt_id:
            return automation.detail(db, existing)
        control = db.get(Control, 1)
        mode = control.mode if control and control.enabled else "manual"
        app = automation.request_application(db, job, mode)
        db.commit()
        automation.work_available.set()
        return automation.detail(db, app)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Вакансия уже обрабатывается; обновите историю откликов") from exc


@router.get("/applications")
def applications(canonical_url: str = Query(default="", max_length=2000), db: Session = Depends(session),
                 summary: bool = False) -> list[dict[str, Any]]:
    rows = [(app, job) for app, job in db.execute(select(Application, Job).join(Job)
            .where(Application.attempt_id.is_not(None)).order_by(Application.updated_at.desc()).limit(500))]
    if canonical_url:
        rows = [(app, job) for app, job in rows
                if automation.canonical(job.canonical_url) == automation.canonical(canonical_url)]
    if summary:
        return [{"id": app.id, "state": app.state, "updated_at": app.updated_at,
                 "job": {"title": job.title, "company": job.company, "canonical_url": job.canonical_url}}
                for app, job in rows]
    return [automation.detail(db, app) for app, _ in rows]


def get_application(db: Session, application_id: str) -> Application:
    app = db.get(Application, application_id)
    if not app:
        raise HTTPException(404, "Отклик не найден")
    return app


@router.get("/applications/{application_id}")
def application(application_id: str, db: Session = Depends(session)) -> dict[str, Any]:
    return automation.detail(db, get_application(db, application_id))


class ActionInput(BaseModel):
    action: Literal["approve", "pause", "resume", "cancel", "verify"]
    approval_token: str | None = Field(default=None, max_length=64)


@router.post("/applications/{application_id}/action")
def action(application_id: str, body: ActionInput, db: Session = Depends(session)) -> dict[str, Any]:
    app = get_application(db, application_id)
    try:
        automation.command(db, app, body.action, body.approval_token)
        db.commit()
        automation.work_available.set()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    return automation.detail(db, app)


class ConfirmedAnswer(BaseModel):
    id: str = Field(pattern=r"^[a-f0-9]{64}$")
    answer: str = Field(min_length=1, max_length=10000)
    remember: bool = False


class ApplicationAnswers(BaseModel):
    answer_token: str = Field(min_length=64, max_length=64)
    answers: list[ConfirmedAnswer] = Field(min_length=1, max_length=100)
    resume: bool = True


@router.post("/applications/{application_id}/answers")
def confirm_answers(application_id: str, body: ApplicationAnswers, db: Session = Depends(session)) -> dict[str, Any]:
    app = get_application(db, application_id)
    try:
        automation.save_answers(db, app, body.answer_token,
                                [item.model_dump() for item in body.answers], resume=body.resume)
        db.commit()
        automation.work_available.set()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    return automation.detail(db, app)


@router.get("/applications/{application_id}/files/{filename}")
def file(application_id: str, filename: str, db: Session = Depends(session)) -> Response:
    import uuid
    app = get_application(db, application_id)
    try:
        uuid.UUID(app.id)
        uuid.UUID(str(app.attempt_id))
    except ValueError as exc:
        raise HTTPException(404) from exc
    if filename not in {"resume.docx", "resume.pdf", "before.png", "after.png", "error.png"}:
        raise HTTPException(404)
    root = settings().data_dir / "artifacts" / app.id / str(app.attempt_id)
    path = root / filename
    media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if filename.endswith("docx") else "image/png"
    if filename.endswith(".pdf"):
        media = "application/pdf"
    stored = db.get(ApplicationArtifact, (app.id, str(app.attempt_id), filename))
    if stored:
        return Response(stored.content, media_type=media, headers={"Content-Disposition": f'inline; filename="{filename}"',
                                                                "Cache-Control": "private, no-store"})
    if not path.is_file():
        raise HTTPException(404, "Файл недоступен на этом экземпляре сервера")
    return FileResponse(path, media_type=media, filename=filename, headers={"Cache-Control": "private, no-store"})
