from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import session
from .domain import Profile
from .http import authenticate
from .models import Candidate
from .resumes import PROVENANCE_FIELDS, rescore_pending
from .services import audit

router = APIRouter(dependencies=[Depends(authenticate)])


@router.get("/profile")
def profile(db: Session = Depends(session)) -> dict[str, Any]:
    candidate = db.get(Candidate, 1)
    return {**Profile().model_dump(), **(candidate.facts if candidate else {})}


@router.put("/profile")
def update_profile(body: Profile, db: Session = Depends(session)) -> dict[str, Any]:
    candidate = db.scalar(select(Candidate).where(Candidate.id == 1).with_for_update())
    if candidate is None:
        raise HTTPException(409, "Профиль кандидата отсутствует")
    # Partial saves and round trips from older clients must not erase provenance.
    updates = body.model_dump(exclude_unset=True, exclude=PROVENANCE_FIELDS)
    candidate.facts = {**candidate.facts, **updates}
    candidate.version += 1
    count = rescore_pending(db)
    audit(db, "profile_updated", {"version": candidate.version, "jobs_rescored": count})
    db.commit()
    return {**Profile().model_dump(), **candidate.facts}


