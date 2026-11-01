from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import session
from .domain import JobInput, Profile, evaluate
from .http import authenticate, serialize
from .models import (
    Application,
    Audit,
    Candidate,
    Control,
    Job,
    Outbox,
    Source,
    now,
    uid,
)
from .services import (
    MINIMUM_QUALIFICATION_SCORE,
    audit,
    job_input,
    readiness,
    rescore,
    transition,
)

router = APIRouter(dependencies=[Depends(authenticate)])


@router.get("/jobs")
def jobs(
    min_score: int = Query(default=0, ge=0, le=100),
    q: str = Query(default="", max_length=300),
    eligibility: Literal["eligible", "unknown", "rejected"] | None = None,
    state: str | None = Query(default=None, max_length=40),
    provider: Literal["greenhouse", "ashby", "lever"] | None = None,
    company: str = Query(default="", max_length=200),
    location: str = Query(default="", max_length=200),
    work_mode: Literal["remote", "hybrid", "onsite"] | None = None,
    has_salary: bool | None = None,
    sort: Literal["score_desc", "score_asc", "newest", "oldest", "company_asc"] = "score_desc",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(session),
) -> dict[str, Any]:
    query = select(
        Job.id.label("id"), Job.company.label("company"), Job.title.label("title"),
        Job.location.label("location"), Job.salary.label("salary"), Job.provider.label("provider"),
        Job.score.label("score"), Job.eligibility.label("eligibility"),
        Job.canonical_url.label("canonical_url"), Job.published_at.label("published_at"),
        Job.discovered_at.label("discovered_at"), Job.duplicate_of.label("duplicate_of"),
        Application.id.label("application_id"), Application.state.label("application_state"),
        Application.attention.label("application_attention"),
    ).join(Application).where(
        Job.score >= min_score
    )
    def contains(column: Any, value: str) -> Any:
        # User text is a literal substring, not a SQL LIKE wildcard expression.
        escaped = value.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return column.ilike(f"%{escaped}%", escape="\\")

    if q:
        query = query.where(contains(Job.title, q) | contains(Job.company, q) | contains(Job.description, q))
    if eligibility:
        query = query.where(Job.eligibility == eligibility)
    if state:
        query = query.where(Application.state == state)
    if provider:
        query = query.where(Job.provider == provider)
    if company:
        query = query.where(contains(Job.company, company))
    if location:
        query = query.where(contains(Job.location, location))
    if has_salary is True:
        query = query.where(Job.salary.is_not(None), Job.salary != "")
    elif has_salary is False:
        query = query.where(Job.salary.is_(None) | (Job.salary == ""))
    if work_mode:
        posted = func.lower(Job.location)
        description = func.lower(Job.description)
        remote_location = posted.contains("remote") | posted.contains("удалён") | posted.contains("удален")
        hybrid = posted.contains("hybrid") | posted.contains("гибрид")
        onsite = posted.contains("onsite") | posted.contains("on-site") | posted.contains("офис")
        # A remote advert may describe company offices. Its posted format wins;
        # inspect only explicit role phrases when the location lacks a format.
        unclassified = ~(remote_location | hybrid | onsite)
        remote = remote_location | (unclassified & (description.contains("fully remote") | description.contains("100% remote") | description.contains("this role is remote")))
        hybrid = hybrid | (unclassified & description.contains("this role is hybrid"))
        onsite = onsite | (unclassified & (description.contains("this role is onsite") | description.contains("this role is on-site")))
        query = query.where({"remote": remote & ~hybrid & ~onsite, "hybrid": hybrid, "onsite": onsite & ~hybrid}[work_mode])
    sort_options: dict[str, tuple[Any, ...]] = {
        "score_desc": (Job.score.desc(), Job.discovered_at.desc()),
        "score_asc": (Job.score.asc(), Job.discovered_at.desc()),
        "newest": (func.coalesce(Job.published_at, Job.discovered_at).desc(), Job.score.desc()),
        "oldest": (func.coalesce(Job.published_at, Job.discovered_at).asc(), Job.score.desc()),
        "company_asc": (func.lower(Job.company).asc(), Job.score.desc()),
    }
    ordering = sort_options[sort]
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(
        query.order_by(*ordering, Job.id.asc()).offset(offset).limit(limit)
    )
    return {
        "total": total,
        "items": [
            {
                **{key: row[key] for key in (
                    "id", "company", "title", "location", "salary", "provider", "score",
                    "eligibility", "canonical_url", "published_at", "discovered_at", "duplicate_of",
                )},
                "application": {
                    "id": row["application_id"], "state": row["application_state"],
                    "attention": row["application_attention"],
                },
            }
            for row in rows.mappings()
        ],
    }


@router.get("/jobs/{job_id}")
def job_detail(job_id: str, db: Session = Depends(session)) -> dict[str, Any]:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    application = db.scalar(select(Application).where(Application.job_id == job_id))
    assert application is not None
    history = db.scalars(select(Audit).where(Audit.application_id == application.id).order_by(Audit.id))
    return {
        **serialize(job),
        "application": serialize(application),
        "history": [serialize(e) for e in history],
    }


class JobAction(BaseModel):
    action: Literal["rescore", "skip", "apply", "retry", "confirm_eligible", "clear_duplicate"]
    reason: str = ""


class ManualJobInput(BaseModel):
    company: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    location: str = Field(min_length=1, max_length=400)
    canonical_url: str = Field(min_length=1, max_length=2000)
    description: str = Field(min_length=1, max_length=50000)
    salary: str | None = Field(default=None, max_length=500)
    published_at: datetime | None = None


@router.post("/jobs/manual")
def add_manual_job(body: ManualJobInput, db: Session = Depends(session)) -> dict[str, Any]:
    from urllib.parse import urlparse

    url = body.canonical_url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(422, "Укажите корректную ссылку на вакансию")
    if db.scalar(select(Job.id).where(Job.canonical_url == url)):
        raise HTTPException(409, "Такая вакансия уже добавлена")
    candidate = db.get(Candidate, 1)
    if candidate is None:
        raise HTTPException(409, "Профиль кандидата отсутствует")
    profile = Profile.model_validate(candidate.facts)
    source = db.scalar(select(Source).where(Source.provider == "manual", Source.board == "manual"))
    if source is None:
        source = Source(provider="manual", board="manual", company="Добавлено вручную", enabled=False)
        db.add(source)
        db.flush()
    job_data = JobInput(
        provider="manual", external_id=uid(), canonical_url=url,
        company=body.company.strip(), title=body.title.strip(), location=body.location.strip(),
        description=body.description.strip(), salary=body.salary.strip() if body.salary else None,
        published_at=body.published_at,
    )
    if not all((job_data.company, job_data.title, job_data.location, job_data.description)):
        raise HTTPException(422, "Заполните компанию, должность, местоположение и описание")
    result = evaluate(job_data, profile)
    peers = db.scalars(select(Job).where(func.lower(Job.company) == body.company.strip().lower())).all()
    from rapidfuzz.fuzz import ratio
    duplicate = next((peer for peer in peers if ratio(peer.title.lower(), body.title.strip().lower()) >= 93
                      and ratio(peer.location.lower(), body.location.strip().lower()) >= 85), None)
    job = Job(source_id=source.id, **job_data.model_dump(), score=result["score"],
              eligibility=result["eligibility"], explanation=result,
              duplicate_of=duplicate.id if duplicate else None)
    db.add(job)
    db.flush()
    application = Application(job_id=job.id)
    db.add(application)
    db.flush()
    transition(db, application, "NORMALIZED")
    transition(db, application, "REJECTED" if job.eligibility == "rejected" else "MATCHED",
               "; ".join(result["reasons"]) if job.eligibility != "eligible" or not profile.resume_text.strip() else None)
    if duplicate:
        application.attention = f"Potential duplicate of {duplicate.id}; review before applying"
    audit(db, "job_added_manually", {"job_id": job.id}, application)
    db.commit()
    return {**serialize(job), "application": serialize(application)}


@router.post("/jobs/{job_id}/action")
def job_action(job_id: str, body: JobAction, db: Session = Depends(session)) -> dict[str, Any]:
    if body.action in {"apply", "retry"}:
        from .automation import detail, request_application
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        try:
            application = request_application(db, job)
            db.commit()
            return detail(db, application)
        except ValueError as exc:
            db.rollback()
            raise HTTPException(409, str(exc)) from exc
    job = db.get(Job, job_id)
    a = db.scalar(select(Application).where(Application.job_id == job_id).with_for_update())
    if not job or not a:
        raise HTTPException(404, "Job not found")
    try:
        if body.action == "rescore":
            rescore(db, job, a)
        elif body.action in {"confirm_eligible", "clear_duplicate"}:
            if len(body.reason.strip()) < 10:
                raise ValueError("Record the factual evidence for this override")
            if a.state not in {"MATCHED", "REJECTED"}:
                raise ValueError("Cannot override an attempted application")
            if body.action == "confirm_eligible":
                job.eligibility = "eligible"
                if a.state == "REJECTED":
                    transition(db, a, "NORMALIZED")
                    transition(db, a, "MATCHED")
                a.attention = None
            else:
                job.duplicate_of = None
        elif body.action == "skip":
            transition(db, a, "REJECTED", body.reason or "Skipped by candidate")
        else:
            control = db.get(Control, 1)
            if not control or not control.enabled:
                raise ValueError("Автоматизация на паузе. Включите агента в настройках, чтобы отправить отклик.")
            if body.action == "retry" and a.state == "FAILED" and not a.submit_intent_at:
                candidate = db.get(Candidate, 1)
                if candidate is None:
                    raise ValueError("Профиль кандидата отсутствует")
                result = evaluate(job_input(job), Profile.model_validate(candidate.facts))
                job.score, job.eligibility, job.explanation = result["score"], result["eligibility"], result
                a.resume_id = None
                audit(db, "retry_revalidated", {"active_resume_id": candidate.facts.get("active_resume_id")}, a)
            reason = readiness(db, job, a)
            if reason:
                raise ValueError(reason)
            transition(db, a, "READY")
        audit(db, "manual_action", body.model_dump(), a)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    return serialize(a)


@router.get("/applications")
def applications(db: Session = Depends(session)) -> list[dict[str, Any]]:
    return [
        {
            "id": row["id"], "job_id": row["job_id"], "state": row["state"],
            "attention": row["attention"], "updated_at": row["updated_at"],
            "company": row["company"], "title": row["title"],
        }
        for row in db.execute(
            select(
                Application.id.label("id"), Application.job_id.label("job_id"),
                Application.state.label("state"), Application.attention.label("attention"),
                Application.updated_at.label("updated_at"),
                Job.company.label("company"), Job.title.label("title"),
            ).join(Job).where(
                Job.score >= MINIMUM_QUALIFICATION_SCORE
            ).order_by(Application.updated_at.desc()).limit(1000)
        ).mappings()
    ]


class Outcome(BaseModel):
    state: Literal["SUBMITTED", "INTERVIEW", "REJECTED_BY_EMPLOYER", "OFFER"]
    evidence: str = Field(min_length=10, max_length=4000)


@router.post("/applications/{application_id}/outcome")
def outcome(application_id: str, body: Outcome, db: Session = Depends(session)) -> dict[str, Any]:
    a = db.scalar(select(Application).where(Application.id == application_id).with_for_update())
    if not a:
        raise HTTPException(404, "Application not found")
    try:
        transition(db, a, body.state)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if body.state == "SUBMITTED":
        a.submitted_at = now()
    audit(db, "outcome_recorded", {"evidence": body.evidence, "state": body.state}, a)
    db.add(Outbox(message=f"🎯 Application {a.id}: {body.state}"))
    db.commit()
    return serialize(a)


