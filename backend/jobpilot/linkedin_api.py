from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import session
from .http import authenticate, serialize
from .models import ReferralLead

router = APIRouter(dependencies=[Depends(authenticate)])


class ReferralLeadInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    company: str = Field(min_length=1, max_length=200)
    location: str = Field(default="", max_length=200)
    profile_url: str = Field(min_length=1, max_length=1000)
    notes: str = Field(default="", max_length=2000)


@router.get("/referral-leads")
def referral_leads(db: Session = Depends(session)) -> list[dict[str, Any]]:
    return [serialize(lead) for lead in db.scalars(select(ReferralLead).order_by(ReferralLead.company, ReferralLead.name))]


@router.post("/referral-leads")
def save_referral_lead(body: ReferralLeadInput, db: Session = Depends(session)) -> dict[str, Any]:
    parsed = urlsplit(body.profile_url.strip())
    if parsed.scheme != "https" or parsed.hostname not in {"linkedin.com", "www.linkedin.com"} or not parsed.path.startswith("/in/"):
        raise HTTPException(422, "Укажите ссылку LinkedIn на профиль вида https://www.linkedin.com/in/…")
    if db.scalar(select(ReferralLead.id).where(ReferralLead.profile_url == body.profile_url.strip())):
        raise HTTPException(409, "Этот профиль уже сохранён")
    lead = ReferralLead(**body.model_dump(exclude={"profile_url"}), profile_url=body.profile_url.strip())
    db.add(lead)
    db.commit()
    return serialize(lead)


@router.delete("/referral-leads/{lead_id}")
def delete_referral_lead(lead_id: str, db: Session = Depends(session)) -> dict[str, bool]:
    lead = db.get(ReferralLead, lead_id)
    if not lead:
        raise HTTPException(404, "Контакт не найден")
    db.delete(lead)
    db.commit()
    return {"deleted": True}


@router.get("/linkedin-search")
def linkedin_search(location: str, keywords: str = "") -> dict[str, str]:
    location, keywords = location.strip(), keywords.strip()
    if not location or len(location) > 100 or len(keywords) > 200:
        raise HTTPException(422, "Укажите страну/город и проверьте длину запроса")
    query = " ".join(part for part in (keywords, location) if part)
    return {"url": "https://www.linkedin.com/search/results/people/?" + urlencode({"keywords": query, "origin": "GLOBAL_SEARCH_HEADER"})}
