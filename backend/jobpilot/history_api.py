from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .db import session
from .http import authenticate, serialize
from .models import Answer, Application, Audit, Control, Job, Outbox, now
from .services import MINIMUM_QUALIFICATION_SCORE, audit

router = APIRouter(dependencies=[Depends(authenticate)])


class AnswerInput(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(min_length=1, max_length=10000)


@router.get("/answers")
def answers(db: Session = Depends(session)) -> list[dict[str, Any]]:
    return [serialize(a) for a in db.scalars(select(Answer))]


@router.post("/answers")
def save_answer(body: AnswerInput, db: Session = Depends(session)) -> dict[str, Any]:
    answer = db.scalar(select(Answer).where(Answer.question == body.question))
    if not answer:
        answer = Answer(**body.model_dump())
        db.add(answer)
    else:
        answer.answer = body.answer
    audit(db, "answer_approved", {"question": body.question})
    db.commit()
    return serialize(answer)


class ControlInput(BaseModel):
    enabled: bool
    auto_apply: bool = False
    min_score: int = Field(ge=0, le=100)
    daily_limit: int = Field(ge=1, le=100)


@router.get("/settings")
def get_settings(db: Session = Depends(session)) -> dict[str, Any]:
    c = db.get(Control, 1)
    assert c is not None
    return {
        **serialize(c),
        "telegram_configured": bool(
            settings().telegram_bot_token.get_secret_value() and settings().telegram_chat_id
        ),
        "llm_configured": bool(settings().llm_api_key.get_secret_value() and settings().llm_model),
    }


@router.put("/settings")
def put_settings(body: ControlInput, db: Session = Depends(session)) -> dict[str, Any]:
    c = db.get(Control, 1)
    assert c is not None
    for key, value in body.model_dump().items():
        setattr(c, key, value)
    audit(db, "automation_control", body.model_dump())
    db.commit()
    return serialize(c)


@router.get("/logs")
def logs(db: Session = Depends(session)) -> list[dict[str, Any]]:
    return [serialize(e) for e in db.scalars(select(Audit).order_by(Audit.id.desc()).limit(300))]


@router.get("/artifacts/{application_id}/{attempt_id}/{filename}")
def artifact(
    application_id: str, attempt_id: str, filename: str, db: Session = Depends(session)
) -> FileResponse:
    import uuid

    try:
        uuid.UUID(application_id)
        uuid.UUID(attempt_id)
    except ValueError as exc:
        raise HTTPException(404) from exc
    if filename not in {"before.png", "after.png", "error.png"} or not db.get(Application, application_id):
        raise HTTPException(404)
    path = settings().data_dir / "artifacts" / application_id / attempt_id / filename
    if not path.is_file():
        raise HTTPException(404, "Artifact not available")
    return FileResponse(path, media_type="image/png")


@router.get("/analytics")
def analytics(db: Session = Depends(session)) -> dict[str, Any]:
    rows = db.execute(
        select(
            Job.provider.label("provider"), Job.company.label("company"), Job.title.label("title"),
            Job.location.label("location"), Job.salary.label("salary"), Job.score.label("score"),
            Job.eligibility.label("eligibility"), Job.discovered_at.label("discovered_at"),
            Job.explanation.label("explanation"), Application.resume_id.label("resume_id"),
            Application.submitted_at.label("submitted_at"), Application.state.label("state"),
            Application.attention.label("attention"),
        ).join(Application).where(Job.score >= MINIMUM_QUALIFICATION_SCORE)
    ).all()
    today = now().date()
    dimension_names = ("source", "company", "role", "cv", "country", "salary", "stack")
    dimensions: dict[str, dict[str, dict[str, int]]] = {name: {} for name in dimension_names}
    stages = {name: 0 for name in ("Discovered", "Eligible", "Matched", "Applied", "Responses", "Interviews", "Offers")}
    total = applied = responses = strong_matches = attention = new_today = 0
    for row in rows:
        total += 1
        is_applied = row.submitted_at is not None
        is_response = row.state in {"INTERVIEW", "OFFER", "REJECTED_BY_EMPLOYER"}
        is_interview = row.state == "INTERVIEW"
        is_offer = row.state == "OFFER"
        applied += is_applied
        responses += is_response
        attention += bool(row.attention)
        new_today += row.discovered_at.date() == today
        strong_matches += row.score >= 80 and row.eligibility == "eligible"
        stages["Discovered"] += 1
        stages["Eligible"] += row.eligibility == "eligible"
        stages["Matched"] += row.score >= 70 and row.eligibility == "eligible"
        stages["Applied"] += is_applied
        stages["Responses"] += is_response
        stages["Interviews"] += is_interview
        stages["Offers"] += is_offer
        keys = (
            row.provider, row.company, row.title, row.resume_id or "Not selected", row.location,
            row.salary or "Not disclosed",
            ", ".join(row.explanation.get("matched_skills", [])[:4]) or "Unknown",
        )
        for dimension, key in zip(dimension_names, keys):
            group = dimensions.setdefault(dimension, {}).setdefault(
                key, {"discovered": 0, "applied": 0, "responses": 0}
            )
            group["discovered"] += 1
            group["applied"] += is_applied
            group["responses"] += is_response
    return {
        "new_today": new_today,
        "strong_matches": strong_matches,
        "submitted": applied,
        "attention": attention,
        "interviews": stages["Interviews"],
        "response_rate": round(100 * responses / applied, 1) if applied else 0,
        "funnel": stages,
        "dimensions": dimensions,
        "dead_letters": db.scalar(select(func.count()).select_from(Outbox).where(Outbox.status == "dead")),
    }


